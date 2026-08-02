package com.mem2life.companion.audio

import kotlinx.coroutines.CoroutineScope

/**
 * 마이크 오디오 소스 추상화.
 *
 * Vuzix Blade 2에서는 앱이 글래스 위에서 직접 실행되므로 온보드 마이크를 표준
 * [android.media.AudioRecord]로 캡처하는 [DeviceMicAudioSource]가 실기기 경로다.
 * 마이크가 불안정한 개발 환경(일부 에뮬레이터 구성 등)에서 오디오 파이프라인만
 * 검증하려면 [MockPcmAudioSource](목업 톤/에셋 PCM 반복 재생)를 쓴다.
 * 이 인터페이스가 그 둘을 교체 가능하게 만든다.
 */
interface AudioSource {
    /** 이 소스가 실제로 캡처하는 원본 샘플레이트(리샘플링 전). */
    val nativeSampleRateHz: Int

    /**
     * 캡처를 시작한다. [onPcmFrame]은 PCM 16-bit/16kHz/mono로 리샘플링된 프레임을
     * 캡처 순서대로 전달한다(계약: 프레임 순서 = 전송 순서, 재조립 번호 없음).
     * 시작에 실패하면 false를 반환한다.
     */
    suspend fun start(scope: CoroutineScope, onPcmFrame: (ByteArray) -> Unit): Boolean

    fun stop()
}
