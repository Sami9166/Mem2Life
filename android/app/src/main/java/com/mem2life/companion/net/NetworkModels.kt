package com.mem2life.companion.net

import org.json.JSONObject

/**
 * 업로드 API 계약(루트 CLAUDE.md "업로드 API 계약 (android ↔ wiki-builder, v1 초안)")에
 * 대응하는 최소 데이터 모델. Retrofit/Moshi 대신 org.json만 사용해 의존성을 최소화했다
 * — 계약이 안정화되면 필요 시 교체 가능.
 */
data class SessionStartRequest(
    val title: String? = null,
    val participants: List<String>? = null,
) {
    fun toJson(): String {
        val json = JSONObject()
        title?.let { json.put("title", it) }
        participants?.let { json.put("participants", org.json.JSONArray(it)) }
        return json.toString()
    }
}

data class SessionStartResponse(
    val sessionId: String,
    val startedAt: String,
) {
    companion object {
        fun parse(body: String): SessionStartResponse {
            val json = JSONObject(body)
            return SessionStartResponse(
                sessionId = json.getString("session_id"),
                startedAt = json.optString("started_at"),
            )
        }
    }
}
