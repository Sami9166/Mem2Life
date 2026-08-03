package com.mem2life.companion.net

import android.util.Log
import com.mem2life.companion.config.BackendConfig
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

private const val TAG = "Mem2Life:RecallApi"

/** 글래스 화면 근거 1건 — `POST /recall/query` 응답의 glass.evidence 항목. */
data class GlassEvidence(val label: String, val videoLink: String?)

/**
 * 글래스 출력 전용 응답 표현 — recall API의 `glass` 필드를 그대로 담는다.
 *
 * 앱은 이 데이터만 렌더하면 된다: [ttsText]는 스피커로 읽고, [displayText]와
 * [evidence]는 480x480 화면에 올린다(계약: recall/api.py의 GlassOut 참고).
 */
data class GlassAnswer(
    val status: String, // answered | answered_from_video | not_found
    val statusLabel: String,
    val ttsText: String,
    val displayText: String,
    val evidence: List<GlassEvidence>,
) {
    companion object {
        fun fromGlassJson(glass: JSONObject): GlassAnswer {
            val evArray = glass.optJSONArray("evidence")
            val evidence =
                buildList {
                    if (evArray != null) {
                        for (i in 0 until evArray.length()) {
                            val e = evArray.getJSONObject(i)
                            add(
                                GlassEvidence(
                                    label = e.optString("label"),
                                    videoLink = e.optString("video_link").ifEmpty { null },
                                ),
                            )
                        }
                    }
                }
            return GlassAnswer(
                status = glass.optString("status", "answered"),
                statusLabel = glass.optString("status_label", ""),
                ttsText = glass.optString("tts_text"),
                displayText = glass.optString("display_text"),
                evidence = evidence,
            )
        }
    }
}

/**
 * 질의응답(recall) 서버 클라이언트.
 *
 * 업로드 수신 서버([SessionApiClient])와 달리 recall은 별도 FastAPI(`recall serve`,
 * 다른 포트 = [BackendConfig.recallPort])라 이 클라이언트로 따로 친다.
 * 질문은 앱에서 STT로 텍스트화해 넘긴다(계약: `question` 필드).
 */
class RecallApiClient(private val configProvider: () -> BackendConfig) {

    private val client =
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            // recall은 STT→검색→LLM→(필요 시)영상 재조회까지 돌 수 있어 넉넉히 잡는다.
            .readTimeout(60, TimeUnit.SECONDS)
            .build()

    suspend fun query(question: String): Result<GlassAnswer> =
        withContext(Dispatchers.IO) {
            runCatching {
                val config = configProvider()
                val payload = JSONObject().put("question", question).toString()
                val request =
                    Request.Builder()
                        .url(config.recallQueryUrl())
                        .post(payload.toRequestBody("application/json".toMediaType()))
                        .build()
                client.newCall(request).execute().use { response ->
                    val bodyText = response.body?.string().orEmpty()
                    if (!response.isSuccessful) {
                        error("POST /recall/query 실패: HTTP ${response.code} ${bodyText.take(200)}")
                    }
                    val json = JSONObject(bodyText)
                    val glass =
                        json.optJSONObject("glass")
                            ?: error("응답에 glass 필드가 없습니다(recall 서버 버전 확인)")
                    GlassAnswer.fromGlassJson(glass)
                }
            }.onFailure { Log.w(TAG, "recall 질의 실패", it) }
        }
}
