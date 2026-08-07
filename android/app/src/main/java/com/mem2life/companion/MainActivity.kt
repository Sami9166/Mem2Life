package com.mem2life.companion

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MenuBook
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.QuestionAnswer
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusManager
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.mem2life.companion.config.BackendConfigStore
import com.mem2life.companion.net.RecallApiClient
import com.mem2life.companion.net.WikiApiClient
import com.mem2life.companion.net.WikiEntity
import com.mem2life.companion.net.WikiPage
import com.mem2life.companion.net.WikiPageSummary
import com.mem2life.companion.query.QueryUiState
import com.mem2life.companion.query.VoiceQueryController
import com.mem2life.companion.recording.RecordingForegroundService
import com.mem2life.companion.recording.RecordingSessionController
import com.mem2life.companion.recording.RecordingState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Vuzix Blade 2 온글래스 앱의 단일 Activity.
 *
 * 화면 구조(홈 → 기록/질문 2모드):
 *   [홈] ── 기록 ──> [기록 화면] 녹화 시작/정지·업로드 상태
 *       └─ 질문 ──> [질문 화면] 푸시투톡 → recall → TTS (구현 예정)
 *       └─ 설정 ──> [설정 화면] 백엔드 host/port·디버그
 *
 * Blade 2 UI 제약:
 *  - 디스플레이 480x480, 웨이브가이드 특성상 검정 = 투명 → 순수 검정 배경 + 밝은
 *    전경의 다크 테마(Vuzix 공식 가이드라인). 배경을 그리지 않는 것이 곧 시야를 비우는 것.
 *  - 터치스크린이 없다. 관자놀이 터치패드가 D-pad(상/하/좌/우 + center) 이벤트로
 *    들어온다. 그래서 모든 조작 요소는 (1) 포커스가 눈에 확 띄어야 하고(웨이브가이드),
 *    (2) 포커스 이동 → center 탭으로만 동작해야 한다.
 *  - 착용 중 glanceable해야 한다 — 화면당 요소를 최소화하고 큰 타이포·고대비를 쓴다.
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
                    Mem2LifeApp(activity = this)
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

/** 앱 내 화면. 뒤로가기(두 손가락 탭 = KEYCODE_BACK)로 홈으로 돌아온다. */
private enum class Screen { HOME, RECORD, QUERY, WIKI, SETTINGS }

// Blade 2 웨이브가이드 팔레트 — 순수 검정 배경 위 밝은 전경, 포커스는 흰색으로 강조.
private val FgPrimary = Color.White
private val FgDim = Color(0xFFB0B0B0)
private val FgFaint = Color(0xFF7A7A7A)
private val FocusBorder = Color.White
private val IdleBorder = Color(0xFF3A3A3A)
private val FocusFill = Color(0xFF161616)
private val RecRed = Color(0xFFFF4A4A)

/**
 * 텍스트필드가 D-pad 상/하 키를 캐럿 이동으로 소비하기 전에 가로채 포커스를 넘긴다.
 *
 * Blade 2 터치패드 스와이프는 D-pad 이벤트로 들어오는데, 이 처리가 없으면 Host/Port
 * 필드에 한 번 포커스가 들어간 뒤 다른 요소로 돌아갈 방법이 없다(실기기 확인:
 * 위/아래 키는 필드가 삼키고, 뒤로가기는 IME만 닫을 뿐 포커스는 필드에 남는다).
 * 터치스크린이 없어 화면을 직접 누를 수도 없으므로 앱 재시작 외에는 탈출구가 없었다.
 */
/** D-pad 한 번에 스크롤할 픽셀 양(글래스 터치패드 스와이프 = D-pad 이벤트). */
private const val DPAD_SCROLL_STEP_PX = 260f

/**
 * 포커스 불가한 긴 본문(위키 페이지 등)을 D-pad로 스크롤한다.
 *
 * Blade 2 터치패드 스와이프는 D-pad 이벤트로 들어오는데, 본문 Text는 포커스 대상이
 * 아니라 포커스 이동으로는 스크롤이 안 된다(아래 내용이 안 보임). 그래서 상/하 키를
 * 가로채 [listState]를 직접 스크롤한다. **더 스크롤할 게 없을 때만 소비하지 않고
 * 흘려보내** — 그래야 끝에 다다르면 포커스가 하단의 연결 칩/버튼으로 넘어간다.
 */
