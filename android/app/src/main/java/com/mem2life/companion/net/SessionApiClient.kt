package com.mem2life.companion.net

import android.util.Log
import com.mem2life.companion.config.BackendConfig
import java.io.File
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody

private const val TAG = "Mem2Life:SessionApi"

/**
 * 업로드 API 계약(루트 CLAUDE.md "업로드 API 계약 (android ↔ wiki-builder, v1 초안)")의
 * HTTP 엔드포인트 3개를 감싼 클라이언트. 함수 하나당 엔드포인트 하나로 대응한다:
 *   - [startSession]    -> POST /sessions/start
 *   - [uploadVideoChunk] -> POST /sessions/{session_id}/video-chunks
 *   - [endSession]      -> POST /sessions/{session_id}/end
 * (오디오 스트림 WS 엔드포인트는 계약에 있지만 이 클라이언트가 아니라 [AudioStreamSocket]이
 * 담당한다 — HTTP와 WebSocket을 같은 클래스에 섞지 않기 위해 분리했다.)
 *
 * 계약이 바뀌면 이 파일의 URL 빌더([BackendConfig])와 요청/응답 필드([NetworkModels.kt])를
 * 함께 갱신해야 한다.
 *
 * 1단계 시점 기준 실제 수신 서버(wiki-builder 담당)는 아직 없다 — 이 클라이언트는
 * `Mem2Life/android/tools/mock-backend/`의 목업 서버를 상대로 검증했다.
 */
class SessionApiClient(private val configProvider: () -> BackendConfig) : VideoChunkUploader {

    private val client =
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()

    suspend fun startSession(request: SessionStartRequest): Result<SessionStartResponse> =
        withContext(Dispatchers.IO) {
            runCatching {
                val config = configProvider()
                val body = request.toJson().toRequestBody("application/json".toMediaType())
                val httpRequest = Request.Builder().url(config.sessionsStartUrl()).post(body).build()
                client.newCall(httpRequest).execute().use { response ->
                    if (!response.isSuccessful) {
                        error("POST /sessions/start 실패: HTTP ${response.code}")
                    }
                    val responseBody = response.body?.string().orEmpty()
                    SessionStartResponse.parse(responseBody)
                }
            }.onFailure { Log.e(TAG, "startSession 실패", it) }
        }

    /**
     * 30초 영상 청크 하나를 multipart/form-data로 업로드한다.
     * 필드명은 계약과 정확히 일치해야 한다: chunk(mp4), seq, start_ts, duration_sec.
     */
    override suspend fun uploadVideoChunk(
        sessionId: String,
        chunkFile: File,
        seq: Int,
        startTsSec: Double,
        durationSec: Double,
    ): Result<Unit> =
        withContext(Dispatchers.IO) {
            runCatching {
                val config = configProvider()
                val multipart =
                    MultipartBody.Builder()
                        .setType(MultipartBody.FORM)
                        .addFormDataPart("seq", seq.toString())
                        .addFormDataPart("start_ts", startTsSec.toString())
                        .addFormDataPart("duration_sec", durationSec.toString())
                        .addFormDataPart(
                            "chunk",
                            chunkFile.name,
                            chunkFile.asRequestBody("video/mp4".toMediaType()),
                        )
                        .build()
                val httpRequest =
                    Request.Builder()
                        .url(config.videoChunksUrl(sessionId))
                        .post(multipart)
                        .build()
                client.newCall(httpRequest).execute().use { response ->
                    if (!response.isSuccessful) {
                        error("POST video-chunks(seq=$seq) 실패: HTTP ${response.code}")
                    }
                }
            }.onFailure { Log.w(TAG, "uploadVideoChunk(seq=$seq) 실패, 큐에서 재시도 예정", it) }
        }

    suspend fun endSession(sessionId: String): Result<Unit> =
        withContext(Dispatchers.IO) {
            runCatching {
                val config = configProvider()
                val httpRequest =
                    Request.Builder()
                        .url(config.sessionEndUrl(sessionId))
                        .post(ByteArray(0).toRequestBody(null))
                        .build()
                client.newCall(httpRequest).execute().use { response ->
                    if (!response.isSuccessful) {
                        error("POST /sessions/$sessionId/end 실패: HTTP ${response.code}")
                    }
                }
            }.onFailure { Log.e(TAG, "endSession 실패", it) }
        }
}
