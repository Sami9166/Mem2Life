package com.mem2life.companion.recording

import android.content.Context
import android.util.Log
import com.mem2life.companion.audio.AudioSource
import com.mem2life.companion.audio.DeviceMicAudioSource
import com.mem2life.companion.audio.MockPcmAudioSource
import com.mem2life.companion.camera.BladeCameraController
import com.mem2life.companion.capture.VideoChunkEncoder
import com.mem2life.companion.config.BackendConfigStore
import com.mem2life.companion.net.AudioSocketState
import com.mem2life.companion.net.AudioStreamSocket
import com.mem2life.companion.net.SessionApiClient
import com.mem2life.companion.net.SessionStartRequest
import com.mem2life.companion.net.VideoChunkUploadQueue
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

private const val TAG = "Mem2Life:RecordingCtrl"

/**
 * Vuzix Blade 2 온보드 카메라 캡처 해상도 — CLAUDE.md의 "720p" 계약에 맞춘
 * 1280x720(landscape). Blade 2 카메라는 최대 1080p까지 YUV 출력을 지원하지만
 * 계약 해상도와 업로드 대역폭을 고려해 720p로 고정한다.
 * (기존 Meta DAT 스트림은 세로 720x1280이었다 — Blade 2는 착용자 시점의
 * 가로 프레임이 자연스럽고 카메라도 landscape 센서다.)
 */
private const val VIDEO_WIDTH_PX = 1280
private const val VIDEO_HEIGHT_PX = 720
private const val VIDEO_FRAME_RATE_FPS = 24
private const val CHUNK_DURATION_SEC = 30.0
private const val QUEUE_DRAIN_GRACE_MS = 10_000L

/**
 * 녹화 세션 전체를 오케스트레이션한다:
 *   backend /sessions/start
 *   -> 오디오 소스(온보드 마이크) 시작
 *   -> 온보드 카메라(Camera2) 캡처 시작
 *   -> 영상 프레임 -> 30초 mp4 인코딩 -> 로컬 큐 -> HTTP 업로드(seq 순서, 재시도)
 *   -> 오디오 프레임 -> WebSocket 스트리밍(재연결, 유실 허용)
 *   -> backend /sessions/{id}/end
 *
 * 푸시투톡 질의 UI/TTS 재생은 이 컨트롤러의 범위가 아니다(recall-dev/이후 작업).
 */