private fun Modifier.dpadScroll(listState: LazyListState, scope: CoroutineScope): Modifier =
    onPreviewKeyEvent { event ->
        if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
        when (event.key) {
            Key.DirectionDown ->
                if (listState.canScrollForward) {
                    scope.launch { listState.animateScrollBy(DPAD_SCROLL_STEP_PX) }
                    true
                } else {
                    false
                }
            Key.DirectionUp ->
                if (listState.canScrollBackward) {
                    scope.launch { listState.animateScrollBy(-DPAD_SCROLL_STEP_PX) }
                    true
                } else {
                    false
                }
            else -> false
        }
    }

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

/**
 * 앱 루트 — 화면 네비게이션 + 녹화 상태(전 화면에서 공유)를 소유한다.
 *
 * 녹화 컨트롤러는 이 컴포저블이 아니라 Application이 소유한다(녹화 중 화면 전환·재진입
 * 에도 세션이 유지되어야 함 — 중복 세션 방지). 자세한 배경은
 * Mem2LifeApplication.recordingController 주석 참고.
 */
@Composable
private fun Mem2LifeApp(activity: MainActivity) {
    val context = activity
    val app = context.applicationContext as Mem2LifeApplication
    val backendConfigStore = remember { BackendConfigStore(context) }

    // 마이크 없는 개발 환경용 목업 오디오 토글(설정 화면에서 바꾼다). 실기기 기본은 실제 마이크.
    var useMockAudio by rememberSaveable { mutableStateOf(false) }
    val recordingController = remember(useMockAudio) { app.recordingController(useMockAudio) }
    val recordingState by recordingController.state.collectAsState()

    var screen by rememberSaveable { mutableStateOf(Screen.HOME) }
    val isRecording = recordingState is RecordingState.Recording

    // 녹화 시작/종료에 맞춰 포그라운드 서비스 유지.
    LaunchedEffect(recordingState) {
        when (recordingState) {
            is RecordingState.Recording -> RecordingForegroundService.start(context)
            is RecordingState.Stopped, is RecordingState.Error, RecordingState.Idle ->
                RecordingForegroundService.stop(context)
            else -> Unit
        }
    }

    when (screen) {
        Screen.HOME ->
            HomeScreen(
                isRecording = isRecording,
                onRecord = { screen = Screen.RECORD },
                onQuery = { screen = Screen.QUERY },
                onWiki = { screen = Screen.WIKI },
                onSettings = { screen = Screen.SETTINGS },
            )
        Screen.RECORD -> {
            BackHandler { screen = Screen.HOME }
            RecordScreen(activity = activity, controller = recordingController)
        }
        Screen.QUERY -> {
            BackHandler { screen = Screen.HOME }
            QueryScreen(backendConfigStore = backendConfigStore)
        }
        Screen.WIKI -> {
            BackHandler { screen = Screen.HOME }
            WikiScreen(backendConfigStore = backendConfigStore)
        }
        Screen.SETTINGS -> {
            BackHandler { screen = Screen.HOME }
            SettingsScreen(
                backendConfigStore = backendConfigStore,
                useMockAudio = useMockAudio,
                onToggleMockAudio = { useMockAudio = it },
                canToggleMockAudio =
                    when (recordingState) {
                        is RecordingState.Idle, is RecordingState.Stopped, is RecordingState.Error -> true
                        else -> false
                    },
            )
        }
    }
}

/**
 * 홈 — 두 개의 큰 모드 카드(기록/질문) + 작은 설정 진입.
 *
 * 진입 시 첫 카드에 자동 포커스를 준다(D-pad로 바로 조작 가능하도록). 카드는 좌/우
 * D-pad로 이동, center로 선택. 포커스는 흰 테두리 + 옅은 채움으로 확실히 드러낸다.
 */
@Composable
private fun HomeScreen(
    isRecording: Boolean,
    onRecord: () -> Unit,
    onQuery: () -> Unit,
    onWiki: () -> Unit,
    onSettings: () -> Unit,
) {
    val firstFocus = remember { FocusRequester() }
    LaunchedEffect(Unit) { firstFocus.requestFocus() }

    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 10.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            "Mem2Life",
            color = FgPrimary,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
        )
        Spacer(Modifier.height(10.dp))

        Row(
            modifier = Modifier.fillMaxWidth().weight(1f),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ModeCard(
                icon = Icons.Filled.Videocam,
                label = "기록",
                subtitle = if (isRecording) "● 기록 중" else "저장",
                accent = isRecording,
                focusRequester = firstFocus,
                onClick = onRecord,
                modifier = Modifier.weight(1f).fillMaxSize(),
            )
            ModeCard(
                icon = Icons.Filled.QuestionAnswer,
                label = "질문",
                subtitle = "물어보기",
                accent = false,
                focusRequester = null,
                onClick = onQuery,
                modifier = Modifier.weight(1f).fillMaxSize(),
            )
            ModeCard(
                icon = Icons.Filled.MenuBook,
                label = "위키",
                subtitle = "기록 열람",
                accent = false,
                focusRequester = null,
                onClick = onWiki,
                modifier = Modifier.weight(1f).fillMaxSize(),
            )
        }

        Spacer(Modifier.height(8.dp))
        SettingsChip(onClick = onSettings)
    }
}

