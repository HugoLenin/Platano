package co.elb.app.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat

/**
 * High-contrast palette. One accent (red) reserved for life-critical
 * information so it never becomes decoration, and generous type sizes because
 * this screen gets read by a frightened person holding a shaking phone.
 */
val Crit = Color(0xFFDC2626)
val CritLight = Color(0xFFEF4444)
val CritContainer = Color(0xFF450A0A)
val Warn = Color(0xFFFBBF24)
val WarnContainer = Color(0xFF451A03)
val Ok = Color(0xFF34D399)
val Info = Color(0xFF38BDF8)

private val DarkColors = darkColorScheme(
    primary = CritLight,
    onPrimary = Color.White,
    primaryContainer = CritContainer,
    onPrimaryContainer = Color(0xFFFECACA),
    secondary = Info,
    background = Color(0xFF0A0C10),
    onBackground = Color(0xFFEEF2F8),
    surface = Color(0xFF10141B),
    onSurface = Color(0xFFEEF2F8),
    surfaceVariant = Color(0xFF1D2430),
    onSurfaceVariant = Color(0xFFC3CCDB),
    outline = Color(0xFF3D4A5F),
    error = CritLight,
)

private val LightColors = lightColorScheme(
    primary = Crit,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFEE2E2),
    onPrimaryContainer = Color(0xFF7F1D1D),
    secondary = Color(0xFF0284C7),
    background = Color(0xFFF8FAFC),
    onBackground = Color(0xFF0F172A),
    surface = Color.White,
    onSurface = Color(0xFF0F172A),
    surfaceVariant = Color(0xFFE2E8F0),
    onSurfaceVariant = Color(0xFF475569),
    outline = Color(0xFFCBD5E1),
    error = Crit,
)

private val ElbTypography = Typography(
    displaySmall = TextStyle(fontSize = 34.sp, fontWeight = FontWeight.Bold, lineHeight = 40.sp),
    headlineSmall = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.Bold, lineHeight = 28.sp),
    titleMedium = TextStyle(fontSize = 17.sp, fontWeight = FontWeight.SemiBold, lineHeight = 24.sp),
    bodyLarge = TextStyle(fontSize = 17.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 15.sp, lineHeight = 21.sp),
    labelSmall = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.6.sp),
)

@Composable
fun ElbTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val colors = if (dark) DarkColors else LightColors
    val view = LocalContext.current as? Activity
    SideEffect {
        view?.window?.let { w ->
            WindowCompat.getInsetsController(w, w.decorView).isAppearanceLightStatusBars = !dark
        }
    }
    MaterialTheme(colorScheme = colors, typography = ElbTypography, content = content)
}
