package com.mem2life.companion.audio

/** 업로드 API 계약이 요구하는 오디오 프레임 포맷: PCM 16-bit, 16kHz, mono. */
object AudioCaptureConfig {
    const val TARGET_SAMPLE_RATE_HZ = 16_000
    const val BYTES_PER_SAMPLE = 2 // PCM 16-bit
    const val FRAME_DURATION_MS = 20L

    /** 16kHz 기준 20ms 프레임의 바이트 수 (320 samples * 2 bytes). */
    val TARGET_FRAME_SIZE_BYTES: Int =
        (TARGET_SAMPLE_RATE_HZ * FRAME_DURATION_MS / 1000).toInt() * BYTES_PER_SAMPLE
}
