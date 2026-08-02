package com.mem2life.companion.audio

import kotlin.math.roundToInt

/**
 * PCM 16-bit little-endian 모노 오디오를 선형 보간으로 리샘플링하는 순수 로직.
 * 안드로이드 프레임워크 의존 없음 — JVM 단위 테스트로 검증 가능.
 *
 * 왜 필요한가: 업로드 API 계약은 16kHz를 요구한다. Vuzix Blade 2 온보드 마이크는
 * AudioRecord로 16kHz 직접 캡처가 가능해 정상 경로에서는 리샘플링이 일어나지
 * 않지만(같은 rate면 그대로 반환), 16kHz 캡처가 안 되는 예외적인 기기/에뮬레이터
 * 구성에서 DeviceMicAudioSource가 48kHz/44.1kHz로 폴백 캡처한 뒤 이 리샘플러로
 * 16kHz로 맞춘다. (과거 Meta Ray-Ban 설계의 HFP 8kHz -> 16kHz 업샘플링 용도에서
 * 폴백 용도로 역할이 바뀌었다. 선형 보간이라 다운샘플링 시 안티에일리어싱 필터는
 * 없지만, 폴백 경로 + 음성 대역 STT 입력이라는 용도에서는 허용 범위다.)
 */
object PcmResampler {

    fun resamplePcm16(
        input: ByteArray,
        inputRateHz: Int,
        outputRateHz: Int,
    ): ByteArray {
        if (inputRateHz == outputRateHz || input.isEmpty()) return input

        val inputSamples = bytesToShorts(input)
        val outputSampleCount =
            ((inputSamples.size.toLong() * outputRateHz) / inputRateHz).toInt().coerceAtLeast(0)
        val outputSamples = ShortArray(outputSampleCount)

        val ratio = inputRateHz.toDouble() / outputRateHz.toDouble()
        for (i in 0 until outputSampleCount) {
            val srcPos = i * ratio
            val srcIndex = srcPos.toInt()
            val frac = srcPos - srcIndex
            val s0 = inputSamples.getOrElse(srcIndex) { inputSamples.lastOrNull() ?: 0 }
            val s1 = inputSamples.getOrElse(srcIndex + 1) { s0 }
            outputSamples[i] = (s0 + (s1 - s0) * frac).roundToInt().toShort()
        }
        return shortsToBytes(outputSamples)
    }

    private fun bytesToShorts(bytes: ByteArray): ShortArray {
        val shorts = ShortArray(bytes.size / 2)
        for (i in shorts.indices) {
            val lo = bytes[i * 2].toInt() and 0xFF
            val hi = bytes[i * 2 + 1].toInt()
            shorts[i] = ((hi shl 8) or lo).toShort()
        }
        return shorts
    }

    private fun shortsToBytes(shorts: ShortArray): ByteArray {
        val bytes = ByteArray(shorts.size * 2)
        for (i in shorts.indices) {
            val v = shorts[i].toInt()
            bytes[i * 2] = (v and 0xFF).toByte()
            bytes[i * 2 + 1] = ((v shr 8) and 0xFF).toByte()
        }
        return bytes
    }
}