/** 홈의 큰 모드 카드. 포커스 상태를 스스로 추적해 테두리·채움·틴트로 강조한다. */
@Composable
private fun ModeCard(
    icon: ImageVector,
    label: String,
    subtitle: String,
    accent: Boolean,
    focusRequester: FocusRequester?,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var focused by remember { mutableStateOf(false) }
    val border = if (focused) FocusBorder else IdleBorder
    val fill = if (focused) FocusFill else Color.Black
    val tint = if (accent) RecRed else if (focused) FgPrimary else FgDim

    Column(
        modifier =
            modifier
                .clip(RoundedCornerShape(18.dp))
                .background(fill)
                .border(BorderStroke(if (focused) 3.dp else 1.dp, border), RoundedCornerShape(18.dp))
                .then(if (focusRequester != null) Modifier.focusRequester(focusRequester) else Modifier)
                .onFocusChanged { focused = it.isFocused }
                .clickable(onClick = onClick)
                .padding(12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(icon, contentDescription = label, tint = tint, modifier = Modifier.size(44.dp))
        Spacer(Modifier.height(6.dp))
        Text(label, color = if (focused) FgPrimary else FgDim, fontSize = 18.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(2.dp))
        Text(
            subtitle,
            color = if (accent) RecRed else FgFaint,
            fontSize = 11.sp,
            textAlign = TextAlign.Center,
        )
    }
}

/** 홈 하단의 작은 설정 진입 — 포커스되면 밝아진다. */
@Composable
private fun SettingsChip(onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Row(
        modifier =
            Modifier
                .clip(RoundedCornerShape(20.dp))
                .border(BorderStroke(1.dp, if (focused) FocusBorder else IdleBorder), RoundedCornerShape(20.dp))
                .onFocusChanged { focused = it.isFocused }
                .clickable(onClick = onClick)
                .padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Icon(
            Icons.Filled.Settings,
            contentDescription = "설정",
            tint = if (focused) FgPrimary else FgDim,
            modifier = Modifier.size(18.dp),
        )
        Text("설정", color = if (focused) FgPrimary else FgDim, fontSize = 14.sp)
    }
}

/**
 * 질문 모드 — 푸시투톡 음성 질의 → recall 검색·답변 → TTS 재생 + 화면 표시.
 *
 * 상태: Idle(탭 대기) → Listening(듣는 중) → Thinking(질의 중) → Answered/Error.
 * SpeechRecognizer/TextToSpeech는 기기에 음성 서비스가 있어야 동작한다(Blade 2
 * 실기기 검증 필요 — VoiceQueryController 주석 참고). 화면을 떠나면 자원을 해제한다.
 */
@Composable
private fun QueryScreen(backendConfigStore: BackendConfigStore) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    val controller =
        remember {
            VoiceQueryController(
                context = context,
                scope = scope,
                recallClient = RecallApiClient { backendConfigStore.load() },
            )
        }
    DisposableEffect(controller) { onDispose { controller.release() } }

    val state by controller.state.collectAsState()

    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 14.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        when (val s = state) {
            is QueryUiState.Idle ->
                MicPrompt(
                    label = "질문하기",
                    hint = "탭하고 질문을 말하세요",
                    listening = false,
                    onTap = { controller.startListening() },
                )

            is QueryUiState.Listening ->
                MicPrompt(
                    label = "듣는 중…",
                    hint = s.partial.ifEmpty { "질문을 말하세요" },
                    listening = true,
                    onTap = { controller.stopListening() },
                )

            is QueryUiState.Thinking -> {
                CircularProgressIndicator(color = FgPrimary, strokeWidth = 3.dp)
                Spacer(Modifier.height(14.dp))
                Text("찾는 중…", color = FgPrimary, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text("\"${s.question}\"", color = FgDim, fontSize = 13.sp, textAlign = TextAlign.Center)
            }

            is QueryUiState.Answered -> AnswerView(s, onAskAgain = { controller.reset(); controller.startListening() })

            is QueryUiState.Error -> {
                Text("문제가 있었어요", color = RecRed, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text(s.message, color = FgDim, fontSize = 12.sp, textAlign = TextAlign.Center)
                Spacer(Modifier.height(16.dp))
                BigActionButton(text = "다시 질문", accent = false, onClick = { controller.startListening() })
            }
        }
    }
}

/** 마이크 프롬프트 — 큰 원형 버튼(탭=듣기 시작/정지). 듣는 중엔 강조색. */
@Composable
private fun MicPrompt(label: String, hint: String, listening: Boolean, onTap: () -> Unit) {
    val focusRequester = remember { FocusRequester() }
    LaunchedEffect(Unit) { focusRequester.requestFocus() }
    var focused by remember { mutableStateOf(false) }
    val ring = if (listening) RecRed else if (focused) FocusBorder else IdleBorder

    Box(
        modifier =
            Modifier
                .size(104.dp)
                .clip(CircleShape)
                .background(if (focused && !listening) FocusFill else Color.Black)
                .border(BorderStroke(if (listening || focused) 3.dp else 1.dp, ring), CircleShape)
                .focusRequester(focusRequester)
                .onFocusChanged { focused = it.isFocused }
                .clickable(onClick = onTap),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            Icons.Filled.Mic,
            contentDescription = label,
            tint = if (listening) RecRed else if (focused) FgPrimary else FgDim,
            modifier = Modifier.size(52.dp),
        )
    }
    Spacer(Modifier.height(14.dp))
    Text(label, color = FgPrimary, fontSize = 18.sp, fontWeight = FontWeight.Bold)
    Spacer(Modifier.height(4.dp))
    Text(hint, color = FgFaint, fontSize = 12.sp, textAlign = TextAlign.Center)
}

