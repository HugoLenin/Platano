package co.elb.app.data

import android.content.Context
import android.content.SharedPreferences

/**
 * Local settings only. Trusted contacts deliberately do NOT live here - they
 * live in Supabase, because the whole premise of the product is that they must
 * survive losing this phone.
 */
class Prefs(context: Context) {
    private val sp: SharedPreferences =
        context.getSharedPreferences("elb", Context.MODE_PRIVATE)

    var language: String
        get() = sp.getString(KEY_LANG, null) ?: DEFAULT_LANG
        set(v) = sp.edit().putString(KEY_LANG, v).apply()

    var displayName: String
        get() = sp.getString(KEY_NAME, null) ?: ""
        set(v) = sp.edit().putString(KEY_NAME, v).apply()

    var room: String
        get() = sp.getString(KEY_ROOM, null) ?: ""
        set(v) = sp.edit().putString(KEY_ROOM, v).apply()

    var userId: String
        get() = sp.getString(KEY_USER, null) ?: ""
        set(v) = sp.edit().putString(KEY_USER, v).apply()

    var onboarded: Boolean
        get() = sp.getBoolean(KEY_ONBOARDED, false)
        set(v) = sp.edit().putBoolean(KEY_ONBOARDED, v).apply()

    companion object {
        const val DEFAULT_LANG = "en"
        private const val KEY_LANG = "language"
        private const val KEY_NAME = "display_name"
        private const val KEY_ROOM = "room"
        private const val KEY_USER = "user_id"
        private const val KEY_ONBOARDED = "onboarded"
    }
}

/** Languages the STT model and the glossary both support. */
data class Language(val code: String, val native: String, val english: String)

val SUPPORTED_LANGUAGES = listOf(
    Language("en", "English", "English"),
    Language("es", "Español", "Spanish"),
    Language("pt", "Português", "Portuguese"),
    Language("fr", "Français", "French"),
    Language("de", "Deutsch", "German"),
    Language("it", "Italiano", "Italian"),
)
