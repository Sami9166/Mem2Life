package com.mem2life.companion.audio

import android.content.Context
import android.util.Log
import kotlin.math.sin
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val TAG = "Mem2Life:MockAudio"

/**
 * 실기기 없이(Mock Device Kit으로 영상만 시뮬레이션되는 동안) 오디오 파이프라인을
 * 검증하기 위한 목업 소스.
 *
 * Meta Mock Device Kit은 카메라 피드/사진 캡처/권한/기기 상태만 시뮬레이션하고
 * 마이크(HFP) 입력은 시뮬레이션하지 않는다 — 이는 DAT SDK의 공식 문서에서 확인한
 * 사실이며 CLAUDE.md가 가정한 "Mock Device Kit으로 영상/음성 스트림 수신 개발"과
 * 어긋나는 부분이다(배경은 루트 CLAUDE.md "알려진 리스크 / 검증 대기" 절의
 * 확인됨 항목 참고). 그래서 오디오 쪽은 자체 목업이 필요하다.
 *
 * assets/mock_audio/sample_conversation_16k_mono.pcm (raw PCM16/16kHz/mono, 헤더 없음)이
 * 있으면 그것을 실시간 속도로 반복 재생하고, 없으면 440Hz 톤을 생성해 최소한
 * "프레임이 순서대로, 실시간 페이스로 도착한다"는 것만 검증한다.
 */
class MockPcmAudioSource(private val context: Context) : AudioSource {

    override val nativeSampleRateHz: Int = AudioCaptureConfig.TARGET_SAMPLE_RATE_HZ

    private var job: Job? = null

    override suspend fun start(scope: CoroutineScope, onPcmFrame: (ByteArray) -> Unit): Boolean {
        val assetBytes = tryLoadAssetPcm()
        job =
            scope.launch(Dispatchers.Default) {
                val frameBytes = AudioCaptureConfig.TARGET_FRAME_SIZE_BYTES
                var toneSamplePhase = 0.0
                var assetOffset = 0
                while (isActive) {
                    val frame =
                        if (assetBytes != null && assetBytes.isNotEmpty()) {
                            val frame = ByteArray(frameBytes)
                            for (i in 0 until frameBytes) {
                                frame[i] = assetBytes[assetOffset % assetBytes.size]
                                assetOffset++
                            }
                            frame
                        } else {
                            val frame = ByteArray(frameBytes)
                            val sampleCount = frameBytes / 2
                            for (i in 0 until sampleCount) {
                                val value =
                                    (sin(toneSamplePhase) * TONE_AMPLITUDE).toInt().toShort()
                                frame[i * 2] = (value.toInt() and 0xFF).toByte()
                                frame[i * 2 + 1] = ((value.toInt() shr 8) and 0xFF).toByte()
                                toneSamplePhase +=
                                    2.0 * Math.PI * TONE_FREQUENCY_HZ / AudioCaptureConfig.TARGET_SAMPLE_RATE_HZ
                            }
                            frame
                        }
                    onPcmFrame(frame)
                    delay(AudioCaptureConfig.FRAME_DURATION_MS)
                }
            }
        Log.i(
            TAG,
            "MockPcmAudioSource 시작 (source=${if (assetBytes != null) "asset" else "generated tone"})",
        )
        return true
    }

    override fun stop() {
        job?.cancel()
        job = null
    }

    private fun tryLoadAssetPcm(): ByteArray? {
        return try {
            context.assets.open(MOCK_AUDIO_ASSET_PATH).use { it.readBytes() }
        } catch (e: Exception) {
            null
        }
    }

    companion object {
        const val MOCK_AUDIO_ASSET_PATH = "mock_audio/sample_conversation_16k_mono.pcm"
        private const val TONE_FREQUENCY_HZ = 440.0
        private const val TONE_AMPLITUDE = 6000.0
    }
}
