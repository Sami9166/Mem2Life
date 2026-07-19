package com.mem2life.companion.audio

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val TAG = "Mem2Life:BtScoAudio"

/**
 * 실기기 글래스 마이크 오디오 소스.
 *
 * DAT SDK 밖에서 표준 Android Bluetooth HFP(Hands-Free Profile) 경로로 캡처한다
 * (com.meta.wearable가 아니라 android.media API). 공식 DAT 문서("Use device
 * microphones and speakers")에 따르면:
 *   - HFP는 양방향이지만 8kHz mono로 고정된다 (A2DP처럼 고음질 재생 전용 경로와는
 *     다름 — CLAUDE.md의 "글래스 마이크 8kHz 모노" 리스크가 바로 이것)
 *   - 착용자 목소리 위주 빔포밍이 적용되어 상대방 목소리가 작게 들어올 수 있음
 *   - DAT 카메라 스트림과 HFP를 함께 쓸 때는 "HFP를 먼저 설정하고 안정화를 기다린
 *     뒤 DAT 스트림을 시작"하는 순서를 지켜야 한다 (RecordingSessionController에서
 *     보장한다)
 *
 * 8kHz로 캡처한 프레임은 [PcmResampler]로 16kHz로 업샘플링해 계약을 만족시킨다.
 */
class BluetoothScoAudioSource(private val context: Context) : AudioSource {

    override val nativeSampleRateHz: Int = NATIVE_SAMPLE_RATE_HZ

    private val audioManager: AudioManager by lazy {
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    }

    private var audioRecord: AudioRecord? = null
    private var captureJob: Job? = null
    private var previousCommunicationDevice: AudioDeviceInfo? = null

    override suspend fun start(scope: CoroutineScope, onPcmFrame: (ByteArray) -> Unit): Boolean {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            Log.e(TAG, "RECORD_AUDIO 권한 없음 — 오디오 캡처 불가")
            return false
        }

        if (!routeToBluetoothSco()) {
            Log.e(TAG, "Bluetooth SCO(HFP) 라우팅 실패 — 연결된 글래스가 없거나 HFP 미지원")
            return false
        }

        // HFP 라우트가 안정화될 시간을 준다 (llms.txt 가이드: DAT 스트림 시작 전
        // 마이크를 먼저 설정하고 라우트가 자리잡을 때까지 대기).
        delay(HFP_ROUTE_SETTLE_DELAY_MS)

        val minBufferSize =
            AudioRecord.getMinBufferSize(
                NATIVE_SAMPLE_RATE_HZ,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
        if (minBufferSize <= 0) {
            Log.e(TAG, "AudioRecord.getMinBufferSize 실패: $minBufferSize")
            return false
        }

        val record =
            try {
                @Suppress("MissingPermission")
                AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                    NATIVE_SAMPLE_RATE_HZ,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    minBufferSize * 4,
                )
            } catch (e: SecurityException) {
                Log.e(TAG, "AudioRecord 생성 실패(권한)", e)
                return false
            }

        if (record.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "AudioRecord 초기화 실패")
            record.release()
            return false
        }

        audioRecord = record
        record.startRecording()

        captureJob =
            scope.launch(Dispatchers.IO) {
                val nativeFrameBytes =
                    (NATIVE_SAMPLE_RATE_HZ * AudioCaptureConfig.FRAME_DURATION_MS / 1000).toInt() *
                        AudioCaptureConfig.BYTES_PER_SAMPLE
                val buffer = ByteArray(nativeFrameBytes)
                while (isActive) {
                    val read = record.read(buffer, 0, buffer.size)
                    if (read > 0) {
                        val captured = if (read == buffer.size) buffer else buffer.copyOf(read)
                        val resampled =
                            PcmResampler.resamplePcm16(
                                captured,
                                inputRateHz = NATIVE_SAMPLE_RATE_HZ,
                                outputRateHz = AudioCaptureConfig.TARGET_SAMPLE_RATE_HZ,
                            )
                        onPcmFrame(resampled)
                    } else if (read < 0) {
                        Log.e(TAG, "AudioRecord.read 오류 코드: $read")
                    }
                }
            }
        return true
    }

    override fun stop() {
        captureJob?.cancel()
        captureJob = null
        audioRecord?.let {
            try {
                it.stop()
            } catch (e: IllegalStateException) {
                Log.w(TAG, "AudioRecord.stop 실패(이미 정지 상태일 수 있음)", e)
            }
            it.release()
        }
        audioRecord = null
        restoreAudioRoute()
    }

    private fun routeToBluetoothSco(): Boolean {
        val devices = audioManager.availableCommunicationDevices
        val scoDevice = devices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO }
        if (scoDevice == null) {
            Log.w(TAG, "TYPE_BLUETOOTH_SCO 디바이스를 찾지 못함 (사용 가능: ${devices.map { it.type }})")
            return false
        }
        previousCommunicationDevice = audioManager.communicationDevice
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        return audioManager.setCommunicationDevice(scoDevice)
    }

    private fun restoreAudioRoute() {
        audioManager.clearCommunicationDevice()
        audioManager.mode = AudioManager.MODE_NORMAL
        previousCommunicationDevice = null
    }

    companion object {
        /** HFP 프로파일은 8kHz 모노로 고정 (CLAUDE.md 알려진 리스크와 일치). */
        const val NATIVE_SAMPLE_RATE_HZ = 8_000
        private const val HFP_ROUTE_SETTLE_DELAY_MS = 2_000L
    }
}
