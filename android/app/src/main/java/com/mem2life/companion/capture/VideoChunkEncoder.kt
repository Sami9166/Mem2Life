package com.mem2life.companion.capture

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import android.os.Bundle
import android.util.Log
import com.meta.wearable.dat.camera.types.VideoFrame
import java.io.File
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val TAG = "Mem2Life:VideoEncoder"
private const val MIME_TYPE = "video/avc"
private const val I_FRAME_INTERVAL_SEC = 2
private const val BITRATE_BPS = 4_000_000

/**
 * DAT `Stream.videoStream`에서 받은 [VideoFrame](원시 I420 YUV)을 H.264로 인코딩하고,
 * 업로드 API 계약이 요구하는 30초 mp4 청크로 잘라 [outputDir]에 순서대로 기록한다.
 *
 * 내부 시간 도메인: 모든 프레젠테이션 타임스탬프는 "세션 시작 기준 상대 시각(us)"으로
 * 통일한다 — 첫 프레임의 [VideoFrame.presentationTimeUs]를 0으로 잡고, 이후 모든 값에서
 * 그 값을 빼서 인코더에 넘긴다. 그래야 청크의 `startTsSec`가 계약이 요구하는
 * "세션 시작 기준 초(start_ts)"와 정확히 같아진다.
 *
 * 청크 경계는 GOP 키프레임에 맞춘다: 목표 시간을 넘기면 인코더에 싱크 프레임을
 * 요청하고, 그 싱크 프레임이 나오는 시점에 이전 MediaMuxer를 닫고 새 파일을 연다
 * — 그래야 각 mp4 조각이 자체적으로 디코딩 가능하다(중간 P프레임부터 시작하는
 * 파일은 재생/분석이 불가능하기 때문).
 *
 * 주의(1단계 검증 필요 사항): MediaCodec 버퍼 입력 모드는 기기별로 지원 컬러
 * 포맷(NV12 세미플레인 vs I420 플레인)이 다르다. [YuvColorConverter]로 변환하지만
 * 실기기 인코더 조합 호환성은 아직 검증되지 않았다 — 실기기 통합 단계에서 재확인할 것.
 */