/**
 * 답변 화면 — 상태 라벨 + 본문 + 근거 라벨. TTS는 컨트롤러가 자동 재생한다.
 * "근거 보기"를 누르면 위키 원문(citations.excerpt)을 글래스에 띄운다(옵션 A).
 */
@Composable
private fun AnswerView(answered: QueryUiState.Answered, onAskAgain: () -> Unit) {
    val glass = answered.result.glass
    val citations = answered.result.citations
    var showEvidence by remember(answered) { mutableStateOf(false) }

    if (showEvidence) {
        BackHandler { showEvidence = false }
        EvidenceView(citations = citations, onBack = { showEvidence = false })
        return
    }

    val statusColor = if (glass.status == "not_found") FgDim else FgPrimary
    Text(glass.statusLabel, color = statusColor, fontSize = 13.sp, fontWeight = FontWeight.Bold)
    Spacer(Modifier.height(8.dp))
    Text(
        glass.displayText,
        color = FgPrimary,
        fontSize = 17.sp,
        textAlign = TextAlign.Center,
        lineHeight = 22.sp,
    )
    if (glass.evidence.isNotEmpty()) {
        Spacer(Modifier.height(8.dp))
        glass.evidence.take(2).forEach { ev ->
            Text("· ${ev.label}", color = FgFaint, fontSize = 11.sp, textAlign = TextAlign.Center)
        }
    }
    Spacer(Modifier.height(16.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        if (citations.isNotEmpty()) {
            BigActionButton(text = "근거 보기", accent = false, onClick = { showEvidence = true })
        }
        BigActionButton(text = "다시 질문", accent = false, onClick = onAskAgain)
    }
}

/**
 * 근거 원문 뷰 — 위키(옵시디언 세션 md)에서 검색된 근거 조각을 글래스에 띄운다.
 * 새 백엔드 엔드포인트 없이 `/recall/query` 응답의 citations(원문 포함)를 그대로 쓴다.
 * D-pad로 스크롤, 두 손가락 탭(뒤로) 또는 "돌아가기"로 답변으로 복귀.
 */
@Composable
private fun EvidenceView(
    citations: List<com.mem2life.companion.net.Citation>,
    onBack: () -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Text("근거 (위키 기록)", color = FgPrimary, fontSize = 15.sp, fontWeight = FontWeight.Bold)
        }
        citations.forEach { c ->
            item {
                Column {
                    Text(c.label, color = FgDim, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(3.dp))
                    Text(c.excerpt, color = FgPrimary, fontSize = 14.sp, lineHeight = 19.sp)
                    if (c.videoLink != null) {
                        Spacer(Modifier.height(2.dp))
                        Text("▶ ${c.timestamp ?: ""} 영상", color = FgFaint, fontSize = 10.sp)
                    }
                    HorizontalDivider(color = IdleBorder, modifier = Modifier.padding(top = 8.dp))
                }
            }
        }
        item {
            BigActionButton(text = "돌아가기", accent = false, onClick = onBack)
        }
    }
}

