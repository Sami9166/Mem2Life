package com.mem2life.companion

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.mem2life.companion.config.BackendConfigStore
import com.mem2life.companion.mock.MockDeviceKitController
import com.mem2life.companion.recording.RecordingForegroundService
import com.mem2life.companion.recording.RecordingSessionController
import com.mem2life.companion.recording.RecordingState
import com.mem2life.companion.wearables.WearablesGlassesController

/**
 * 1단계 데모용 단일 화면 UI. 푸시투톡 질의/TTS 재생은 이 화면의 범위가 아니다
 * (recall-dev 이후 작업) — 여기서는 등록/녹화 시작·정지/업로드 상태/Mock Device Kit
 * 디버그 패널만 다룬다.
 */
class MainActivity : ComponentActivity() {

    private val wearablesController = WearablesGlassesController()

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestRuntimePermissionsIfNeeded()

        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    Mem2LifeScreen(activity = this, wearablesController = wearablesController)
                }
            }
        }
    }

    private fun requestRuntimePermissionsIfNeeded() {
        val needed = mutableListOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            needed += Manifest.permission.BLUETOOTH_CONNECT
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            needed += Manifest.permission.POST_NOTIFICATIONS
        }
        val missing =
            needed.filter {
                ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
            }
        if (missing.isNotEmpty()) {
            permissionLauncher.launch(missing.toTypedArray())
        }
    }
}

@Composable
private fun Mem2LifeScreen(activity: ComponentActivity, wearablesController: WearablesGlassesController) {
    val context = activity

    val backendConfigStore = remember { BackendConfigStore(context) }
    val mockDeviceKitController = remember { MockDeviceKitController(context) }

    var useMockAudio by rememberSaveable { mutableStateOf(true) }
    val recordingController =
        remember(useMockAudio) {
            RecordingSessionController(context, wearablesController, backendConfigStore, useMockAudio)
        }

    val registrationState by wearablesController.registrationState.collectAsState()
    val recordingState by recordingController.state.collectAsState()
    val statusSnapshot by recordingController.statusSnapshot.collectAsState()

    // 목업 오디오 체크박스는 녹화가 진행 중이 아닐 때만 바꿀 수 있다. 녹화 중에
    // 바꾸면 remember(useMockAudio)가 새 RecordingSessionController를 만들어
    // 옛 컨트롤러(살아있는 코루틴 스코프·업로드 큐·오디오 소켓)가 UI에서 끊긴 채
    // 백그라운드에 남고, "녹화 종료" 버튼은 새 컨트롤러(Idle 상태)에 no-op으로
    // 호출돼 실제 세션을 멈출 방법이 없어진다. 체크박스를 잠가 그 상태 자체를
    // 만들지 않는다.
    val canToggleMockAudio =
        when (recordingState) {
            is RecordingState.Idle, is RecordingState.Stopped, is RecordingState.Error -> true
            else -> false
        }

    var config by remember { mutableStateOf(backendConfigStore.load()) }
    var hostText by remember { mutableStateOf(config.host) }
    var portText by remember { mutableStateOf(config.port.toString()) }

    val videoPicker =
        rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            uri?.let { mockDeviceKitController.setCameraFeed(it) }
        }

    LaunchedEffect(recordingState) {
        when (recordingState) {
            is RecordingState.Recording -> RecordingForegroundService.start(context)
            is RecordingState.Stopped, is RecordingState.Error, RecordingState.Idle ->
                RecordingForegroundService.stop(context)
            else -> Unit
        }
    }

    Scaffold { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Text("Mem2Life 컴패니언", style = MaterialTheme.typography.headlineSmall)
                Text("글래스 등록 상태: $registrationState")
                Button(onClick = { wearablesController.startRegistration(activity) }) {
                    Text("글래스 연결(Meta AI 앱 등록)")
                }
            }

            item { HorizontalDivider() }

            item {
                Text("백엔드 설정", style = MaterialTheme.typography.titleMedium)
                OutlinedTextField(
                    value = hostText,
                    onValueChange = { hostText = it },
                    label = { Text("Host (예: 10.0.2.2 = 에뮬레이터에서 본 노트북 localhost)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = portText,
                    onValueChange = { portText = it },
                    label = { Text("Port") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(onClick = {
                    val newConfig =
                        config.copy(host = hostText, port = portText.toIntOrNull() ?: config.port)
                    backendConfigStore.save(newConfig)
                    config = newConfig
                }) {
                    Text("저장")
                }
            }

            item { HorizontalDivider() }

            item {
                Text("녹화", style = MaterialTheme.typography.titleMedium)
                Row {
                    Checkbox(
                        checked = useMockAudio,
                        onCheckedChange = { useMockAudio = it },
                        enabled = canToggleMockAudio,
                    )
                    Text(
                        "목업 오디오 소스 사용 (실기기 HFP 마이크 없을 때, 기본 켜짐)" +
                            if (!canToggleMockAudio) " — 녹화 중에는 변경 불가" else "",
                    )
                }
                Text("상태: $recordingState")
                Text(
                    "영상 청크 — 대기 중: ${statusSnapshot.pendingVideoChunks}, " +
                        "업로드됨: ${statusSnapshot.uploadedVideoChunks}" +
                        (statusSnapshot.lastUploadError?.let { ", 최근 오류: $it" } ?: ""),
                )
                Text(
                    "오디오 WebSocket: ${statusSnapshot.audioSocketState} " +
                        "(재연결로 인한 유실 구간 ${statusSnapshot.audioReconnectDrops}회)",
                )
                Row {
                    Button(onClick = { recordingController.startRecording() }) { Text("녹화 시작") }
                    Button(onClick = { recordingController.stopRecording() }) { Text("녹화 종료") }
                }
            }

            item { HorizontalDivider() }

            item {
                Text("Mock Device Kit (디버그)", style = MaterialTheme.typography.titleMedium)
                val mockState by mockDeviceKitController.uiState.collectAsState()
                Text("$mockState")
                Row {
                    Button(onClick = { mockDeviceKitController.enable() }) { Text("Enable") }
                    Button(onClick = { mockDeviceKitController.disable() }) { Text("Disable") }
                }
                Row {
                    Button(onClick = { mockDeviceKitController.pairRayBanMeta() }) { Text("Pair RayBan Meta") }
                }
                Row {
                    Button(onClick = { mockDeviceKitController.powerOn() }) { Text("Power On") }
                    Button(onClick = { mockDeviceKitController.unfold() }) { Text("Unfold") }
                    Button(onClick = { mockDeviceKitController.don() }) { Text("Don") }
                }
                Button(onClick = { videoPicker.launch(arrayOf("video/*")) }) {
                    Text("목업 카메라 피드 영상 선택 (h.264/h.265)")
                }
            }
        }
    }
}
