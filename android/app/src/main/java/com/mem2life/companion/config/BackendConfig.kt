package com.mem2life.companion.config

/**
 * 백엔드(FastAPI, Mem2Life/backend) 접속 설정.
 *
 * 원칙(CLAUDE.md): 백엔드 호스트/포트는 하드코딩하지 않는다. 이 데이터 클래스는
 * 두 소스에서 채워진다:
 *   1) assets/backend_config.json — 앱 최초 설치 시 기본값 (데모 노트북 IP 등)
 *   2) SharedPreferences — 사용자가 설정 화면에서 덮어쓴 값 (데모 중 백엔드가
 *      다른 IP/포트로 뜨는 경우 재빌드 없이 대응하기 위함)
 *
 * [BackendConfigStore]가 위 두 소스를 병합해 이 클래스를 만든다.
 */
data class BackendConfig(
    val scheme: String,
    val host: String,
    val port: Int,
    val wsScheme: String,
) {
    val httpBaseUrl: String
        get() = "$scheme://$host:$port"

    val wsBaseUrl: String
        get() = "$wsScheme://$host:$port"

    fun sessionsStartUrl(): String = "$httpBaseUrl/sessions/start"

    fun videoChunksUrl(sessionId: String): String = "$httpBaseUrl/sessions/$sessionId/video-chunks"

    fun audioStreamWsUrl(sessionId: String): String = "$wsBaseUrl/sessions/$sessionId/audio-stream"

    fun sessionEndUrl(sessionId: String): String = "$httpBaseUrl/sessions/$sessionId/end"

    companion object {
        /** 앱이 한 번도 설정을 저장한 적 없을 때 쓰는 안전한 기본값(assets 로드 실패 시). */
        val FALLBACK =
            BackendConfig(scheme = "http", host = "10.0.2.2", port = 8000, wsScheme = "ws")
    }
}
