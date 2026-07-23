package com.mem2life.companion

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.mem2life.companion.camera.BladeCameraController
import com.mem2life.companion.config.BackendConfigStore
import com.mem2life.companion.recording.RecordingForegroundService
import com.mem2life.companion.recording.RecordingSessionController
import com.mem2life.companion.recording.RecordingState

/**
 * Vuzix Blade 2 온글래스 단일 화면 UI. 푸시투톡 질의/TTS 재생은 이 화면의 범위가
 * 아니다(recall-dev 이후 작업) — 여기서는 백엔드 설정/녹화 시작·정지/업로드 상태만 다룬다.
 *
 * Blade 2 UI 제약:
 *  - 디스플레이 480x480, 웨이브가이드 특성상 검정 = 투명 → 순수 검정 배경의
 *    다크 테마를 쓴다(Vuzix 공식 가이드라인).
 *  - 터치스크린이 없다. 관자놀이 터치패드가 트랙볼/D-pad 이벤트로 들어오므로
 *    모든 조작 요소는 포커스 이동(스와이프) + 탭(클릭)으로 동작해야 한다 —
 *    Compose의 Button/Checkbox/TextField는 기본적으로 포커스 내비게이션을
 *    지원하므로 별도 처리 없이 동작한다.
 */
class MainActivity : ComponentActivity() {

    private val cameraController = BladeCameraController(this)

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestRuntimePermissionsIfNeeded()

        setContent {
            // 웨이브가이드에서 검정이 투명으로 보이므로 배경은 항상 순수 검정.
            MaterialTheme(colorScheme = darkColorScheme(background = Color.Black, surface = Color.Black)) {
                Surface(modifier = Modifier.fillMaxSize(), color = Color.Black) {
                    Mem2LifeScreen(activity = this, cameraController = cameraController)
                }
            }
        }
    }

    private fun requestRuntimePermissionsIfNeeded() {
        val needed = mutableListOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
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
private fun Mem2LifeScreen(activity: ComponentActivity, cameraController: BladeCameraController) {
    val context = activity

    val backendConfigStore = remember { BackendConfigStore(context) }

    // Blade 2 실기기에는 온보드 마이크가 있으므로 기본은 실제 마이크. 마이크가
    // 없는/불안정한 개발 환경에서만 목업 오디오로 전환한다.
    var useMockAudio by rememberSaveable { mutableStateOf(false) }
    val recordingController =
        remember(useMockAudio) {
            RecordingSessionController(context, cameraController, backendConfigStore, useMockAudio)
        }

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

    LaunchedEffect(recordingState) {
        when (recordingState) {
            is RecordingState.Recording -> RecordingForegroundService.start(context)
            is RecordingState.Stopped, is RecordingState.Error, RecordingState.Idle ->
                RecordingForegroundService.stop(context)
            else -> Unit
        }
    }

    Scaffold(containerColor = Color.Black) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item {
                Text("Mem2Life (Vuzix Blade 2)", style = MaterialTheme.typography.titleLarge)
            }

            item { HorizontalDivider() }

            item {
                Text("녹화", style = MaterialTheme.typography.titleMedium)
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
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { recordingController.startRecording() }) { Text("녹화 시작") }
                    Button(onClick = { recordingController.stopRecording() }) { Text("녹화 종료") }
                }
            }

            item { HorizontalDivider() }

            item {
                Text("백엔드 설정", style = MaterialTheme.typography.titleMedium)
                OutlinedTextField(
                    value = hostText,
                    onValueChange = { hostText = it },
                    label = { Text("Host (Blade 2 실기기: 백엔드 PC의 LAN IP)") },
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
                Text("디버그", style = MaterialTheme.typography.titleMedium)
                Row {
                    Checkbox(
                        checked = useMockAudio,
                        onCheckedChange = { useMockAudio = it },
                        enabled = canToggleMockAudio,
                    )
                    Text(
                        "목업 오디오 소스 사용 (마이크 없는 개발 환경용, 기본 꺼짐)" +
                            if (!canToggleMockAudio) " — 녹화 중에는 변경 불가" else "",
                    )
                }
            }
        }
    }
}
