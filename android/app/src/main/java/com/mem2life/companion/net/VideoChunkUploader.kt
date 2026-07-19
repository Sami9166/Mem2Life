package com.mem2life.companion.net

import java.io.File

/**
 * 영상 청크 업로드 한 가지 동작만을 위한 좁은 인터페이스.
 *
 * 실제 앱 코드에서는 [SessionApiClient]가 이 인터페이스를 구현하며, 업로드 API
 * 계약(엔드포인트, multipart 필드명 등)은 여기서 바뀌지 않는다 — 여전히
 * `SessionApiClient.uploadVideoChunk`의 구현에만 있다.
 *
 * 존재 이유는 순전히 테스트 시임(seam)이다: [VideoChunkUploadQueue]의 워커 루프
 * (재시도/백오프/동시성)를 실 네트워크 호출 없이 단위 테스트하려면 가짜 업로더를
 * 주입할 수 있어야 한다. `SessionApiClient`는 OkHttpClient를 직접 들고 있는
 * `final` 클래스라 그 자체로는 테스트 대역을 만들 수 없다.
 */
fun interface VideoChunkUploader {
    suspend fun uploadVideoChunk(
        sessionId: String,
        chunkFile: File,
        seq: Int,
        startTsSec: Double,
        durationSec: Double,
    ): Result<Unit>
}