/** 위키 탐색 스택의 한 지점. 목록 ↔ 페이지 ↔ 엔티티를 오가며 그래프를 걸어다닌다. */
private sealed interface WikiNav {
    data object List : WikiNav
    data class Page(val page: WikiPage) : WikiNav
    data class Entity(val entity: WikiEntity) : WikiNav
}

/**
 * 위키 모드 — 볼트 페이지 브라우징(옵션 B) + 인물·주제 그래프 탐색(옵션 C).
 *
 * recall 서버의 `/wiki/pages`·`/wiki/page`·`/wiki/entity`를 읽는다. 페이지의
 * `[[위키링크]]`를 탭하면 그 인물·주제(엔티티)로 이동하고, 엔티티에서 언급 세션이나
 * 관련 엔티티로 다시 이동하며 그래프를 노드 단위로 걸어다닌다(480x480 논터치에
 * 맞춘 그래프 형태 — 시각 노드-엣지 대신 연결 탐색). 뒤로가기로 스택을 되짚고,
 * 목록에서 더 뒤로 가면 홈으로 나간다.
 */
@Composable
private fun WikiScreen(backendConfigStore: BackendConfigStore) {
    val scope = rememberCoroutineScope()
    val client = remember { WikiApiClient { backendConfigStore.load() } }

    var pages by remember { mutableStateOf<List<WikiPageSummary>?>(null) }
    var listError by remember { mutableStateOf<String?>(null) }
    var navError by remember { mutableStateOf<String?>(null) }
    var stack by remember { mutableStateOf<List<WikiNav>>(listOf(WikiNav.List)) }

    LaunchedEffect(Unit) {
        client.listPages().fold(
            onSuccess = { pages = it },
            onFailure = { listError = it.message ?: "목록을 불러오지 못했습니다." },
        )
    }

    fun openPage(path: String) {
        navError = null
        scope.launch {
            client.getPage(path).fold(
                onSuccess = { stack = stack + WikiNav.Page(it) },
                onFailure = { navError = it.message ?: "페이지 열기 실패" },
            )
        }
    }
    fun openEntity(name: String) {
        navError = null
        scope.launch {
            client.getEntity(name).fold(
                onSuccess = { stack = stack + WikiNav.Entity(it) },
                onFailure = { navError = it.message ?: "연결된 기록이 없습니다: $name" },
            )
        }
    }
    fun back() { if (stack.size > 1) stack = stack.dropLast(1) }

    // 스택이 목록보다 깊으면 뒤로가기는 스택을 되짚는다(홈으로 나가지 않게).
    BackHandler(enabled = stack.size > 1) { back() }

    when (val current = stack.last()) {
        is WikiNav.List ->
            WikiListView(pages = pages, listError = listError, navError = navError, onOpenPage = ::openPage)
        is WikiNav.Page ->
            WikiPageView(page = current.page, navError = navError, onOpenEntity = ::openEntity, onBack = ::back)
        is WikiNav.Entity ->
            WikiEntityView(
                entity = current.entity,
                navError = navError,
                onOpenPage = ::openPage,
                onOpenEntity = ::openEntity,
                onBack = ::back,
            )
    }
}

/** 위키 목록 뷰 — 세션/문서 목록. */
@Composable
private fun WikiListView(
    pages: List<WikiPageSummary>?,
    listError: String?,
    navError: String?,
    onOpenPage: (String) -> Unit,
) {
    Column(modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp)) {
        Text("위키", color = FgPrimary, fontSize = 18.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        when {
            listError != null -> Text("불러오기 실패: $listError", color = RecRed, fontSize = 12.sp)
            pages == null -> Text("불러오는 중…", color = FgDim, fontSize = 13.sp)
            pages.isEmpty() -> Text("아직 기록된 위키 페이지가 없습니다.", color = FgFaint, fontSize = 13.sp)
            else ->
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    pages.forEach { p ->
                        item { WikiListRow(summary = p, onClick = { onOpenPage(p.path) }) }
                    }
                    navError?.let { item { Text("오류: $it", color = RecRed, fontSize = 11.sp) } }
                }
        }
    }
}

/** 위키 목록 행 — 제목 + 종류·날짜. 포커스되면 강조. */
@Composable
private fun WikiListRow(summary: WikiPageSummary, onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Column(
        modifier =
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp))
                .background(if (focused) FocusFill else Color.Black)
                .border(BorderStroke(if (focused) 2.dp else 1.dp, if (focused) FocusBorder else IdleBorder), RoundedCornerShape(10.dp))
                .onFocusChanged { focused = it.isFocused }
                .clickable(onClick = onClick)
                .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        Text(summary.title, color = if (focused) FgPrimary else FgDim, fontSize = 15.sp, fontWeight = FontWeight.Bold)
        Text(
            listOfNotNull(kindLabel(summary.kind), summary.date).joinToString(" · "),
            color = FgFaint,
            fontSize = 10.sp,
        )
    }
}

