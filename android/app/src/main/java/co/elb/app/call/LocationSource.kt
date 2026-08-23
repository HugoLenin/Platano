package co.elb.app.call

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * One high-accuracy fix, on demand.
 *
 * Deliberately not a continuous stream: the operator needs "where are you"
 * once, plus a refresh if the caller moves. Polling GPS for the whole call
 * drains a battery that may be the only way to reach this person.
 */
class LocationSource(private val context: Context) {

    fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    suspend fun current(): Triple<Double, Double, Float>? {
        if (!hasPermission()) return null
        val client = LocationServices.getFusedLocationProviderClient(context)
        return suspendCancellableCoroutine { cont ->
            client.getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY, null)
                .addOnSuccessListener { loc ->
                    cont.resume(loc?.let { Triple(it.latitude, it.longitude, it.accuracy) })
                }
                .addOnFailureListener { cont.resume(null) }
                .addOnCanceledListener { cont.resume(null) }
        }
    }
}
