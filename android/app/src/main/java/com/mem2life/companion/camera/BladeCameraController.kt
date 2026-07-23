package com.mem2life.companion.camera

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Range
import androidx.core.content.ContextCompat
import java.util.concurrent.Executor
import kotlin.math.min
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull

private const val TAG = "Mem2Life:BladeCamera"
private const val SESSION_START_TIMEOUT_MS = 15_000L
private const val MAX_READER_IMAGES = 4

/**
 * Vuzix Blade 2 온보드 카메라 캡처 컨트롤러.
 *
 * Blade 2는 Android 11(API 30)이 탑재된 독립 실행형 기기라 이 앱이 글래스 위에서
 * 직접 실행된다 — 과거 Meta Ray-Ban 설계처럼 폰 컴패니언 앱이 DAT SDK로 원격
 * 스트림을 받는 구조가 아니다. Vuzix 공식 가이드에 따라 표준 Camera2 API로
 * 온보드 8MP 카메라에서 YUV_420_888 프레임을 받아, 인코더가 소비하는 tightly-packed
 * I420 버퍼로 변환해 [onVideoFrame] 콜백으로 넘긴다.
 *
 * 기존 WearablesGlassesController(DAT 세션/스트림)와 동일한 외부 계약을 유지한다:
 *   - suspend start → Result (타임아웃 포함)
 *   - 프레임 콜백 + 예기치 않은 종료 콜백
 *   - stop은 몇 번 불려도 안전(idempotent)
 * 그래서 RecordingSessionController의 오케스트레이션 구조는 그대로다.
 */
class BladeCameraController(private val context: Context) {

    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var cameraThread: HandlerThread? = null
    private var cameraHandler: Handler? = null

    /** stop 경로에서 의도적으로 닫는 중일 때 onDisconnected/onError를 무시하기 위한 플래그. */
    @Volatile private var stopping = false

    /**
     * 카메라를 열고 [widthPx]x[heightPx] YUV 반복 캡처를 시작한다.
     * 프레임은 [onVideoFrame](tightly-packed I420, 세션 단조 증가 pts us)으로,
     * 카메라가 (다른 앱 선점·기기 오류 등으로) 예기치 않게 끊기면 [onSessionEnded]로 통지한다.
     */
    suspend fun startCameraSession(
        widthPx: Int,
        heightPx: Int,
        frameRateFps: Int,
        onVideoFrame: (i420: ByteArray, presentationTimeUs: Long) -> Unit,
        onSessionEnded: (reason: String) -> Unit,
    ): Result<Unit> {
        stopCameraSession()
        stopping = false

        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return Result.failure(IllegalStateException("CAMERA 권한 없음 — 카메라 캡처 불가"))
        }

        val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val cameraId =
            pickCameraId(manager)
                ?: return Result.failure(IllegalStateException("사용 가능한 카메라가 없음"))

        val characteristics = manager.getCameraCharacteristics(cameraId)
        val streamMap =
            characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                ?: return Result.failure(IllegalStateException("카메라 스트림 구성 정보를 읽을 수 없음"))
        val supported = streamMap.getOutputSizes(ImageFormat.YUV_420_888).orEmpty()
        if (supported.none { it.width == widthPx && it.height == heightPx }) {
            return Result.failure(
                IllegalStateException(
                    "카메라가 ${widthPx}x${heightPx} YUV 출력을 지원하지 않음 (지원: ${supported.joinToString()})",
                ),
            )
        }

        val thread = HandlerThread("BladeCamera").apply { start() }
        cameraThread = thread
        val handler = Handler(thread.looper)
        cameraHandler = handler

        val readySignal = CompletableDeferred<Result<Unit>>()

        val reader = ImageReader.newInstance(widthPx, heightPx, ImageFormat.YUV_420_888, MAX_READER_IMAGES)
        imageReader = reader
        reader.setOnImageAvailableListener(
            { r ->
                // 인코더가 밀리면 최신 프레임만 취하고 백로그는 버린다(실시간 우선).
                val image = r.acquireLatestImage() ?: return@setOnImageAvailableListener
                try {
                    val i420 = ByteArray(widthPx * heightPx * 3 / 2)
                    imageToI420(image, i420)
                    onVideoFrame(i420, image.timestamp / 1_000) // ns -> us
                } catch (e: Exception) {
                    Log.e(TAG, "프레임 변환 실패(해당 프레임 드롭)", e)
                } finally {
                    image.close()
                }
            },
            handler,
        )

        try {
            @Suppress("MissingPermission")
            manager.openCamera(
                cameraId,
                object : CameraDevice.StateCallback() {
                    override fun onOpened(device: CameraDevice) {
                        cameraDevice = device
                        createSession(device, reader, characteristics, frameRateFps, handler, readySignal, onSessionEnded)
                    }

                    override fun onDisconnected(device: CameraDevice) {
                        device.close()
                        if (!readySignal.isCompleted) {
                            readySignal.complete(Result.failure(IllegalStateException("카메라 연결 끊김(다른 앱 선점 가능성)")))
                        } else if (!stopping) {
                            onSessionEnded("카메라 연결 끊김(onDisconnected)")
                        }
                    }

                    override fun onError(device: CameraDevice, error: Int) {
                        device.close()
                        if (!readySignal.isCompleted) {
                            readySignal.complete(Result.failure(IllegalStateException("카메라 오류(code=$error)")))
                        } else if (!stopping) {
                            onSessionEnded("카메라 오류(code=$error)")
                        }
                    }
                },
                handler,
            )
        } catch (e: Exception) {
            stopCameraSession()
            return Result.failure(IllegalStateException("카메라 열기 실패: ${e.message}", e))
        }

