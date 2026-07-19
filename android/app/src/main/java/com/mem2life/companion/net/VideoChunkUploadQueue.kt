package com.mem2life.companion.net

import android.util.Log
import java.io.File
import java.util.concurrent.ConcurrentLinkedDeque
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min
import kotlin.math.pow
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val TAG = "Mem2Life:UploadQueue"

data class PendingVideoChunk(
    val file: File,
    val seq: Int,
    val startTsSec: Double,
    val durationSec: Double,
)

data class UploadQueueStatus(
    val pendingCount: Int = 0,
    val isUploading: Boolean = false,
    val lastError: String? = null,
    val uploadedCount: Int = 0,
)

/**
 * 30초 영상 청크의 디스크 기반 로컬 큐. 네트워크가 끊기면 청크 파일을 [queueDir]에
 * 남겨두고, 재연결 시 seq 오름차순으로 순서대로 재전송한다(exponential backoff).
 *
 * CLAUDE.md 원칙: "네트워크 끊김: video-chunks는 android가 로컬 큐잉 후 seq 순서로
 * 재전송(exponential backoff)". 오디오(WS)와 달리 영상 청크는 유실을 허용하지 않는다
 * — 세션이 끝나기 전까지는 디스크에 남아있는 한 계속 재시도한다.
 *
 * 파일명 규약: "{seq 6자리}_{startTsMillis}_{durationMillis}.mp4" — 프로세스가
 * 재시작되어도 [loadPendingFromDisk]가 이 규약으로 큐를 복원한다.
 *
 * 워커 동시성 설계 (2026-07-19 재작성): 이전 버전은 큐가 비면 워커 코루틴이
 * `runWorkerLoop()`에서 완전히 return하고, `enqueue()`가 `workerJob?.isActive`를
 * 보고 필요하면 새 워커를 재기동하는 방식이었다. 그 사이에는 "워커가 큐를 비었다고
 * 판단해 종료를 시작하는 시점"과 "enqueue가 그 워커를 아직 살아있다고 보고 재기동을
 * 생략하는 시점"이 겹치는 lost-wakeup 레이스가 있었다 — 두 시점이 겹치면 새로
 * 들어온 청크를 깨울 대상이 없어져, 이후 무관한 enqueue가 우연히 재기동해줄
 * 때까지 그 청크가 큐에 방치된다. 세션 종료 시 마지막 청크가 바로 이 창(직전
 * 청크의 업로드 성공으로 워커가 큐 빔을 확인하는 순간과 겹침)에 가장 걸리기
 * 쉬웠다 — 데모에서 재시도 기회가 없는 최악의 타이밍이다.
 *
 * 지금은 워커가 세션 동안 절대 자연 종료하지 않는다: 큐가 비면 return하는 대신
 * [wakeSignal] 채널의 suspend receive로만 대기한다. "재기동" 판단 자체가 없으므로
 * (워커는 [ensureWorkerStarted]를 통해 세션당 정확히 한 번만 launch되고, 그
 * 시작조차 [workerStarted]의 원자적 compareAndSet으로 보호된다) 위 레이스가
 * 존재할 수 있는 상태 전이 자체가 사라진다.
 */