/**
 * 위키 페이지 본문 — 마크다운을 큰 글씨로 스크롤 표시 + 하단에 연결(위키링크) 칩.
 * 칩을 탭하면 그 인물·주제(엔티티)로 이동해 그래프를 걸어다닌다.
 */
@Composable
private fun WikiPageView(page: WikiPage, navError: String?, onOpenEntity: (String) -> Unit, onBack: () -> Unit) {
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val scrollFocus = remember { FocusRequester() }
    // 진입 시 스크롤 컨테이너에 포커스를 줘야 D-pad(스와이프) 스크롤이 먹는다.
    LaunchedEffect(page) { scrollFocus.requestFocus() }
    LazyColumn(
        state = listState,
        modifier =
            Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp, vertical = 12.dp)
                .focusRequester(scrollFocus)
                .dpadScroll(listState, scope)
                .focusable(),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        item {
            Text(page.title, color = FgPrimary, fontSize = 17.sp, fontWeight = FontWeight.Bold)
            Text(
                listOfNotNull(kindLabel(page.kind), page.date).joinToString(" · "),
                color = FgFaint,
                fontSize = 10.sp,
            )
            HorizontalDivider(color = IdleBorder, modifier = Modifier.padding(vertical = 6.dp))
        }
        page.body.lines().forEach { line ->
            if (line.isNotBlank()) {
                item {
                    Text(
                        cleanMarkdownLine(line),
                        color = lineColor(line),
                        fontSize = lineSize(line),
                        fontWeight = if (line.trimStart().startsWith("#")) FontWeight.Bold else FontWeight.Normal,
                        lineHeight = 18.sp,
                    )
                }
            }
        }
        if (page.links.isNotEmpty()) {
            item {
                Spacer(Modifier.height(8.dp))
                HorizontalDivider(color = IdleBorder)
                Text("연결 (인물·주제)", color = FgDim, fontSize = 11.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 6.dp))
            }
            page.links.forEach { link -> item { WikiChip(label = link, onClick = { onOpenEntity(link) }) } }
        }
        navError?.let { item { Text("오류: $it", color = RecRed, fontSize = 11.sp) } }
        item {
            Spacer(Modifier.height(8.dp))
            BigActionButton(text = "뒤로", accent = false, onClick = onBack)
        }
    }
}

/**
 * 엔티티(인물·주제) 가상 페이지 — 이 노드를 언급한 세션들(백링크) + 관련 엔티티.
 * 세션을 탭하면 그 페이지로, 관련 엔티티를 탭하면 그 노드로 이동한다(그래프 traversal).
 */
@Composable
private fun WikiEntityView(
    entity: WikiEntity,
    navError: String?,
    onOpenPage: (String) -> Unit,
    onOpenEntity: (String) -> Unit,
    onBack: () -> Unit,
) {
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val scrollFocus = remember { FocusRequester() }
    LaunchedEffect(entity) { scrollFocus.requestFocus() }
    LazyColumn(
        state = listState,
        modifier =
            Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp, vertical = 12.dp)
                .focusRequester(scrollFocus)
                .dpadScroll(listState, scope)
                .focusable(),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        item {
            Text("◆ ${entity.name}", color = FgPrimary, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            Text("인물·주제 노드", color = FgFaint, fontSize = 10.sp)
            HorizontalDivider(color = IdleBorder, modifier = Modifier.padding(vertical = 6.dp))
            Text("언급된 세션", color = FgDim, fontSize = 12.sp, fontWeight = FontWeight.Bold)
        }
        entity.mentionedIn.forEach { m ->
            item {
                WikiChip(
                    label = m.title + (m.date?.let { " · $it" } ?: ""),
                    onClick = { onOpenPage(m.path) },
                )
                m.excerpts.firstOrNull()?.let {
                    Text(cleanMarkdownLine(it).take(70), color = FgFaint, fontSize = 10.sp, lineHeight = 14.sp, modifier = Modifier.padding(start = 12.dp))
                }
            }
        }
        if (entity.related.isNotEmpty()) {
            item {
                Spacer(Modifier.height(6.dp))
                Text("관련 (함께 등장)", color = FgDim, fontSize = 12.sp, fontWeight = FontWeight.Bold)
            }
            entity.related.forEach { r -> item { WikiChip(label = r, onClick = { onOpenEntity(r) }) } }
        }
        navError?.let { item { Text("오류: $it", color = RecRed, fontSize = 11.sp) } }
        item {
            Spacer(Modifier.height(8.dp))
            BigActionButton(text = "뒤로", accent = false, onClick = onBack)
        }
    }
}

