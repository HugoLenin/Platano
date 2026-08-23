package co.elb.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class ElbApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_CALL,
                    getString(R.string.channel_call),
                    NotificationManager.IMPORTANCE_HIGH,
                ).apply { description = getString(R.string.channel_call_desc) },
            )
        }
    }

    companion object {
        const val CHANNEL_CALL = "elb_call"
    }
}
