package com.mem2life.companion.net

import android.util.Log
import com.mem2life.companion.config.BackendConfig
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject

private const val TAG = "Mem2Life:WikiApi"

/** 위키 페이지 목록 항목 — `GET /wiki/pages`의 pages[] 항목. */
data class WikiPageSummary(val path: String, val kind: String, val title: String, val date: String?)

/** 위키 페이지 본문 — `GET /wiki/page`. */
data class WikiPage(val path: String, val kind: String, val title: String, val date: String?, val body: String)

/**
 * 위키 열람(읽기 전용) 클라이언트 — recall 서버(`recall serve`)의 wiki 경로를 친다.
 *
 * 글래스는 PC 볼트 파일을 직접 못 읽으므로 로컬 서버가 서빙하는 이 경로로 받아온다
 * (배포와 무관, 로컬 서버 라우트). 질의응답([RecallApiClient])과 같은 host/recallPort.
 */
class WikiApiClient(private val configProvider: () -> BackendConfig) {

    private val client =
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .build()

    suspend fun listPages(): Result<List<WikiPageSummary>> =
        withContext(Dispatchers.IO) {
            runCatching {
                val request = Request.Builder().url(configProvider().wikiPagesUrl()).get().build()
                client.newCall(request).execute().use { response ->
                    val bodyText = response.body?.string().orEmpty()
                    if (!response.isSuccessful) error("GET /wiki/pages 실패: HTTP ${response.code}")
                    val arr = JSONObject(bodyText).optJSONArray("pages")
                    buildList {
                        if (arr != null) {
                            for (i in 0 until arr.length()) {
                                val p = arr.getJSONObject(i)
                                add(
                                    WikiPageSummary(
                                        path = p.optString("path"),
                                        kind = p.optString("kind"),
                                        title = p.optString("title"),
                                        date = p.optString("date").ifEmpty { null },
                                    ),
                                )
                            }
                        }
                    }
                }
            }.onFailure { Log.w(TAG, "위키 목록 조회 실패", it) }
        }

    suspend fun getPage(path: String): Result<WikiPage> =
        withContext(Dispatchers.IO) {
            runCatching {
                val config = configProvider()
                // path를 쿼리 파라미터로 안전하게 인코딩해 붙인다(한글/슬래시 포함).
                val url =
                    config.wikiPagesUrl().replace("/wiki/pages", "/wiki/page").toHttpUrl().newBuilder()
                        .addQueryParameter("path", path)
                        .build()
                val request = Request.Builder().url(url).get().build()
                client.newCall(request).execute().use { response ->
                    val bodyText = response.body?.string().orEmpty()
                    if (!response.isSuccessful) error("GET /wiki/page 실패: HTTP ${response.code}")
                    val json = JSONObject(bodyText)
                    WikiPage(
                        path = json.optString("path"),
                        kind = json.optString("kind"),
                        title = json.optString("title"),
                        date = json.optString("date").ifEmpty { null },
                        body = json.optString("body"),
                    )
                }
            }.onFailure { Log.w(TAG, "위키 페이지 조회 실패($path)", it) }
        }
}
