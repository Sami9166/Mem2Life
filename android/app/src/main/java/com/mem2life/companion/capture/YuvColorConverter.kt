package com.mem2life.companion.capture

import java.nio.ByteBuffer

/**
 * DAT `VideoFrame`은 I420(플레인 순서 Y, U, V) 원시 버퍼를 제공한다(공식 CameraAccess
 * 샘플의 YuvToBitmapConverter가 그렇게 취급한다). 반면 MediaCodec 하드웨어 인코더는
 * 기기에 따라 NV12(세미플레인, Y 다음에 U/V 인터리브)만 지원하는 경우가 많다.
 * 이 컨버터는 인코더가 보고한 ColorFormat에 맞춰 필요할 때만 변환한다.
 */
object YuvColorConverter {

    /** I420(Y, U, V 순서 플레인) 원본을 그대로 복사한다 — COLOR_FormatYUV420Planar용. */
    fun copyI420(src: ByteBuffer, width: Int, height: Int, dst: ByteArray) {
        val ySize = width * height
        val uvSize = ySize / 4
        src.rewind()
        src.get(dst, 0, ySize + uvSize * 2)
    }

    /** I420(Y, U, V) -> NV12(Y, 그 뒤 U/V 인터리브)로 변환 — COLOR_FormatYUV420SemiPlanar용. */
    fun i420ToNv12(src: ByteBuffer, width: Int, height: Int, dst: ByteArray) {
        val ySize = width * height
        val uvSize = ySize / 4
        src.rewind()

        // Y plane은 그대로.
        src.get(dst, 0, ySize)

        val uPlane = ByteArray(uvSize)
        val vPlane = ByteArray(uvSize)
        src.get(uPlane)
        src.get(vPlane)

        var dstIndex = ySize
        for (i in 0 until uvSize) {
            dst[dstIndex++] = uPlane[i]
            dst[dstIndex++] = vPlane[i]
        }
    }
}
