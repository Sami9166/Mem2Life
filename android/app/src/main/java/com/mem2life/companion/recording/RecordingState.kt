package com.mem2life.companion.recording

sealed class RecordingState {
    data object Idle : RecordingState()
    data object Starting : RecordingState()
    data class Recording(val sessionId: String) : RecordingState()
    data object Stopping : RecordingState()
    data object Stopped : RecordingState()
    data class Error(val message: String) : RecordingState()
}
