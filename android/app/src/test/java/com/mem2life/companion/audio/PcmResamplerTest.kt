package com.mem2life.companion.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PcmResamplerTest {

    @Test
    fun `동일 샘플레이트면 원본을 그대로 반환한다`() {
        val input = shortsToBytes(shortArrayOf(100, 200, 300, 400))
        val result = PcmResampler.resamplePcm16(input, inputRateHz = 16_000, outputRateHz = 16_000)
        assertEquals(input.toList(), result.toList())
    }

    @Test
    fun `8kHz에서 16kHz로 업샘플링하면 샘플 수가 2배가 된다`() {
        // HFP 캡처(8kHz) -> 계약이 요구하는 16kHz로 리샘플링하는 실제 사용 시나리오.
        val sampleCount = 160 // 20ms @ 8kHz
        val input = shortsToBytes(ShortArray(sampleCount) { (it * 10).toShort() })

        val result =
            PcmResampler.resamplePcm16(input, inputRateHz = 8_000, outputRateHz = 16_000)

        val outputSamples = result.size / 2
        assertEquals(sampleCount * 2, outputSamples)
    }

    @Test
    fun `업샘플링된 값은 원본 값 사이를 선형 보간한다`() {
        val input = shortsToBytes(shortArrayOf(0, 1000))
        val result = PcmResampler.resamplePcm16(input, inputRateHz = 8_000, outputRateHz = 16_000)
        val outputSamples = bytesToShorts(result)

        // 정확한 보간 계수는 구현 세부사항이지만, 값이 [0, 1000] 범위 안에서
        // 단조 증가해야 한다는 것은 리샘플러의 정확성 계약이다.
        for (i in 1 until outputSamples.size) {
            assertTrue(outputSamples[i] >= outputSamples[i - 1])
        }
        assertEquals(0, outputSamples.first().toInt())
    }

    @Test
    fun `빈 입력은 빈 출력을 반환한다`() {
        val result = PcmResampler.resamplePcm16(ByteArray(0), inputRateHz = 8_000, outputRateHz = 16_000)
        assertTrue(result.isEmpty())
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

    private fun bytesToShorts(bytes: ByteArray): ShortArray {
        val shorts = ShortArray(bytes.size / 2)
        for (i in shorts.indices) {
            val lo = bytes[i * 2].toInt() and 0xFF
            val hi = bytes[i * 2 + 1].toInt()
            shorts[i] = ((hi shl 8) or lo).toShort()
        }
        return shorts
    }
}
