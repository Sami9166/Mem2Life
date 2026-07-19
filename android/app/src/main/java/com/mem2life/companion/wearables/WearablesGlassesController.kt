package com.mem2life.companion.wearables

import android.app.Activity
import android.util.Log
import com.meta.wearable.dat.camera.Stream
import com.meta.wearable.dat.camera.addStream
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.StreamError
import com.meta.wearable.dat.camera.types.VideoFrame
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.RegistrationState
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

private const val TAG = "Mem2Life:Wearables"
private const val SESSION_START_TIMEOUT_MS = 15_000L

/**
 * 글래스 등록/세션/카메라 스트림 생명주기를 감싼 컨트롤러.
 *
 * 공식 CameraAccess 샘플의 WearablesViewModel + StreamViewModel 패턴을 따르되,
 * 이 앱은 미리보기 UI가 필요 없고(녹화만 하면 됨) 백그라운드 녹화 세션 하나만
 * 관리하면 되므로 두 ViewModel을 하나의 컨트롤러로 단순화했다.
 *
 * 실기기가 없는 동안은 Mock Device Kit(별도 MockDeviceKitController)이
 * Wearables.registrationState/devices/session을 그대로 시뮬레이션하므로, 이
 * 컨트롤러의 코드는 실기기/목업 여부와 무관하게 동일하게 동작한다.
 *
 * 주의: DAT SDK의 `DatResult`가 `getOrElse`/`onFailure` 등을 inline으로 제공하는지
 * 문서만으로는 확정할 수 없었다(1단계 시점 기준 SDK 소스에 직접 접근 불가). 그래서
 * 이 컨트롤러는 각 `DatResult`를 `.fold(onSuccess, onFailure)`로
 * 즉시 `kotlin.Result`로 변환한 뒤 다루며, 람다 안에서 바깥 함수로 non-local
 * return을 시도하지 않는다 — inline 여부와 무관하게 항상 컴파일되는 안전한 패턴이다.
 */
class WearablesGlassesController {

    val registrationState: StateFlow<RegistrationState> = Wearables.registrationState

    private val deviceSelector = AutoDeviceSelector()
    private var session: DeviceSession? = null
    private var stream: Stream? = null
    private var stateJob: Job? = null
    private var errorJob: Job? = null
    private var videoJob: Job? = null

    fun startRegistration(activity: Activity) {
        Wearables.startRegistration(activity)
    }

    /**
     * 세션을 만들고 카메라 스트림을 붙인다. 프레임은 [onVideoFrame]으로, 세션이
     * (기기 쪽 사정으로) 예기치 않게 끊기면 [onSessionEnded]로 통지한다.
     *
     * 참고: DAT SDK는 오디오 캡처를 제공하지 않는다 — 오디오는 이 컨트롤러와
     * 완전히 독립적으로 com.mem2life.companion.audio 패키지가 담당한다.
     */
    suspend fun startGlassesSession(
        scope: CoroutineScope,
        streamConfiguration: StreamConfiguration,
        onVideoFrame: (VideoFrame) -> Unit,
        onSessionEnded: (reason: String) -> Unit,
    ): Result<Unit> {
        stopGlassesSession()

        val createdSessionResult: Result<DeviceSession> =
            Wearables.createSession(deviceSelector).fold(
                onSuccess = { s -> Result.success(s) },
                onFailure = { error, _ ->
                    Result.failure(IllegalStateException("세션 생성 실패: ${error.description}"))
                },
            )
        val createdSession =
            createdSessionResult.getOrNull()
                ?: return Result.failure(
                    createdSessionResult.exceptionOrNull() ?: IllegalStateException("세션 생성 실패"),
                )

        session = createdSession
        createdSession.start()

        val readySignal = CompletableDeferred<Result<Unit>>()

        stateJob =
            scope.launch {
                createdSession.state.collect { state ->
                    when (state) {
                        DeviceSessionState.STARTED -> {
                            if (stream == null) {
                                val attachResult =
                                    attachStream(scope, createdSession, streamConfiguration, onVideoFrame)
                                if (!readySignal.isCompleted) readySignal.complete(attachResult)
                            }
                        }
                        DeviceSessionState.STOPPED -> {
                            if (!readySignal.isCompleted) {
                                readySignal.complete(
                                    Result.failure(IllegalStateException("세션이 시작 전 종료됨")),
                                )
                            } else {
                                onSessionEnded("세션 종료(DeviceSessionState.STOPPED)")
                            }
                        }
                        else -> Unit
                    }
                }
            }

        errorJob =
            scope.launch {
                createdSession.errors.collect { error ->
                    Log.e(TAG, "세션 오류: ${error.description}")
                }
            }

        val result =
            withTimeoutOrNull(SESSION_START_TIMEOUT_MS) { readySignal.await() }
                ?: Result.failure(IllegalStateException("세션 시작 타임아웃(글래스 연결/전원/착용 상태 확인)"))

        if (result.isFailure) {
            stopGlassesSession()
        }
        return result
    }

    private suspend fun attachStream(
        scope: CoroutineScope,
        deviceSession: DeviceSession,
        streamConfiguration: StreamConfiguration,
        onVideoFrame: (VideoFrame) -> Unit,
    ): Result<Unit> {
        val addStreamResult: Result<Stream> =
            deviceSession.addStream(streamConfiguration).fold(
                onSuccess = { s -> Result.success(s) },
                onFailure = { error, _ ->
                    Result.failure(IllegalStateException("스트림 추가 실패: ${error.description}"))
                },
            )
        val addedStream =
            addStreamResult.getOrNull()
                ?: return Result.failure(
                    addStreamResult.exceptionOrNull() ?: IllegalStateException("스트림 추가 실패"),
                )
        stream = addedStream

        videoJob =
            scope.launch {
                addedStream.videoStream.collect { frame -> onVideoFrame(frame) }
            }

        scope.launch {
            addedStream.state.collect { state ->
                Log.d(TAG, "스트림 상태: $state")
            }
        }
        scope.launch {
            addedStream.errorStream.collect { error ->
                if (error != StreamError.STREAM_ERROR) {
                    Log.e(TAG, "치명적 스트림 오류: ${error.description}")
                }
            }
        }

        val startResult: Result<Unit> =
            addedStream.start().fold(
                onSuccess = { Result.success(Unit) },
                onFailure = { error, _ ->
                    Result.failure(IllegalStateException("스트림 시작 실패: ${error.description}"))
                },
            )
        return startResult
    }

    fun stopGlassesSession() {
        videoJob?.cancel()
        videoJob = null
        stateJob?.cancel()
        stateJob = null
        errorJob?.cancel()
        errorJob = null
        stream?.stop()
        stream = null
        session?.stop()
        session = null
    }
}