class VideoChunkUploadQueue(
    private val queueDir: File,
    private val apiClient: VideoChunkUploader,
    private val sessionId: String,
    private val scope: CoroutineScope,
    private val initialBackoffMs: Long = 1_000L,
    private val maxBackoffMs: Long = 30_000L,
) {
    private val _status = MutableStateFlow(UploadQueueStatus())
    val status: StateFlow<UploadQueueStatus> = _status.asStateFlow()

    private var workerJob: Job? = null
    private val workerStarted = AtomicBoolean(false)

    // 큐가 빈 상태에서 워커를 깨우는 신호. CONFLATED 버퍼(용량 1, 최신값만 유지)라
    // 신호를 놓치는 경우가 없다: enqueue()의 trySend가 워커의 receive()보다 먼저
    // 일어나도 버퍼에 남아 다음 receive()가 즉시 받고, receive()가 먼저 대기 중이면
    // 랑데부로 바로 전달된다 — 어느 순서로 인터리빙돼도 "보냈는데 아무도 못 받는"
    // 경우가 없다.
    private val wakeSignal = Channel<Unit>(Channel.CONFLATED)

    // 인코더의 드레인 스레드(enqueue)와 워커 코루틴(runWorkerLoop)이 동시에
    // 접근하므로 스레드 안전한 자료구조를 쓴다.
    private val pending = ConcurrentLinkedDeque<PendingVideoChunk>()

    init {
        queueDir.mkdirs()
    }

    /** 프로세스 재시작 후에도 디스크에 남은 미전송 청크를 큐에 다시 채운다. */
    fun loadPendingFromDisk() {
        val files = queueDir.listFiles { f -> f.isFile && f.name.endsWith(".mp4") } ?: return
        val restored =
            files.mapNotNull { file -> parseFileName(file) }.sortedBy { it.seq }
        pending.clear()
        pending.addAll(restored)
        _status.update { it.copy(pendingCount = pending.size) }
        if (restored.isNotEmpty()) {
            Log.i(TAG, "디스크에서 미전송 청크 ${restored.size}개 복원")
        }
    }

    fun enqueue(sourceFile: File, seq: Int, startTsSec: Double, durationSec: Double) {
        val destName = fileName(seq, startTsSec, durationSec)
        val destFile = File(queueDir, destName)
        if (sourceFile != destFile) {
            sourceFile.copyTo(destFile, overwrite = true)
            sourceFile.delete()
        }
        pending.addLast(PendingVideoChunk(destFile, seq, startTsSec, durationSec))
        _status.update { it.copy(pendingCount = pending.size) }
        ensureWorkerStarted()
        wakeSignal.trySend(Unit)
    }

    fun start() {
        loadPendingFromDisk()
        ensureWorkerStarted()
    }

    fun stop() {
        workerJob?.cancel()
        workerJob = null
        workerStarted.set(false)
    }

    /**
     * 워커 코루틴을 세션당 정확히 한 번만 기동한다. [AtomicBoolean.compareAndSet]은
     * 원자적이므로 `start()`와 `enqueue()`가 서로 다른 스레드에서 동시에 호출돼도
     * launch는 딱 한 번만 이긴다. 한 번 기동된 워커는 세션이 끝날 때까지(`stop()`
     * 또는 scope 취소 전까지) 스스로 종료하지 않으므로, 그 이후로는 "재기동해야
     * 하나"를 판단할 필요 자체가 없다 — 판단이 없으면 그 판단들 사이의 레이스도
     * 없다.
     */
    private fun ensureWorkerStarted() {
        if (workerStarted.compareAndSet(false, true)) {
            workerJob =
                scope.launch {
                    runWorkerLoop()
                }
        }
    }

    private suspend fun runWorkerLoop() {
        while (scope.isActive) {
            val next = pending.peekFirst()
            if (next == null) {
                _status.update { it.copy(isUploading = false) }
                // 큐가 비어도 종료하지 않는다 — enqueue()의 trySend가 깨울 때까지만
                // 대기했다가 peekFirst()부터 다시 확인한다. wakeSignal이 CONFLATED라
                // trySend가 이 receive()보다 먼저 일어나도 유실되지 않는다.
                wakeSignal.receive()
                continue
            }
            _status.update { it.copy(isUploading = true) }
            var attempt = 0
            var uploaded = false
            while (!uploaded && scope.isActive) {
                val result =
                    apiClient.uploadVideoChunk(
                        sessionId = sessionId,
                        chunkFile = next.file,
                        seq = next.seq,
                        startTsSec = next.startTsSec,
                        durationSec = next.durationSec,
                    )
                if (result.isSuccess) {
                    uploaded = true
                } else {
                    val message = result.exceptionOrNull()?.message ?: "알 수 없는 업로드 오류"
                    _status.update { it.copy(lastError = message) }
                    val backoff = backoffFor(attempt)
                    Log.w(TAG, "청크 seq=${next.seq} 업로드 실패, ${backoff}ms 후 재시도 (attempt=$attempt)")
                    delay(backoff)
                    attempt += 1
                }
            }
            if (uploaded) {
                next.file.delete()
                pending.pollFirst()
                _status.update {
                    it.copy(pendingCount = pending.size, uploadedCount = it.uploadedCount + 1)
                }
            }
        }
    }

    /** 지수 백오프: initialBackoffMs * 2^attempt, maxBackoffMs로 상한. */
    internal fun backoffFor(attempt: Int): Long {
        val scaled = initialBackoffMs * 2.0.pow(attempt.coerceAtMost(10))
        return min(scaled, maxBackoffMs.toDouble()).toLong()
    }

    internal fun fileName(seq: Int, startTsSec: Double, durationSec: Double): String {
        val seqPart = seq.toString().padStart(6, '0')
        val startMs = (startTsSec * 1000).toLong()
        val durMs = (durationSec * 1000).toLong()
        return "${seqPart}_${startMs}_${durMs}.mp4"
    }

    internal fun parseFileName(file: File): PendingVideoChunk? {
        val name = file.nameWithoutExtension
        val parts = name.split("_")
        if (parts.size != 3) return null
        val seq = parts[0].toIntOrNull() ?: return null
        val startMs = parts[1].toLongOrNull() ?: return null
        val durMs = parts[2].toLongOrNull() ?: return null
        return PendingVideoChunk(file, seq, startMs / 1000.0, durMs / 1000.0)
    }
}