/** 위키 그래프 이동용 칩(연결 노드) — 포커스되면 강조, 탭하면 그 노드로 이동. */
@Composable
private fun WikiChip(label: String, onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Row(
        modifier =
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .background(if (focused) FocusFill else Color.Black)
                .border(BorderStroke(if (focused) 2.dp else 1.dp, if (focused) FocusBorder else IdleBorder), RoundedCornerShape(8.dp))
                .onFocusChanged { focused = it.isFocused }
                .clickable(onClick = onClick)
                .padding(horizontal = 12.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("→ ", color = if (focused) FgPrimary else FgFaint, fontSize = 13.sp)
        Text(label, color = if (focused) FgPrimary else FgDim, fontSize = 13.sp)
    }
}

private fun kindLabel(kind: String): String =
    when (kind) {
        "session" -> "세션"
        "people" -> "인물"
        "topic", "topics" -> "주제"
        "daily" -> "하루"
        else -> kind
    }

/** 마크다운 헤더(##)는 강조색·큰 글씨로 아주 단순하게 구분한다. */
private fun lineColor(line: String): Color = if (line.trimStart().startsWith("#")) FgPrimary else FgDim

private fun lineSize(line: String) = if (line.trimStart().startsWith("#")) 15.sp else 13.sp

/** 글래스 가독성을 위해 마크다운 마커(#, -, *)만 앞에서 벗긴다(간단 렌더). */
private fun cleanMarkdownLine(line: String): String =
    line.trimStart().trimStart('#', '-', '*', ' ').ifEmpty { line.trim() }

/** 녹화 중 컨트롤을 띄운 뒤 이만큼 입력이 없으면 다시 감춘다. */
private const val CONTROLS_AUTO_HIDE_MS = 5_000L

/**
 * 기록 화면 — 유휴 시 큰 시작 버튼, 녹화 중엔 glanceable 상태(REC·경과·청크 수).
 *
 * 녹화가 시작되면 컨트롤을 감춰(웨이브가이드에서 검정 = 투명 → 시야를 비운다) 착용자
 * 시야를 방해하지 않는다. 터치패드 입력이 오면 컨트롤을 다시 띄우고, 그 첫 입력은
 * 삼킨다(안 보이는 버튼이 눌리지 않도록). 이 동작은 실기기에서 검증된 제약이다.
 */
@Composable
private fun RecordScreen(activity: MainActivity, controller: RecordingSessionController) {
    val statusSnapshot by controller.statusSnapshot.collectAsState()
    val recordingState by controller.state.collectAsState()
    val isRecording = recordingState is RecordingState.Recording

    var controlsVisible by remember { mutableStateOf(true) }
    var inputTick by remember { mutableStateOf(0) }
    var elapsedSec by remember { mutableStateOf(0) }

    LaunchedEffect(isRecording) { controlsVisible = !isRecording }

    // 경과 시간 카운터(녹화 중에만).
    LaunchedEffect(isRecording) {
        elapsedSec = 0
        while (isRecording) {
            delay(1_000)
            elapsedSec += 1
        }
    }

    // 컨트롤을 띄운 뒤 입력이 없으면 다시 감춘다(입력마다 inputTick이 올라 타이머 재시작).
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
                true // 복귀용으로만 쓰고 삼킨다.
            } else {
                inputTick += 1
                false
            }
        }
        onDispose { activity.onTouchpadInput = null }
    }

    if (isRecording && !controlsVisible) {
        // 녹화 중 & 컨트롤 숨김 — 아무것도 그리지 않는다(= 투명, 시야 비움).
        return
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        if (isRecording) {
            // glanceable 녹화 상태.
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Box(modifier = Modifier.size(14.dp).clip(CircleShape).background(RecRed))
                Text("REC", color = RecRed, fontSize = 26.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(6.dp))
            Text(formatElapsed(elapsedSec), color = FgPrimary, fontSize = 34.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(10.dp))
            Text(
                "업로드 ${statusSnapshot.uploadedVideoChunks} · 대기 ${statusSnapshot.pendingVideoChunks}",
                color = FgDim,
                fontSize = 14.sp,
            )
            statusSnapshot.lastUploadError?.let {
                Spacer(Modifier.height(4.dp))
                Text("오류: $it", color = RecRed, fontSize = 11.sp, textAlign = TextAlign.Center)
            }
            Spacer(Modifier.height(18.dp))
            BigActionButton(text = "정지", accent = true, onClick = { controller.stopRecording() })
        } else {
            Text("기록", color = FgPrimary, fontSize = 24.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            Text(stateLabel(recordingState), color = FgDim, fontSize = 13.sp, textAlign = TextAlign.Center)
            Spacer(Modifier.height(20.dp))
            BigActionButton(text = "시작", accent = false, onClick = { controller.startRecording() })
        }
    }
}

