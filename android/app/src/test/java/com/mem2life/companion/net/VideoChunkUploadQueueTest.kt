package com.mem2life.companion.net

import com.mem2life.companion.config.BackendConfig
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * 네트워크 I/O 없이 [VideoChunkUploadQueue]의 순수 로직(백오프 계산, 파일명 규약)만
 * 검증한다 — 실제 업로드 워커 루프는 기동시키지 않는다.
 */
class VideoChunkUploadQueueTest {

    @get:Rule val tempFolder = TemporaryFolder()

    private fun newQueue(): VideoChunkUploadQueue {
        val apiClient = SessionApiClient { BackendConfig.FALLBACK }
        val scope = CoroutineScope(Job())
        return VideoChunkUploadQueue(
            queueDir = tempFolder.newFolder("queue"),
            apiClient = apiClient,
            sessionId = "test-session",
            scope = scope,
        )
    }

    @Test
    fun `백오프는 초기값에서 시작해 지수적으로 증가하고 상한을 넘지 않는다`() {
        val queue = newQueue()
        assertEquals(1_000L, queue.backoffFor(0))
        assertEquals(2_000L, queue.backoffFor(1))
        assertEquals(4_000L, queue.backoffFor(2))
        assertEquals(8_000L, queue.backoffFor(3))
        // 상한(기본 30초)을 넘지 않아야 한다.
        assertEquals(30_000L, queue.backoffFor(20))
    }

    @Test
    fun `청크 파일명은 seq, startTs, duration을 인코딩하고 다시 파싱할 수 있다`() {
        val queue = newQueue()
        val fileName = queue.fileName(seq = 7, startTsSec = 210.0, durationSec = 30.0)
        assertEquals("000007_210000_30000.mp4", fileName)

        val file = File(tempFolder.newFolder("parse"), fileName)
        file.writeBytes(byteArrayOf(1, 2, 3))
        val parsed = queue.parseFileName(file)

        assertEquals(7, parsed?.seq)
        assertEquals(210.0, parsed?.startTsSec)
        assertEquals(30.0, parsed?.durationSec)
    }

    @Test
    fun `규약에 맞지 않는 파일명은 파싱에 실패해 null을 반환한다`() {
        val queue = newQueue()
        val file = File(tempFolder.newFolder("bad"), "not_a_valid_name.mp4")
        file.writeBytes(byteArrayOf(1))
        assertNull(queue.parseFileName(file))
    }
}
