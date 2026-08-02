package com.mem2life.companion.audio

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val TAG = "Mem2Life:DeviceMic"

/**
 * Vuzix Blade 2 온보드 마이크 오디오 소스.
 *
 * 앱이 글래스 위에서 직접 실행되므로 표준 [AudioRecord]로 기기 마이크를 바로
 * 캡처한다 — 과거 Meta Ray-Ban 설계의 Bluetooth HFP/SCO 경로(8kHz 고정,
 * BluetoothScoAudioSource)는 Blade 2 전환으로 폐기됐다.
 *
 * 계약이 요구하는 16kHz/mono/PCM16으로 직접 캡처를 시도하고, 기기가 16kHz
 * 캡처를 지원하지 않는 예외적인 경우에만 48kHz(→ 안 되면 44.1kHz)로 캡처해
 * [PcmResampler]로 다운샘플링한다. Blade 2는 16kHz를 지원하므로 정상 경로에서는
 * 리샘플링이 일어나지 않는다.
 *
 * 오디오 소스는 VOICE_RECOGNITION을 쓴다 — 백엔드 파이프라인의 입력이 STT이고,
 * 이 소스가 AGC/필터 개입이 가장 적은 원음에 가까운 신호를 준다.
 */
class DeviceMicAudioSource(private val context: Context) : AudioSource {

    override var nativeSampleRateHz: Int = AudioCaptureConfig.TARGET_SAMPLE_RATE_HZ
        private set

    private var audioRecord: AudioRecord? = null
    private var captureJob: Job? = null

    override suspend fun start(scope: CoroutineScope, onPcmFrame: (ByteArray) -> Unit): Boolean {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            Log.e(TAG, "RECORD_AUDIO 권한 없음 — 오디오 캡처 불가")
            return false
        }

        val record = createRecordWithFallback() ?: return false
        audioRecord = record
        nativeSampleRateHz = record.sampleRate
        record.startRecording()
        Log.i(TAG, "온보드 마이크 캡처 시작 (native=${record.sampleRate}Hz)")

        captureJob =
            scope.launch(Dispatchers.IO) {
                val nativeFrameBytes =
                    (record.sampleRate * AudioCaptureConfig.FRAME_DURATION_MS / 1000).toInt() *
                        AudioCaptureConfig.BYTES_PER_SAMPLE
                val buffer = ByteArray(nativeFrameBytes)
                while (isActive) {
                    val read = record.read(buffer, 0, buffer.size)
                    if (read > 0) {
                        val captured = if (read == buffer.size) buffer.copyOf() else buffer.copyOf(read)
                        val frame =
                            PcmResampler.resamplePcm16(
                                captured,
                                inputRateHz = record.sampleRate,
                                outputRateHz = AudioCaptureConfig.TARGET_SAMPLE_RATE_HZ,
                            )
                        onPcmFrame(frame)
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
    }

    /** 16kHz 우선, 실패 시 48kHz → 44.1kHz 순으로 폴백해 AudioRecord를 만든다. */
    private fun createRecordWithFallback(): AudioRecord? {
        for (rate in CANDIDATE_SAMPLE_RATES_HZ) {
            val minBufferSize =
                AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            if (minBufferSize <= 0) continue
            val record =
                try {
                    @Suppress("MissingPermission")
                    AudioRecord(
                        MediaRecorder.AudioSource.VOICE_RECOGNITION,
                        rate,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT,
                        minBufferSize * 4,
                    )
                } catch (e: Exception) {
                    Log.w(TAG, "AudioRecord 생성 실패(rate=${rate}Hz)", e)
                    continue
                }
            if (record.state == AudioRecord.STATE_INITIALIZED) {
                return record
            }
            record.release()
            Log.w(TAG, "AudioRecord 초기화 실패(rate=${rate}Hz) — 다음 후보로 폴백")
        }
        Log.e(TAG, "모든 후보 샘플레이트에서 AudioRecord 초기화 실패")
        return null
    }

    companion object {
        private val CANDIDATE_SAMPLE_RATES_HZ =
            intArrayOf(AudioCaptureConfig.TARGET_SAMPLE_RATE_HZ, 48_000, 44_100)
    }
}
