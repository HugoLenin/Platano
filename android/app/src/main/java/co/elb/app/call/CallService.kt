package co.elb.app.call

import android.annotation.SuppressLint
import android.app.Notification
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import co.elb.app.ElbApp
import co.elb.app.MainActivity
import co.elb.app.R

/**
 * Foreground service that keeps the emergency call alive.
 *
 * Typed `microphone|location`. The location type is the reason this app does
 * NOT need ACCESS_BACKGROUND_LOCATION: a foreground service with that type may
 * read location while the app is backgrounded or the screen is off, so we
 * never have to send the user through the two-step "Allow all the time"
 * settings flow that Android 11+ requires. See docs/DECISIONS.md.
 */
class CallService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    @SuppressLint("InlinedApi")
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val open = android.app.PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val notification: Notification = NotificationCompat.Builder(this, ElbApp.CHANNEL_CALL)
            .setSmallIcon(R.drawable.ic_stat_call)
            .setContentTitle(getString(R.string.call_ongoing_title))
            .setContentText(getString(R.string.call_ongoing_body))
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        return START_STICKY
    }

    companion object {
        private const val NOTIFICATION_ID = 4711

        fun start(context: Context) {
            val i = Intent(context, CallService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(i)
            else context.startService(i)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, CallService::class.java))
        }
    }
}
