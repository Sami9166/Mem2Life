package com.mem2life.companion.recording

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.mem2life.companion.R

private const val CHANNEL_ID = "mem2life_recording"
private const val NOTIFICATION_ID = 1001

/**
 * 녹화(영상 청크 업로드 + 오디오 스트리밍) 중 프로세스가 백그라운드에서 죽지 않도록
 * 유지하는 최소한의 포그라운드 서비스. 실제 녹화 로직은 갖지 않는다 — 오케스트레이션은
 * [RecordingSessionController]가 전담하고, 이 서비스는 그 생명주기에 맞춰 시작/종료만 된다.
 */
class RecordingForegroundService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ensureChannel()
        startForeground(NOTIFICATION_ID, buildNotification())
        return START_STICKY
    }

    private fun buildNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.recording_notification_title))
            .setContentText(getString(R.string.recording_notification_text))
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setOngoing(true)
            .build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    getString(R.string.recording_notification_channel),
                    NotificationManager.IMPORTANCE_LOW,
                ),
            )
        }
    }

    companion object {
        fun start(context: Context) {
            val intent = Intent(context, RecordingForegroundService::class.java)
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, RecordingForegroundService::class.java))
        }
    }
}