class RecordingSessionController(
    private val context: Context,
    private val cameraController: BladeCameraController,
    private val backendConfigStore: BackendConfigStore,
    private val useMockAudioSource: Boolean,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val apiClient = SessionApiClient { backendConfigStore.load() }

    private var audioSource: AudioSource? = null
    private var audioSocket: AudioStreamSocket? = null
    private var videoChunkEncoder: VideoChunkEncoder? = null
    private var uploadQueue: VideoChunkUploadQueue? = null
    private var currentSessionId: String? = null

    private val _state = MutableStateFlow<RecordingState>(RecordingState.Idle)
    val state: StateFlow<RecordingState> = _state.asStateFlow()

    /**
     * 업로드 큐 상태 + 오디오 소켓 상태를 UI에서 한 번에 볼 수 있게 합친 스냅샷.
     * 녹화가 시작될 때마다 내부적으로 재구독되므로, UI는 이 프로퍼티 하나만 관찰하면 된다.
     */
    private val _statusSnapshot = MutableStateFlow(RecordingStatusSnapshot())
    val statusSnapshot: StateFlow<RecordingStatusSnapshot> = _statusSnapshot.asStateFlow()

    private fun observeStatusSnapshot(queue: VideoChunkUploadQueue, socket: AudioStreamSocket) {
        scope.launch {
            combine(queue.status, socket.state, socket.droppedReconnectCount) { q, s, dropped ->
                RecordingStatusSnapshot(
                    pendingVideoChunks = q.pendingCount,
                    uploadedVideoChunks = q.uploadedCount,
                    lastUploadError = q.lastError,
                    audioSocketState = s,
                    audioReconnectDrops = dropped,
                )
            }.collect { snapshot -> _statusSnapshot.value = snapshot }
        }
    }

    fun startRecording() {
        if (_state.value is RecordingState.Recording || _state.value is RecordingState.Starting) {
            Log.w(TAG, "이미 녹화 중이거나 시작 중 — 무시")
            return
        }
        _state.value = RecordingState.Starting
        scope.launch {
            val startResult = apiClient.startSession(SessionStartRequest())
            val sessionResponse =
                startResult.getOrElse {
                    _state.value = RecordingState.Error("세션 시작 실패(백엔드 연결 확인): ${it.message}")
                    return@launch
                }
            val sessionId = sessionResponse.sessionId
            currentSessionId = sessionId
            Log.i(TAG, "백엔드 세션 시작: $sessionId")

            val queueDir = File(context.filesDir, "pending_chunks/$sessionId")
            val encoderTmpDir = File(context.cacheDir, "recording_tmp/$sessionId")
            val queue = VideoChunkUploadQueue(queueDir, apiClient, sessionId, scope)
            uploadQueue = queue
            queue.start()

            val encoder =
                VideoChunkEncoder(
                    outputDir = encoderTmpDir,
                    width = VIDEO_WIDTH_PX,
                    height = VIDEO_HEIGHT_PX,
                    frameRateFps = VIDEO_FRAME_RATE_FPS,
                    chunkDurationSec = CHUNK_DURATION_SEC,
                    onChunkReady = { chunk ->
                        queue.enqueue(chunk.file, chunk.seq, chunk.startTsSec, chunk.durationSec)
                    },
                )
            videoChunkEncoder = encoder

            val socket = AudioStreamSocket(configProvider = { backendConfigStore.load() }, scope = scope)
            audioSocket = socket
            observeStatusSnapshot(queue, socket)

            val source: AudioSource =
                if (useMockAudioSource) {
                    MockPcmAudioSource(context)
                } else {
                    DeviceMicAudioSource(context)
                }
            audioSource = source

            // Blade 2에서는 카메라/마이크가 같은 기기 안에 있어 과거 Meta 설계의
            // "HFP 라우트를 카메라 스트림보다 먼저 안정화" 같은 순서 제약은 없다.
            // 오디오를 먼저 시작하는 것은 세션 초반 오디오 공백을 줄이기 위함이다.
            val audioStarted =
                source.start(scope) { pcmFrame -> socket.sendPcmFrame(pcmFrame) }
            if (!audioStarted) {
                Log.w(TAG, "오디오 소스 시작 실패 — 영상만으로 녹화를 계속 진행한다(데모 안정성 우선)")
            }
            socket.connect(sessionId)

            encoder.start(scope)
            val cameraResult =
                cameraController.startCameraSession(
                    widthPx = VIDEO_WIDTH_PX,
                    heightPx = VIDEO_HEIGHT_PX,
                    frameRateFps = VIDEO_FRAME_RATE_FPS,
                    onVideoFrame = { i420, ptsUs -> encoder.onVideoFrame(i420, ptsUs) },
                    onSessionEnded = { reason ->
                        Log.w(TAG, "카메라 세션이 예기치 않게 종료됨: $reason")
                        stopRecording()
                    },
                )

            if (cameraResult.isFailure) {
                _state.value =
                    RecordingState.Error(
                        "카메라 시작 실패: ${cameraResult.exceptionOrNull()?.message}",
                    )
                cleanupAfterFailure()
                return@launch
            }

            _state.value = RecordingState.Recording(sessionId)
        }
    }

    fun stopRecording() {
        val sessionId = currentSessionId ?: return
        if (_state.value is RecordingState.Stopping || _state.value is RecordingState.Stopped) return
        _state.value = RecordingState.Stopping
        scope.launch {
            cameraController.stopCameraSession()
            videoChunkEncoder?.stop() // 마지막 짧은 청크도 큐로 넘어감
            audioSource?.stop()
            audioSocket?.disconnect()

            // 네트워크가 복구될 때까지 최대한 기다렸다가(데모 안정성) 세션 종료를 알린다.
            // 다 못 비워도 계속 재시도되며, /end는 그와 무관하게 호출한다(계약: 비동기 트리거).
            withTimeoutOrNull(QUEUE_DRAIN_GRACE_MS) {
                while (uploadQueue?.status?.value?.pendingCount != 0) {
                    delay(500)
                }
            }

            apiClient.endSession(sessionId).onFailure {
                Log.e(TAG, "세션 종료 알림 실패 — 백엔드가 재조회 시 로컬 원본 영상으로 보완 필요", it)
            }

            currentSessionId = null
            _state.value = RecordingState.Stopped
        }
    }

    private fun cleanupAfterFailure() {
        cameraController.stopCameraSession()
        videoChunkEncoder?.stop()
        audioSource?.stop()
        audioSocket?.disconnect()
        uploadQueue?.stop()
        currentSessionId = null
    }
}

data class RecordingStatusSnapshot(
    val pendingVideoChunks: Int = 0,
    val uploadedVideoChunks: Int = 0,
    val lastUploadError: String? = null,
    val audioSocketState: AudioSocketState = AudioSocketState.DISCONNECTED,
    val audioReconnectDrops: Int = 0,
)
