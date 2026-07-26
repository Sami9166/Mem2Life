package com.mem2life.companion

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusManager
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.mem2life.companion.config.BackendConfigStore
import com.mem2life.companion.recording.RecordingForegroundService
import com.mem2life.companion.recording.RecordingState
import kotlinx.coroutines.delay

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

    /**
     * 터치패드 입력 후크. 녹화 중 화면을 감춘 동안 들어온 입력은 "컨트롤 복귀"에만 쓰고
     * true를 돌려 여기서 삼킨다 — 그대로 흘려보내면 착용자가 **보지도 못한 버튼이
     * 눌린다**(포커스가 남아 있는 `녹화 종료`가 첫 스와이프에 실행되는 식).
     *
     * 컨트롤이 보이는 동안에는 false를 돌려 정상 조작을 그대로 통과시킨다.
     */
    var onTouchpadInput: (() -> Boolean)? = null

    /** DOWN을 삼켰으면 짝이 되는 UP도 삼킨다 — UP만 UI에 도달하면 오작동 소지가 있다. */
    private var swallowedKeyCode: Int? = null

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        when (event.action) {
            KeyEvent.ACTION_DOWN ->
                if (onTouchpadInput?.invoke() == true) {
                    swallowedKeyCode = event.keyCode
                    return true
                }
            KeyEvent.ACTION_UP ->
                if (swallowedKeyCode == event.keyCode) {
                    swallowedKeyCode = null
                    return true
                }
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestRuntimePermissionsIfNeeded()

        setContent {
            // 웨이브가이드에서 검정이 투명으로 보이므로 배경은 항상 순수 검정.
            MaterialTheme(colorScheme = darkColorScheme(background = Color.Black, surface = Color.Black)) {
                Surface(modifier = Modifier.fillMaxSize(), color = Color.Black) {
                    Mem2LifeScreen(activity = this)
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

/**
 * 텍스트필드가 D-pad 상/하 키를 캐럿 이동으로 소비하기 전에 가로채 포커스를 넘긴다.
 *
 * Blade 2 터치패드 스와이프는 D-pad 이벤트로 들어오는데, 이 처리가 없으면 Host/Port
 * 필드에 한 번 포커스가 들어간 뒤 **녹화 버튼으로 돌아갈 방법이 없다**(실기기 확인:
 * 위/아래 키는 필드가 삼키고, 뒤로가기는 IME만 닫을 뿐 포커스는 필드에 남는다).
 * 터치스크린이 없어 화면을 직접 누를 수도 없으므로 앱 재시작 외에는 탈출구가 없었다.
 *
 * [FocusManager.moveFocus]가 false(더 이동할 대상 없음)면 소비하지 않고 그대로 흘려
 * 기본 동작에 맡긴다.
 */
private fun Modifier.dpadFocusEscape(focusManager: FocusManager): Modifier =
    onPreviewKeyEvent { event ->
        if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
        val direction =
            when (event.key) {
                Key.DirectionUp -> FocusDirection.Up
                Key.DirectionDown -> FocusDirection.Down
                else -> return@onPreviewKeyEvent false
            }
        focusManager.moveFocus(direction)
    }

/** 녹화 중 컨트롤을 띄운 뒤 이만큼 입력이 없으면 다시 감춘다. */
private const val CONTROLS_AUTO_HIDE_MS = 5_000L

@Composable
private fun Mem2LifeScreen(activity: MainActivity) {
    val context = activity
    val focusManager = LocalFocusManager.current

    val backendConfigStore = remember { BackendConfigStore(context) }

    // Blade 2 실기기에는 온보드 마이크가 있으므로 기본은 실제 마이크. 마이크가
    // 없는/불안정한 개발 환경에서만 목업 오디오로 전환한다.
    var useMockAudio by rememberSaveable { mutableStateOf(false) }

    // 컨트롤러는 이 화면이 아니라 Application이 소유한다 — 녹화 중 뒤로가기로 나갔다가
    // 다시 들어와도 진행 중인 세션을 그대로 이어받아야 하기 때문이다(중복 세션 방지).
    // 자세한 배경은 Mem2LifeApplication.recordingController 주석 참고.
    val app = context.applicationContext as Mem2LifeApplication
    val recordingController = remember(useMockAudio) { app.recordingController(useMockAudio) }

    val recordingState by recordingController.state.collectAsState()
    val statusSnapshot by recordingController.statusSnapshot.collectAsState()

    // 목업 오디오 체크박스는 녹화가 진행 중이 아닐 때만 바꿀 수 있다. 녹화 중에
    // 바꾸면 remember(useMockAudio)가 컨트롤러를 새로 요청하게 되어, 진행 중인
    // 세션(살아있는 코루틴 스코프·업로드 큐·오디오 소켓)이 UI에서 끊긴 채 남을 수
    // 있다. 체크박스를 잠가 그 상태 자체를 만들지 않는다.
    // (Application 쪽에서도 녹화 중이면 컨트롤러를 교체하지 않도록 이중으로 막는다.)
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

    // 녹화가 시작되면 화면을 비운다. 웨이브가이드에서 검정 = 투명이라 "아무것도 그리지
    // 않는 것"이 곧 착용자 시야를 완전히 비우는 것이다 — 착용자가 설정 화면을 계속
    // 보고 있을 이유가 없고, 녹화·업로드는 포그라운드 서비스로 그대로 진행된다.
    // (화면 자체를 끄지는 않는다. 앱은 포그라운드에 남아 터치패드 입력을 받아야
    //  다시 컨트롤을 띄울 수 있기 때문이다. 디스플레이는 기기 기본 타임아웃으로 꺼진다.)
    val isRecording = recordingState is RecordingState.Recording
    var controlsVisible by remember { mutableStateOf(true) }
    var inputTick by remember { mutableStateOf(0) }

    LaunchedEffect(isRecording) { controlsVisible = !isRecording }

    // 컨트롤을 띄운 뒤 입력이 없으면 다시 감춘다. inputTick이 키에 있어 입력이 올
    // 때마다 이 효과가 재시작되며 타이머도 함께 초기화된다.
    LaunchedEffect(isRecording, controlsVisible, inputTick) {
        if (isRecording && controlsVisible) {
            delay(CONTROLS_AUTO_HIDE_MS)
            controlsVisible = false
        }
    }

    DisposableEffect(activity, isRecording, controlsVisible) {
        activity.onTouchpadInput = {
            if (isRecording && !controlsVisible) {
                controlsVisible = true
                true // 복귀용으로만 쓰고 삼킨다 — 안 보이는 버튼이 눌리지 않도록.
            } else {
                inputTick += 1
                false
            }
        }
        onDispose { activity.onTouchpadInput = null }
    }

    Scaffold(containerColor = Color.Black) { padding ->
        if (!controlsVisible) {
            // 녹화 중 — 아무것도 그리지 않는다(= 투명).
            return@Scaffold
        }
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
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                    keyboardActions = KeyboardActions(onNext = { focusManager.moveFocus(FocusDirection.Down) }),
                    modifier = Modifier.fillMaxWidth().dpadFocusEscape(focusManager),
                )
                OutlinedTextField(
                    value = portText,
                    onValueChange = { portText = it },
                    label = { Text("Port") },
                    singleLine = true,
                    keyboardOptions =
                        KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { focusManager.clearFocus() }),
                    modifier = Modifier.fillMaxWidth().dpadFocusEscape(focusManager),
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
