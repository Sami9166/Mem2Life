package com.mem2life.companion.mock

import android.content.Context
import android.net.Uri
import android.util.Log
import com.meta.wearable.dat.mockdevice.MockDeviceKit
import com.meta.wearable.dat.mockdevice.api.GlassesModel
import com.meta.wearable.dat.mockdevice.api.MockGlasses
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

private const val TAG = "Mem2Life:MockDeviceKit"

data class MockGlassesUiState(
    val isEnabled: Boolean = false,
    val isPaired: Boolean = false,
    val isPoweredOn: Boolean = false,
    val isUnfolded: Boolean = false,
    val isDonned: Boolean = false,
    val hasCameraFeed: Boolean = false,
)

/**
 * 실기기 없이 글래스 페어링/전원/착용/카메라 피드를 시뮬레이션하는 디버그용 컨트롤러.
 *
 * 참고: Mock Device Kit은 카메라 스트림/사진 캡처/권한/기기 상태만 시뮬레이션한다.
 * 마이크(HFP) 오디오 입력은 시뮬레이션 대상이 아니다 — 오디오 쪽 개발/검증은
 * com.mem2life.companion.audio.MockPcmAudioSource가 별도로 담당한다 (배경은 루트
 * CLAUDE.md "알려진 리스크 / 검증 대기" 절의 확인됨 항목 참고).
 *
 * 공식 CameraAccess 샘플(MockDeviceKitViewModel)은 pairGlasses/전원·착용 조작/
 * setCameraFeed를 모두 코루틴 안에서 호출한다 — 이 컨트롤러도 동일하게 내부
 * CoroutineScope로 감싼다(해당 API들이 suspend인지 문서만으로 확정할 수 없어
 * 샘플과 같은 안전한 패턴을 따름).
 */
class MockDeviceKitController(context: Context) {
    private val mockDeviceKit = MockDeviceKit.getInstance(context.applicationContext)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    private val _uiState = MutableStateFlow(MockGlassesUiState())
    val uiState: StateFlow<MockGlassesUiState> = _uiState.asStateFlow()

    private var pairedDevice: MockGlasses? = null

    fun enable() {
        mockDeviceKit.enable()
        _uiState.update { it.copy(isEnabled = true) }
    }

    fun disable() {
        mockDeviceKit.disable()
        _uiState.update { MockGlassesUiState() }
        pairedDevice = null
    }

    fun pairRayBanMeta() {
        scope.launch {
            mockDeviceKit
                .pairGlasses(GlassesModel.RAYBAN_META)
                .fold(
                    onSuccess = { device ->
                        pairedDevice = device
                        _uiState.update { it.copy(isPaired = true) }
                        Log.i(TAG, "MockDeviceKit: RayBan Meta 페어링 완료")
                    },
                    onFailure = { error, _ -> Log.e(TAG, "MockDeviceKit 페어링 실패: $error") },
                )
        }
    }

    fun powerOn() = withDevice { device ->
        device.powerOn()
        _uiState.update { it.copy(isPoweredOn = true) }
    }

    fun unfold() = withDevice { device ->
        device.unfold()
        _uiState.update { it.copy(isUnfolded = true) }
    }

    fun don() = withDevice { device ->
        device.don()
        _uiState.update { it.copy(isDonned = true, isUnfolded = true) }
    }

    fun doff() = withDevice { device ->
        device.doff()
        _uiState.update { it.copy(isDonned = false) }
    }

    /** 목업 카메라 피드로 쓸 h.264/h.265 영상 파일을 지정한다(스트리밍 대상 영상). */
    fun setCameraFeed(uri: Uri) = withDevice { device ->
        device.services.camera.setCameraFeed(uri)
        _uiState.update { it.copy(hasCameraFeed = true) }
    }

    private fun withDevice(action: suspend (MockGlasses) -> Unit) {
        val device = pairedDevice
        if (device == null) {
            Log.w(TAG, "페어링된 목업 디바이스가 없음")
            return
        }
        scope.launch {
            try {
                action(device)
            } catch (e: Exception) {
                Log.e(TAG, "MockDeviceKit 조작 실패", e)
            }
        }
    }
}