        val result =
            withTimeoutOrNull(SESSION_START_TIMEOUT_MS) { readySignal.await() }
                ?: Result.failure(IllegalStateException("카메라 세션 시작 타임아웃"))

        if (result.isFailure) {
            stopCameraSession()
        }
        return result
    }

    private fun createSession(
        device: CameraDevice,
        reader: ImageReader,
        characteristics: CameraCharacteristics,
        frameRateFps: Int,
        handler: Handler,
        readySignal: CompletableDeferred<Result<Unit>>,
        onSessionEnded: (reason: String) -> Unit,
    ) {
        val executor = Executor { command -> handler.post(command) }
        val stateCallback =
            object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    captureSession = session
                    try {
                        val request =
                            device.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                                addTarget(reader.surface)
                                pickFpsRange(characteristics, frameRateFps)?.let {
                                    set(android.hardware.camera2.CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, it)
                                }
                            }
                        session.setRepeatingRequest(request.build(), null, handler)
                        readySignal.complete(Result.success(Unit))
                    } catch (e: Exception) {
                        readySignal.complete(Result.failure(IllegalStateException("반복 캡처 요청 실패: ${e.message}", e)))
                    }
                }

                override fun onConfigureFailed(session: CameraCaptureSession) {
                    readySignal.complete(Result.failure(IllegalStateException("카메라 세션 구성 실패")))
                }

                override fun onClosed(session: CameraCaptureSession) {
                    if (readySignal.isCompleted && !stopping) {
                        onSessionEnded("카메라 세션 종료(onClosed)")
                    }
                }
            }
        try {
            device.createCaptureSession(
                SessionConfiguration(
                    SessionConfiguration.SESSION_REGULAR,
                    listOf(OutputConfiguration(reader.surface)),
                    executor,
                    stateCallback,
                ),
            )
        } catch (e: Exception) {
            readySignal.complete(Result.failure(IllegalStateException("카메라 세션 생성 실패: ${e.message}", e)))
        }
    }

    fun stopCameraSession() {
        stopping = true
        try {
            captureSession?.close()
        } catch (e: Exception) {
            Log.w(TAG, "캡처 세션 종료 중 오류(무시)", e)
        }
        captureSession = null
        try {
            cameraDevice?.close()
        } catch (e: Exception) {
            Log.w(TAG, "카메라 종료 중 오류(무시)", e)
        }
        cameraDevice = null
        imageReader?.close()
        imageReader = null
        cameraThread?.quitSafely()
        cameraThread = null
        cameraHandler = null
    }

    /** 후면(월드뷰) 카메라 우선 — Blade 2 온보드 카메라는 LENS_FACING_BACK으로 보고된다. */
    private fun pickCameraId(manager: CameraManager): String? {
        val ids = manager.cameraIdList
        return ids.firstOrNull { id ->
            manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) ==
                CameraCharacteristics.LENS_FACING_BACK
        } ?: ids.firstOrNull()
    }

    /** 목표 fps를 포함하는 가장 좁은 AE fps 범위를 고른다(없으면 프레임워크 기본값 사용). */
    private fun pickFpsRange(characteristics: CameraCharacteristics, targetFps: Int): Range<Int>? {
        val ranges =
            characteristics.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
                ?: return null
        return ranges
            .filter { it.lower <= targetFps && targetFps <= it.upper }
            .minByOrNull { it.upper - it.lower }
    }

    /**
     * Camera2 YUV_420_888 [Image]를 tightly-packed I420(Y, U, V 플레인 순서)으로 변환한다.
     * rowStride/pixelStride가 기기마다 달라(플레인이 NV21처럼 인터리브돼 있을 수도 있음)
     * 플레인별로 stride를 해석해 복사한다.
     */
    private fun imageToI420(image: Image, dst: ByteArray) {
        val width = image.width
        val height = image.height
        var offset = 0
        copyPlane(image.planes[0], width, height, dst, offset)
        offset += width * height
        copyPlane(image.planes[1], width / 2, height / 2, dst, offset)
        offset += (width / 2) * (height / 2)
        copyPlane(image.planes[2], width / 2, height / 2, dst, offset)
    }

    private fun copyPlane(
        plane: Image.Plane,
        planeWidth: Int,
        planeHeight: Int,
        dst: ByteArray,
        dstOffset: Int,
    ) {
        val buffer = plane.buffer
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        var dstIndex = dstOffset
        if (pixelStride == 1 && rowStride == planeWidth) {
            // 가장 흔한 경우: 이미 연속 배치 — 통째로 복사.
            buffer.position(0)
            buffer.get(dst, dstIndex, planeWidth * planeHeight)
            return
        }
        val rowBytes = ByteArray(rowStride)
        for (row in 0 until planeHeight) {
            buffer.position(row * rowStride)
            if (pixelStride == 1) {
                buffer.get(dst, dstIndex, planeWidth)
                dstIndex += planeWidth
            } else {
                val toRead = min(buffer.remaining(), rowStride)
                buffer.get(rowBytes, 0, toRead)
                for (col in 0 until planeWidth) {
                    dst[dstIndex++] = rowBytes[col * pixelStride]
                }
            }
        }
    }
}