/** 큰 원형/알약 액션 버튼 — 포커스되면 채워지고 밝아진다. */
@Composable
private fun BigActionButton(text: String, accent: Boolean, onClick: () -> Unit) {
    val focusRequester = remember { FocusRequester() }
    LaunchedEffect(Unit) { focusRequester.requestFocus() }
    var focused by remember { mutableStateOf(false) }
    val base = if (accent) RecRed else FgPrimary
    Box(
        modifier =
            Modifier
                .clip(RoundedCornerShape(28.dp))
                .background(if (focused) base else Color.Black)
                .border(BorderStroke(if (focused) 3.dp else 2.dp, base), RoundedCornerShape(28.dp))
                .focusRequester(focusRequester)
                .onFocusChanged { focused = it.isFocused }
                .clickable(onClick = onClick)
                .padding(horizontal = 40.dp, vertical = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            color = if (focused) Color.Black else base,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
        )
    }
}

/** 설정 화면 — 백엔드 host/port + 디버그(목업 오디오). 스크롤 리스트. */
@Composable
private fun SettingsScreen(
    backendConfigStore: BackendConfigStore,
    useMockAudio: Boolean,
    onToggleMockAudio: (Boolean) -> Unit,
    canToggleMockAudio: Boolean,
) {
    val focusManager = LocalFocusManager.current
    var config by remember { mutableStateOf(backendConfigStore.load()) }
    var hostText by remember { mutableStateOf(config.host) }
    var portText by remember { mutableStateOf(config.port.toString()) }
    var recallPortText by remember { mutableStateOf(config.recallPort.toString()) }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { Text("설정", color = FgPrimary, fontSize = 20.sp, fontWeight = FontWeight.Bold) }
        item { HorizontalDivider(color = IdleBorder) }
        item {
            Text("백엔드", color = FgDim, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            OutlinedTextField(
                value = hostText,
                onValueChange = { hostText = it },
                label = { Text("Host (백엔드 PC의 LAN IP)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                keyboardActions = KeyboardActions(onNext = { focusManager.moveFocus(FocusDirection.Down) }),
                modifier = Modifier.fillMaxWidth().dpadFocusEscape(focusManager),
            )
            OutlinedTextField(
                value = portText,
                onValueChange = { portText = it },
                label = { Text("Port (업로드 수신)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Next),
                keyboardActions = KeyboardActions(onNext = { focusManager.moveFocus(FocusDirection.Down) }),
                modifier = Modifier.fillMaxWidth().dpadFocusEscape(focusManager),
            )
            OutlinedTextField(
                value = recallPortText,
                onValueChange = { recallPortText = it },
                label = { Text("Recall Port (질의응답)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { focusManager.clearFocus() }),
                modifier = Modifier.fillMaxWidth().dpadFocusEscape(focusManager),
            )
            Button(onClick = {
                val newConfig =
                    config.copy(
                        host = hostText,
                        port = portText.toIntOrNull() ?: config.port,
                        recallPort = recallPortText.toIntOrNull() ?: config.recallPort,
                    )
                backendConfigStore.save(newConfig)
                config = newConfig
            }) { Text("저장") }
        }
        item { HorizontalDivider(color = IdleBorder) }
        item {
            Text("디버그", color = FgDim, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = useMockAudio, onCheckedChange = onToggleMockAudio, enabled = canToggleMockAudio)
                Text(
                    "목업 오디오 (마이크 없는 개발용)" +
                        if (!canToggleMockAudio) " — 녹화 중 변경 불가" else "",
                    color = FgDim,
                    fontSize = 12.sp,
                )
            }
        }
        item {
            Spacer(Modifier.height(4.dp))
            Text("두 손가락 탭 = 뒤로", color = FgFaint, fontSize = 11.sp)
        }
    }
}

private fun formatElapsed(sec: Int): String = "%02d:%02d".format(sec / 60, sec % 60)

private fun stateLabel(state: RecordingState): String =
    when (state) {
        is RecordingState.Idle -> "대기 중"
        is RecordingState.Starting -> "시작하는 중…"
        is RecordingState.Recording -> "기록 중"
        is RecordingState.Stopping -> "정지하는 중…"
        is RecordingState.Stopped -> "정지됨 · 업로드 완료"
        is RecordingState.Error -> "오류: ${state.message}"
    }
