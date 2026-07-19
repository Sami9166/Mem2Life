package com.mem2life.companion.capture

import java.io.File

/** 인코딩이 끝난 30초(또는 마지막 짧은) 영상 청크 하나에 대한 메타데이터. */
data class ChunkFile(
    val file: File,
    val seq: Int,
    val startTsSec: Double,
    val durationSec: Double,
)
