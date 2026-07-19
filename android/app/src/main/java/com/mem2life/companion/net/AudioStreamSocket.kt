package com.mem2life.companion.net

import android.util.Log
import com.mem2life.companion.config.BackendConfig
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString

private const val TAG = "Mem2Life:AudioSocket"

enum class AudioSocketState { DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING }

/**
 * WS /sessions/{session_id}/audio-stream 클라이언트.
 *
 * 계약(CLAUDE.md): 바이너리 프레임 = PCM 16-bit/16kHz/mono, 전송 순서 그대로,
 * 재조립 번호 없음. 연결이 끊기면 재연결만 하고 그 사이 유실된 프레임은 포기한다
 * (영상 청크와 달리 재전송하지 않음 — 실시간 스트림 특성상 재전송이 의미 없음).
 */
class AudioStreamSocket(
    private val configProvider: () -> BackendConfig,
    private val scope: CoroutineScope,
    private val initialBackoffMs: Long = 500L,
    private val maxBackoffMs: Long = 10_000L,
) {
    private val client =
        OkHttpClient.Builder()
            .readTimeout(0, TimeUnit.MILLISECONDS) // 스트리밍 연결은 read timeout 없음
            .build()

    private val _state = MutableStateFlow(AudioSocketState.DISCONNECTED)
    val state: StateFlow<AudioSocketState> = _state.asStateFlow()

    /** 재연결 사이 유실된 오디오량을 데모/디버깅에서 확인하기 위한 카운터(재전송하지 않음). */
    private val _droppedReconnectCount = MutableStateFlow(0)
    val droppedReconnectCount: StateFlow<Int> = _droppedReconnectCount.asStateFlow()

    private var webSocket: WebSocket? = null
    private var connectionJob: Job? = null
    private var sessionId: String? = null
    private var stopped = false

    fun connect(sessionId: String) {
        this.sessionId = sessionId
        stopped = false
        connectionJob?.cancel()
        connectionJob =
            scope.launch {
                var attempt = 0
                while (isActive && !stopped) {
                    _state.value = if (attempt == 0) AudioSocketState.CONNECTING else AudioSocketState.RECONNECTING
                    val connected = openSocketAndAwaitClose(sessionId)
                    if (stopped) return@launch
                    if (!connected) {
                        _droppedReconnectCount.update { it + 1 }
                    }
                    val backoff = backoffFor(attempt)
                    Log.w(TAG, "오디오 WebSocket 끊김, ${backoff}ms 후 재연결 시도 (유실 구간 포기)")
                    delay(backoff)
                    attempt = (attempt + 1).coerceAtMost(10)
                }
            }
    }

    /** 연결을 열고, 연결이 끊길 때까지 suspend한다. 정상적으로 한 번이라도 열렸으면 true. */
    private suspend fun openSocketAndAwaitClose(sessionId: String): Boolean {
        val config = configProvider()
        val request = Request.Builder().url(config.audioStreamWsUrl(sessionId)).build()
        var everConnected = false
        val closedSignal = kotlinx.coroutines.CompletableDeferred<Unit>()

        val listener =
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    everConnected = true
                    _state.value = AudioSocketState.CONNECTED
                    Log.i(TAG, "오디오 WebSocket 연결됨 (session=$sessionId)")
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    Log.w(TAG, "오디오 WebSocket 실패: ${t.message}")
                    closedSignal.complete(Unit)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    Log.i(TAG, "오디오 WebSocket 닫힘: code=$code reason=$reason")
                    closedSignal.complete(Unit)
                }
            }

        webSocket = client.newWebSocket(request, listener)
        closedSignal.await()
        webSocket = null
        return everConnected
    }

    /**
     * PCM 16-bit/16kHz/mono 프레임 하나를 전송한다. 연결이 없으면 조용히 드롭한다
     * (오디오는 재전송하지 않는다는 계약에 따른 의도된 동작).
     */
    fun sendPcmFrame(frame: ByteArray): Boolean {
        val socket = webSocket ?: return false
        return socket.send(frame.toByteString(0, frame.size))
    }

    fun disconnect() {
        stopped = true
        connectionJob?.cancel()
        webSocket?.close(1000, "session_end")
        webSocket = null
        _state.value = AudioSocketState.DISCONNECTED
    }

    internal fun backoffFor(attempt: Int): Long {
        val scaled = initialBackoffMs * (1 shl attempt.coerceAtMost(10))
        return scaled.coerceAtMost(maxBackoffMs)
    }
}
