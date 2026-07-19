package com.mem2life.companion.net

import java.io.File
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * [VideoChunkUploadQueueTest]는 의도적으로 실제 워커 루프를 기동시키지 않는다
 * (그 파일 자체의 주석 참고) — 그래서 워커 재기동 로직의 lost-wakeup 레이스를
 * 잡을 수 없었다. 이 테스트는 실제 [VideoChunkUploadQueue.enqueue]/워커 코루틴을
 * 진짜 스레드풀 위에서 동시에 돌려 그 레이스를 재현하려 시도한다.
 *
 * 정직하게 밝혀둘 한계: 이건 진짜 스레드 인터리빙에 의존하는 lost-wakeup
 * 레이스라 프로덕션 코드에 훅을 심지 않는 한 100% 결정론적으로 재현할 수는
 * 없다. 대신 (a) 실제 멀티스레드 디스패처 위에서, (b) 업로드가 즉시 성공해
 * 워커가 "큐가 비었다"로 전이하는 빈도를 최대화하는 가짜 업로더로, (c) 드레인과
 * 동시에 매우 촘촘하게(수백 회) enqueue를 반복해 그 레이스 창을 최대한 많이
 * 두드리는 스트레스 테스트다. 수정 전(재시작형 워커) 코드로 이 테스트를 여러 번
 * 돌리면 산발적으로 "업로드된 청크 수 < 전체 청크 수"로 타임아웃 실패하는 것을
 * 확인했다 — 수정 후(Channel 기반 상시 워커)에서는 재시작 판단 자체가 없으므로
 * 이 창이 구조적으로 존재하지 않아 결정론적으로 통과해야 한다.
 */
class VideoChunkUploadQueueWorkerRaceTest {

    @get:Rule val tempFolder = TemporaryFolder()

    private val executor = Executors.newFixedThreadPool(4)
    private val dispatcher = executor.asCoroutineDispatcher()

    @After
    fun tearDown() {
        executor.shutdownNow()
    }

    /** 네트워크 없이 즉시 성공하는 가짜 업로더 — 워커가 최대한 자주 "큐 빔"으로
     * 전이하도록(=레이스 창을 자주 열도록) 인위적인 지연을 전혀 넣지 않는다. */
    private class InstantSuccessUploader : VideoChunkUploader {
        val uploadedSeqs = CopyOnWriteArrayList<Int>()

        override suspend fun uploadVideoChunk(
            sessionId: String,
            chunkFile: File,
            seq: Int,
            startTsSec: Double,
            durationSec: Double,
        ): Result<Unit> {
            uploadedSeqs.add(seq)
            return Result.success(Unit)
        }
    }

    @Test
    fun `드레인과 동시에 촘촘하게 enqueue해도 모든 청크가 유실 없이 업로드된다`() {
        val uploader = InstantSuccessUploader()
        val scope = CoroutineScope(Job() + dispatcher)
        val queue =
            VideoChunkUploadQueue(
                queueDir = tempFolder.newFolder("queue"),
                apiClient = uploader,
                sessionId = "race-session",
                scope = scope,
            )
        queue.start()

        val chunkCount = 500
        val seqCounter = AtomicInteger(0)
        val enqueueDone = CountDownLatch(1)

        // VideoChunkEncoder의 드레인 스레드를 흉내낸다: 별도의 진짜 OS 스레드에서
        // 워커 코루틴과 동시에 최대한 촘촘하게 enqueue를 반복한다. 이게 정확히
        // VideoChunkUploadQueue의 클래스 KDoc이 설명하는 위험한 인터리빙이다
        // (인코더 드레인 스레드의 enqueue vs. 워커 코루틴의 "큐 빔" 판단이 겹치는 경우).
        val producer =
            Thread {
                repeat(chunkCount) {
                    val seq = seqCounter.getAndIncrement()
                    val file = tempFolder.newFile("chunk_$seq.mp4")
                    file.writeBytes(byteArrayOf(0))
                    queue.enqueue(file, seq, seq * 30.0, 30.0)
                }
                enqueueDone.countDown()
            }
        producer.start()
        assertTrue(
            "enqueue 스레드가 제한 시간 안에 끝나지 않았다",
            enqueueDone.await(10, TimeUnit.SECONDS),
        )

        runBlocking {
            withTimeoutOrNull(15_000) {
                while (uploader.uploadedSeqs.size < chunkCount) {
                    delay(20)
                }
            }
        }

        assertEquals(
            "일부 청크가 업로드되지 않고 유실됐다(lost-wakeup 재발 의심) — " +
                "업로드됨: ${uploader.uploadedSeqs.size}/$chunkCount",
            chunkCount,
            uploader.uploadedSeqs.size,
        )
        assertEquals(
            "seq 순서대로(0..chunkCount-1) 모두 업로드돼야 한다",
            (0 until chunkCount).toList(),
            uploader.uploadedSeqs.sorted(),
        )

        queue.stop()
        scope.cancel()
    }
}
