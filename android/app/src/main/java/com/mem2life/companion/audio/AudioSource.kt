package com.mem2life.companion.audio

import kotlinx.coroutines.CoroutineScope

/**
 * 마이크 오디오 소스 추상화.
 *
 * 중요: Meta DAT SDK(mwdat-camera/mwdat-core)는 오디오 캡처 API를 제공하지 않는다.
 * 실기기 마이크 접근은 SDK 밖에서 표준 Android Bluetooth HFP(Hands-Free Profile)
 * 경로로 이뤄진다 — [BluetoothScoAudioSource] 참고. Mock Device Kit 역시 카메라
 * 스트림/사진 캡처/권한/기기 상태만 시뮬레이션하고 마이크 입력은 시뮬레이션하지
 * 않으므로, 실기기 없이 오디오 파이프라인을 검증하려면 [MockPcmAudioSource]처럼
 * 별도의 목업 소스가 필요하다. 이 인터페이스가 그 둘을 교체 가능하게 만든다.
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
