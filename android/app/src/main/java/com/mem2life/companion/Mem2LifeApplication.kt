package com.mem2life.companion

import android.app.Application
import com.mem2life.companion.camera.BladeCameraController
import com.mem2life.companion.config.BackendConfigStore
import com.mem2life.companion.recording.RecordingSessionController
import com.mem2life.companion.recording.RecordingState

/**
 * Vuzix Blade 2 온글래스 앱의 Application.
 *
 * 표준 Android API만 사용하므로 별도 SDK 초기화가 없다 — 과거 Meta DAT SDK의
 * Wearables.initialize() 호출은 Blade 2 전환으로 제거됐다.
 *
 * 대신 **녹화 컨트롤러와 카메라 컨트롤러의 소유자** 역할을 한다. 둘 다 Activity가
 * 아니라 프로세스 수명에 묶여야 하기 때문이다 — 아래 [recordingController] 참고.
 * 여기 보관하는 객체들은 Activity가 아닌 Application 컨텍스트만 참조하므로
 * Activity 누수가 생기지 않는다.
 */
class Mem2LifeApplication : Application() {

    /** 녹화 컨트롤러가 붙잡고 있으므로 카메라 컨트롤러도 같은 수명이어야 한다. */
    val cameraController: BladeCameraController by lazy { BladeCameraController(this) }

    private var controller: RecordingSessionController? = null
    private var controllerUsesMockAudio: Boolean? = null

    /**
     * 녹화 컨트롤러를 프로세스 수명으로 유지한다.
     *
     * Activity 안에서 `remember`로 만들면, 녹화 중 뒤로가기로 화면을 빠져나갔다가
     * 런처로 다시 들어왔을 때 새 Activity가 **Idle 상태의 새 컨트롤러**를 만든다.
     * 그러면 [RecordingSessionController.startRecording]의 "이미 녹화 중" 가드가
     * 무력화되어 두 번째 세션이 시작되고, 두 세션이 온보드 카메라를 두고 충돌해
     * 먼저 돌던 녹화가 `카메라 연결 끊김(onDisconnected)`으로 죽는다
     * (실기기에서 재현·확인 — 첫 세션이 부분 청크만 남기고 종료됐다).
     *
     * 같은 인스턴스를 돌려주면 재진입한 화면이 진행 중인 녹화 상태를 그대로 이어받아
     * `Recording(sessionId=...)`을 표시하고, 가드도 정상 동작한다.
     */
    fun recordingController(useMockAudio: Boolean): RecordingSessionController {
        val existing = controller
        if (existing != null && (controllerUsesMockAudio == useMockAudio || existing.isBusy())) {
            // 오디오 소스가 달라도 녹화가 진행 중이면 교체하지 않는다 — 교체하는 순간
            // 살아있는 세션을 정지시킬 방법이 사라진다. (UI도 녹화 중에는 체크박스를
            // 잠가 이 경로로 들어오지 않게 막고 있다.)
            return existing
        }
        val created =
            RecordingSessionController(
                context = this,
                cameraController = cameraController,
                backendConfigStore = BackendConfigStore(this),
                useMockAudioSource = useMockAudio,
            )
        controller = created
        controllerUsesMockAudio = useMockAudio
        return created
    }

    private fun RecordingSessionController.isBusy(): Boolean =
        when (state.value) {
            is RecordingState.Starting, is RecordingState.Recording, is RecordingState.Stopping -> true
            else -> false
        }
}
