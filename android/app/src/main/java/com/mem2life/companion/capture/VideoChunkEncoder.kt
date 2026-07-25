package com.mem2life.companion.capture

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import android.os.Bundle
import android.util.Log
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val TAG = "Mem2Life:VideoEncoder"
private const val MIME_TYPE = "video/avc"
private const val I_FRAME_INTERVAL_SEC = 2
private const val BITRATE_BPS = 4_000_000

/**
 * Vuzix Blade 2 온보드 카메라(BladeCameraController)가 넘겨주는 tightly-packed
 * I420 YUV 프레임을 H.264로 인코딩하고, 업로드 API 계약이 요구하는 30초 mp4
 * 청크로 잘라 [outputDir]에 순서대로 기록한다.
 *
 * 내부 시간 도메인: 모든 프레젠테이션 타임스탬프는 "세션 시작 기준 상대 시각(us)"으로
 * 통일한다 — 첫 프레임의 presentationTimeUs를 0으로 잡고, 이후 모든 값에서
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

    // MediaCodec은 INFO_OUTPUT_FORMAT_CHANGED를 코덱 수명당 딱 한 번만 낸다.
    // 청크 로테이션으로 새 muxer를 열 때마다 이 이벤트를 다시 기다릴 수 없으므로
    // 최초 1회 받은 출력 포맷을 캐시해 두고, 2번째 청크부터는 이 포맷으로 즉시
    // 트랙을 추가/시작한다. (이게 없으면 seq>=1 청크가 전부 0바이트가 됐다.)
    private var cachedOutputFormat: MediaFormat? = null

    /** 현재 청크에 muxer로 실제 기록한 바이트 수(진단용 — 0이면 프레임/인코딩 경로 문제). */
    private var currentChunkBytesWritten: Long = 0

    private var currentSeq = 0
    private var currentChunkFile: File? = null
    /** 현재 청크의 시작 시각 — 세션 시작 기준 상대 us. */
    private var currentChunkStartRelativeUs: Long = 0
    private var pendingRotation = false

    private var sessionStartAbsoluteUs: Long = -1
    /** 인코더에 마지막으로 넣은 프레임의 상대 pts(us) — 세션 종료 시 마지막 청크 길이 계산용. */
    private var lastQueuedRelativeUs: Long = 0

    private var drainJob: Job? = null

    // BladeCameraController는 프레임마다 새 I420 배열을 할당해 넘기므로(Camera2
    // Image는 콜백 안에서 close되기 전에 이미 복사됨) 버퍼 재사용 걱정은 없다.
    // NV12가 필요한 인코더라면 큐에 넣기 전에 여기서 변환한다.
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
     * 카메라 컨트롤러에서 새 프레임이 올 때마다 호출한다(카메라 콜백 스레드에서 호출).
     * [i420]은 tightly-packed I420(Y, U, V 순서) 버퍼여야 하며, 호출자가 프레임마다
     * 새로 할당해 소유권을 넘긴다. 인코더가 NV12를 요구하면 여기서 즉시 변환한다.
     */
    fun onVideoFrame(i420: ByteArray, presentationTimeUs: Long) {
        if (!running) return
        val expectedSize = width * height * 3 / 2
        if (i420.size < expectedSize) {
            Log.w(TAG, "프레임 크기 불일치(expected>=$expectedSize, got=${i420.size}) — 드롭")
            return
        }
        val data =
            when (colorFormat) {
                MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar -> {
                    val convertBuffer = ByteArray(expectedSize)
                    YuvColorConverter.i420ToNv12(ByteBuffer.wrap(i420), width, height, convertBuffer)
                    convertBuffer
                }
                else -> i420
            }
        pendingFrames.offer(QueuedFrame(data, presentationTimeUs))
    }

    /**
     * 세션 종료 시 호출. 남은 프레임을 flush하고 마지막 청크를 마무리해 콜백한다.
     *
     * suspend인 이유: drainJob(드레인 코루틴)이 [codec]을 실제로 쓰는 유일한 다른
     * 스레드다. `cancel()`만으로는 즉시 멈추지 않아, 그 코루틴이 dequeueOutputBuffer를
     * 실행 중인데 이 함수가 encoder.stop()/release()를 호출하면 해제된 코덱에 접근해
     * IllegalStateException으로 프로세스가 죽는다. cancelAndJoin으로 드레인 코루틴이
     * 완전히 종료된 뒤에만 코덱을 정지/해제한다.
     */
    suspend fun stop() {
        running = false
        drainJob?.cancelAndJoin()
        drainJob = null

        val encoder = codec
        try {
            // byte-buffer 입력 모드에서는 EOS를 빈 입력 버퍼 + BUFFER_FLAG_END_OF_STREAM로
            // 알린다. signalEndOfInputStream()은 Surface 입력 모드 전용이라 여기서
            // 부르면 IllegalStateException이 난다(그래서 마지막 청크가 flush되지 않았다).
            encoder?.let { queueEndOfStream(it) }
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

    /** byte-buffer 입력 모드의 EOS 신호: 빈 입력 버퍼에 END_OF_STREAM 플래그를 실어 넣는다. */
    private fun queueEndOfStream(encoder: MediaCodec) {
        val inputIndex = encoder.dequeueInputBuffer(INPUT_TIMEOUT_US)
        if (inputIndex < 0) {
            Log.w(TAG, "EOS 입력 버퍼를 얻지 못함 — 마지막 프레임 일부가 누락될 수 있음")
            return
        }
        encoder.queueInputBuffer(
            inputIndex,
            0,
            0,
            lastQueuedRelativeUs,
            MediaCodec.BUFFER_FLAG_END_OF_STREAM,
        )
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
                    val format = encoder.outputFormat
                    cachedOutputFormat = format
                    val currentMuxer = muxer ?: return
                    if (!muxerStarted) {
                        muxerTrackIndex = currentMuxer.addTrack(format)
                        currentMuxer.start()
                        muxerStarted = true
                    }
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
                        currentChunkBytesWritten += bufferInfo.size
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
        val newMuxer = MediaMuxer(file.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        muxer = newMuxer
        muxerStarted = false
        // 2번째 이후 청크: 출력 포맷은 이미 알고 있으므로(캐시) 즉시 트랙 추가/시작.
        // 첫 청크는 아직 포맷을 모르므로 drainOutput의 INFO_OUTPUT_FORMAT_CHANGED에서 시작된다.
        val format = cachedOutputFormat
        if (format != null) {
            muxerTrackIndex = newMuxer.addTrack(format)
            newMuxer.start()
            muxerStarted = true
        }
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
        Log.i(TAG, "청크 마감: seq=$finishedSeq 기록바이트=$currentChunkBytesWritten")

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
        currentChunkBytesWritten = 0
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
