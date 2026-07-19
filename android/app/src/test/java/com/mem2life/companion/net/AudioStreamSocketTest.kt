package com.mem2life.companion.net

import com.mem2life.companion.config.BackendConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import org.junit.Assert.assertEquals
import org.junit.Test

class AudioStreamSocketTest {

    private fun newSocket(): AudioStreamSocket =
        AudioStreamSocket(configProvider = { BackendConfig.FALLBACK }, scope = CoroutineScope(Job()))

    @Test
    fun `재연결 백오프는 초기값에서 시작해 2배씩 증가하고 상한을 넘지 않는다`() {
        val socket = newSocket()
        assertEquals(500L, socket.backoffFor(0))
        assertEquals(1_000L, socket.backoffFor(1))
        assertEquals(2_000L, socket.backoffFor(2))
        assertEquals(4_000L, socket.backoffFor(3))
        assertEquals(8_000L, socket.backoffFor(4))
        // 상한(기본 10초)을 넘지 않아야 한다.
        assertEquals(10_000L, socket.backoffFor(20))
    }
}
