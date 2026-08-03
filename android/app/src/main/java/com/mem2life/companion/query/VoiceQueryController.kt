package com.mem2life.companion.query

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.util.Log
import com.mem2life.companion.net.GlassAnswer
import com.mem2life.companion.net.RecallApiClient
import java.util.Locale
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val TAG = "Mem2Life:VoiceQuery"

/**
 * 푸시투톡 질의 흐름 상태.
 *
 *   Idle → (탭) → Listening → (발화 종료) → Thinking → Answered / Error
 */
sealed interface QueryUiState {
    data object Idle : QueryUiState
    data class Listening(val partial: String) : QueryUiState
    data class Thinking(val question: String) : QueryUiState
    data class Answered(val question: String, val answer: GlassAnswer) : QueryUiState
    data class Error(val message: String) : QueryUiState
}

/**
 * 음성 질의 전체를 오케스트레이션한다: 온디바이스 STT(SpeechRecognizer)로 질문을
 * 텍스트화 → recall 서버 질의 → 답변을 TTS(TextToSpeech)로 읽고 화면에 표시.
 *
 * Blade 2 유의: SpeechRecognizer/TextToSpeech는 기기에 음성 인식·합성 서비스가
 * 설치돼 있어야 동작한다(표준 Android는 Google 음성 서비스). AOSP 기반 기기에
 * 없을 수 있으므로, 사용 불가 시 [QueryUiState.Error]로 명확히 알린다 — 실기기
 * 검증에서 재확인이 필요한 지점이다. STT는 이 컨트롤러 뒤에 숨겨 두어, 필요하면
 * 나중에 "질문 오디오를 서버 RTZR로 전송"하는 구현으로 교체할 수 있다.
 *
 * SpeechRecognizer는 메인 스레드에서만 조작해야 하므로, STT 관련 호출은 모두
 * Dispatchers.Main에서 수행한다.
 */
class VoiceQueryController(
    private val context: Context,
    private val scope: CoroutineScope,
    private val recallClient: RecallApiClient,
) {
    private val _state = MutableStateFlow<QueryUiState>(QueryUiState.Idle)
    val state: StateFlow<QueryUiState> = _state.asStateFlow()

    private var recognizer: SpeechRecognizer? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    init {
        tts =
            TextToSpeech(context.applicationContext) { status ->
                if (status == TextToSpeech.SUCCESS) {
                    val result = tts?.setLanguage(Locale.KOREAN)
                    ttsReady =
                        result != TextToSpeech.LANG_MISSING_DATA && result != TextToSpeech.LANG_NOT_SUPPORTED
                    if (!ttsReady) Log.w(TAG, "TTS 한국어 미지원(음성 없이 화면만 표시)")
                } else {
                    Log.w(TAG, "TTS 초기화 실패(음성 없이 화면만 표시)")
                }
            }
    }

    /** 푸시투톡 시작 — 마이크를 열고 질문을 듣는다. */
    fun startListening() {
        val current = _state.value
        if (current is QueryUiState.Listening || current is QueryUiState.Thinking) return
        scope.launch(Dispatchers.Main) {
            if (!SpeechRecognizer.isRecognitionAvailable(context)) {
                _state.value =
                    QueryUiState.Error("이 기기에서 음성 인식을 사용할 수 없습니다(음성 서비스 미설치).")
                return@launch
            }
            recognizer?.destroy()
            val sr = SpeechRecognizer.createSpeechRecognizer(context)
            recognizer = sr
            sr.setRecognitionListener(recognitionListener)
            val intent =
                Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(
                        RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                        RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
                    )
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ko-KR")
                    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                }
            _state.value = QueryUiState.Listening(partial = "")
            sr.startListening(intent)
        }
    }

    /** 사용자가 다 말했다고 알림(선택). SpeechRecognizer가 알아서 종료하기도 한다. */
    fun stopListening() {
        scope.launch(Dispatchers.Main) { recognizer?.stopListening() }
    }

    /** 질문/답변을 지우고 다시 대기 상태로. */
    fun reset() {
        scope.launch(Dispatchers.Main) {
            recognizer?.cancel()
            tts?.stop()
            _state.value = QueryUiState.Idle
        }
    }

    fun release() {
        recognizer?.destroy()
        recognizer = null
        tts?.stop()
        tts?.shutdown()
        tts = null
    }

    private val recognitionListener =
        object : RecognitionListener {
            override fun onPartialResults(partialResults: Bundle?) {
                val text =
                    partialResults
                        ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull()
                        .orEmpty()
                if (_state.value is QueryUiState.Listening) {
                    _state.value = QueryUiState.Listening(partial = text)
                }
            }

            override fun onResults(results: Bundle?) {
                val question =
                    results
                        ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull()
                        ?.trim()
                        .orEmpty()
                if (question.isEmpty()) {
                    _state.value = QueryUiState.Error("질문을 알아듣지 못했습니다. 다시 시도하세요.")
                    return
                }
                askRecall(question)
            }

            override fun onError(error: Int) {
                _state.value = QueryUiState.Error(sttErrorMessage(error))
            }

            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit
        }

    private fun askRecall(question: String) {
        _state.value = QueryUiState.Thinking(question)
        scope.launch {
            val result = recallClient.query(question)
            withContext(Dispatchers.Main) {
                result.fold(
                    onSuccess = { answer ->
                        _state.value = QueryUiState.Answered(question, answer)
                        speak(answer.ttsText)
                    },
                    onFailure = {
                        _state.value =
                            QueryUiState.Error("답변을 가져오지 못했습니다(recall 서버 연결 확인): ${it.message}")
                    },
                )
            }
        }
    }

    private fun speak(text: String) {
        if (!ttsReady || text.isBlank()) return
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "mem2life-answer")
    }

    private fun sttErrorMessage(error: Int): String =
        when (error) {
            SpeechRecognizer.ERROR_NO_MATCH -> "질문을 알아듣지 못했습니다. 다시 시도하세요."
            SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "말소리가 없어 종료했습니다. 탭 후 말씀하세요."
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "마이크 권한이 없습니다."
            SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                "음성 인식 네트워크 오류입니다."
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "음성 인식이 사용 중입니다. 잠시 후 다시 시도하세요."
            else -> "음성 인식 오류(code=$error)."
        }
}