class VideoChunkEncoder(
    private val outputDir: File,
    private val width: Int,
    private val height: Int,
    private val frameRateFps: Int,
    private val chunkDurationSec: Double,
    private val onChunkReady: (ChunkFile) -> Unit,
) {
    private var codec: MediaCodec? = null
    private var colorFormat: Int = MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible
    private var muxer: MediaMuxer? = null
    private var muxerTrackIndex = -1
    private var muxerStarted = false

    private var currentSeq = 0
    private var currentChunkFile: File? = null
    /** 현재 청크의 시작 시각 — 세션 시작 기준 상대 us. */
    private var currentChunkStartRelativeUs: Long = 0
    private var pendingRotation = false

    private var sessionStartAbsoluteUs: Long = -1
    /** 인코더에 마지막으로 넣은 프레임의 상대 pts(us) — 세션 종료 시 마지막 청크 길이 계산용. */
    private var lastQueuedRelativeUs: Long = 0

    private var drainJob: Job? = null

    // DAT의 VideoFrame.buffer는 SDK 내부에서 재사용되는 버퍼일 수 있으므로(공식
    // 샘플도 콜백에서 즉시 변환한다), 큐에 넣기 전에 인코더가 원하는 포맷으로 즉시
    // 복사/변환해 둔다 — 드레인 스레드가 나중에 처리할 때 버퍼가 덮어써지는 것을 방지.
    private val pendingFrames = LinkedBlockingQueue<QueuedFrame>()
    @Volatile private var running = false

    private data class QueuedFrame(val data: ByteArray, val presentationTimeUs: Long)

    fun start(scope: CoroutineScope) {
        outputDir.mkdirs()
        val format =
            MediaFormat.createVideoFormat(MIME_TYPE, width, height).apply {
                setInteger(MediaFormat.KEY_BIT_RATE, BITRATE_BPS)
                setInteger(MediaFormat.KEY_FRAME_RATE, frameRateFps)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, I_FRAME_INTERVAL_SEC)
            }

        val encoder = MediaCodec.createEncoderByType(MIME_TYPE)
        colorFormat = pickSupportedColorFormat(encoder)
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT, colorFormat)
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        encoder.start()
        codec = encoder
        running = true

        drainJob =
            scope.launch(Dispatchers.IO) {
                while (isActive && running) {
                    val frame = pendingFrames.poll(200, TimeUnit.MILLISECONDS)
                    if (frame != null) {
                        encodeOneFrame(encoder, frame)
                    }
                    drainOutput(encoder)
                }
            }
    }

    /**
     * DAT `Stream.videoStream`에서 새 프레임이 올 때마다 호출한다(수집 코루틴에서 호출).
     * 여기서 즉시 인코더 컬러 포맷으로 변환/복사해 큐에 넣는다(버퍼 재사용 대비).
     */
    fun onVideoFrame(frame: VideoFrame) {
        if (!running) return
        val expectedSize = width * height * 3 / 2
        val convertBuffer = ByteArray(expectedSize)
        when (colorFormat) {
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar ->
                YuvColorConverter.i420ToNv12(frame.buffer, width, height, convertBuffer)
            else -> YuvColorConverter.copyI420(frame.buffer, width, height, convertBuffer)
        }
        pendingFrames.offer(QueuedFrame(convertBuffer, frame.presentationTimeUs))
    }

    /** 세션 종료 시 호출. 남은 프레임을 flush하고 마지막 청크를 마무리해 콜백한다. */
    fun stop() {
        running = false
        drainJob?.cancel()
        drainJob = null

        val encoder = codec
        try {
            encoder?.signalEndOfInputStream()
            encoder?.let { drainOutput(it, endOfStream = true) }
            encoder?.stop()
            encoder?.release()
        } catch (e: Exception) {
            Log.w(TAG, "인코더 정지 중 오류(무시하고 계속 진행)", e)
        }
        codec = null

        finalizeMuxerOnly()

        val finishedFile = currentChunkFile
        if (finishedFile != null && finishedFile.exists() && finishedFile.length() > 0) {
            val durationSec = (lastQueuedRelativeUs - currentChunkStartRelativeUs) / 1_000_000.0
            onChunkReady(
                ChunkFile(
                    file = finishedFile,
                    seq = currentSeq,
                    startTsSec = currentChunkStartRelativeUs / 1_000_000.0,
                    durationSec = durationSec.coerceAtLeast(0.0),
                ),
            )
        }
        currentChunkFile = null
    }

    private fun pickSupportedColorFormat(encoder: MediaCodec): Int {
        val capabilities = encoder.codecInfo.getCapabilitiesForType(MIME_TYPE)
        val supported = capabilities.colorFormats.toSet()
        return when {
            supported.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar) ->
                MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar
            supported.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar) ->
                MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar
            else -> MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible
        }
    }

    private fun encodeOneFrame(encoder: MediaCodec, frame: QueuedFrame) {
        if (sessionStartAbsoluteUs < 0) {
            sessionStartAbsoluteUs = frame.presentationTimeUs
            currentChunkStartRelativeUs = 0
            startNewChunk()
        }

        val relativeUs = frame.presentationTimeUs - sessionStartAbsoluteUs
        lastQueuedRelativeUs = relativeUs

        val elapsedInChunkSec = (relativeUs - currentChunkStartRelativeUs) / 1_000_000.0
        if (!pendingRotation && elapsedInChunkSec >= chunkDurationSec) {
            // 다음 싱크 프레임에서 청크를 자르도록 요청한다.
            val params = Bundle()
            params.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0)
            encoder.setParameters(params)
            pendingRotation = true
        }

        val inputIndex = encoder.dequeueInputBuffer(INPUT_TIMEOUT_US)
        if (inputIndex < 0) {
            Log.w(TAG, "인코더 입력 버퍼 없음(프레임 드롭, seq=$currentSeq)")
            return
        }
        val inputBuffer = encoder.getInputBuffer(inputIndex) ?: return
        inputBuffer.clear()
        inputBuffer.put(frame.data)

        encoder.queueInputBuffer(inputIndex, 0, frame.data.size, relativeUs, 0)
    }

    private fun drainOutput(encoder: MediaCodec, endOfStream: Boolean = false) {
        val bufferInfo = MediaCodec.BufferInfo()
        while (true) {
            val outputIndex = encoder.dequeueOutputBuffer(bufferInfo, OUTPUT_TIMEOUT_US)
            when {
                outputIndex == MediaCodec.INFO_TRY_AGAIN_LATER -> {
                    if (!endOfStream) return
                }
                outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    val currentMuxer = muxer ?: return
                    muxerTrackIndex = currentMuxer.addTrack(encoder.outputFormat)
                    currentMuxer.start()
                    muxerStarted = true
                }
                outputIndex >= 0 -> {
                    val isKeyFrame = (bufferInfo.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0
                    if (pendingRotation && isKeyFrame && bufferInfo.size > 0) {
                        rotateChunk(bufferInfo.presentationTimeUs)
                    }

                    val outputBuffer = encoder.getOutputBuffer(outputIndex)
                    val isConfig = (bufferInfo.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0
                    if (outputBuffer != null && bufferInfo.size > 0 && !isConfig && muxerStarted) {
                        muxer?.writeSampleData(muxerTrackIndex, outputBuffer, bufferInfo)
                    }
                    encoder.releaseOutputBuffer(outputIndex, false)
                    if (endOfStream && (bufferInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        return
                    }
                }
                else -> return
            }
        }
    }

    private fun startNewChunk() {
        val fileName = "chunk_%06d.mp4".format(currentSeq)
        val file = File(outputDir, fileName)
        currentChunkFile = file
        muxer = MediaMuxer(file.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        muxerStarted = false
        Log.i(
            TAG,
            "새 청크 시작: seq=$currentSeq file=${file.name} startTs=${currentChunkStartRelativeUs / 1_000_000.0}s",
        )
    }

    private fun rotateChunk(newChunkFirstFrameRelativeUs: Long) {
        val finishedFile = currentChunkFile
        val finishedSeq = currentSeq
        val finishedStartTsSec = currentChunkStartRelativeUs / 1_000_000.0
        val finishedDurationSec =
            (newChunkFirstFrameRelativeUs - currentChunkStartRelativeUs) / 1_000_000.0

        finalizeMuxerOnly()

        if (finishedFile != null) {
            onChunkReady(
                ChunkFile(
                    file = finishedFile,
                    seq = finishedSeq,
                    startTsSec = finishedStartTsSec,
                    durationSec = finishedDurationSec,
                ),
            )
        }

        currentSeq += 1
        pendingRotation = false
        currentChunkStartRelativeUs = newChunkFirstFrameRelativeUs
        startNewChunk()
    }

    private fun finalizeMuxerOnly() {
        try {
            if (muxerStarted) {
                muxer?.stop()
            }
            muxer?.release()
        } catch (e: Exception) {
            Log.w(TAG, "MediaMuxer 종료 중 오류(파일이 비어있을 수 있음)", e)
        }
        muxer = null
        muxerStarted = false
    }

    companion object {
        private const val INPUT_TIMEOUT_US = 10_000L
        private const val OUTPUT_TIMEOUT_US = 10_000L
    }
}
