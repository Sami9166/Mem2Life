package com.mem2life.companion.audio

import kotlin.math.roundToInt

/**
 * PCM 16-bit little-endian 모노 오디오를 선형 보간으로 리샘플링하는 순수 로직.
 * 안드로이드 프레임워크 의존 없음 — JVM 단위 테스트로 검증 가능.
 *
 * 왜 필요한가: 업로드 API 계약은 16kHz를 요구하지만("리샘플링 불필요"라고 적혀
 * 있는 건 STT 입력 포맷과 이미 같다는 전제다), 실제 글래스 마이크는 Bluetooth
 * HFP 프로파일을 통해 8kHz로만 캡처된다(CLAUDE.md 알려진 리스크 참고). 따라서
 * android 클라이언트가 8kHz -> 16kHz 업샘플링을 책임진다. 데모용 목업 오디오
 * 소스가 이미 16kHz라면 리샘플링은 필요 없다(같은 rate면 그대로 반환).
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
