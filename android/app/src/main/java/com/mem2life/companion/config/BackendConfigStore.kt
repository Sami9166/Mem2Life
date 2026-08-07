package com.mem2life.companion.config

import android.content.Context
import android.util.Log
import org.json.JSONObject

private const val TAG = "Mem2Life:BackendConfig"
private const val PREFS_NAME = "mem2life_backend_config"
private const val KEY_SCHEME = "scheme"
private const val KEY_HOST = "host"
private const val KEY_PORT = "port"
private const val KEY_WS_SCHEME = "ws_scheme"
private const val KEY_RECALL_PORT = "recall_port"

/**
 * [BackendConfig]의 로드/저장을 담당한다.
 *
 * 우선순위: SharedPreferences(사용자가 설정 화면에서 바꾼 값) > assets 기본값 >
 * [BackendConfig.FALLBACK]. 설정 파일/화면을 통해서만 값을 바꿀 수 있고 코드에는
 * 백엔드 주소를 절대 하드코딩하지 않는다.
 */
class BackendConfigStore(private val context: Context) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun load(): BackendConfig {
        val default = loadAssetDefault()
        val scheme = prefs.getString(KEY_SCHEME, null) ?: default.scheme
        val host = prefs.getString(KEY_HOST, null) ?: default.host
        val port = if (prefs.contains(KEY_PORT)) prefs.getInt(KEY_PORT, default.port) else default.port
        val wsScheme = prefs.getString(KEY_WS_SCHEME, null) ?: default.wsScheme
        val recallPort =
            if (prefs.contains(KEY_RECALL_PORT)) prefs.getInt(KEY_RECALL_PORT, default.recallPort)
            else default.recallPort
        return BackendConfig(
            scheme = scheme,
            host = host,
            port = port,
            wsScheme = wsScheme,
            recallPort = recallPort,
        )
    }

    fun save(config: BackendConfig) {
        prefs.edit()
            .putString(KEY_SCHEME, config.scheme)
            .putString(KEY_HOST, config.host)
            .putInt(KEY_PORT, config.port)
            .putString(KEY_WS_SCHEME, config.wsScheme)
            .putInt(KEY_RECALL_PORT, config.recallPort)
            .apply()
    }

    private fun loadAssetDefault(): BackendConfig {
        return try {
            context.assets.open("backend_config.json").use { input ->
                val json = JSONObject(input.readBytes().decodeToString())
                BackendConfig(
                    scheme = json.optString("scheme", BackendConfig.FALLBACK.scheme),
                    host = json.optString("host", BackendConfig.FALLBACK.host),
                    port = json.optInt("port", BackendConfig.FALLBACK.port),
                    wsScheme = json.optString("wsScheme", BackendConfig.FALLBACK.wsScheme),
                    recallPort = json.optInt("recallPort", BackendConfig.FALLBACK.recallPort),
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "backend_config.json 로드 실패, 폴백 기본값 사용", e)
            BackendConfig.FALLBACK
        }
    }
}
