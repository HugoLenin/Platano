package co.elb.app.data

import co.elb.app.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * Thin client for the ELB web backend. The phone never holds a LiveKit secret,
 * a Supabase key or an LLM key - it asks the backend for a scoped token.
 */
class Api(private val base: String = BuildConfig.API_BASE) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    private val jsonType = "application/json; charset=utf-8".toMediaType()

    class ApiError(message: String) : Exception(message)

    private suspend fun post(path: String, body: String): String = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url("$base$path")
            .post(body.toRequestBody(jsonType))
            .build()
        http.newCall(req).execute().use { res ->
            val text = res.body?.string().orEmpty()
            if (!res.isSuccessful) throw ApiError("HTTP ${res.code}: ${text.take(200)}")
            text
        }
    }

    private suspend fun get(path: String): String = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$base$path").get().build()
        http.newCall(req).execute().use { res ->
            val text = res.body?.string().orEmpty()
            if (!res.isSuccessful) throw ApiError("HTTP ${res.code}: ${text.take(200)}")
            text
        }
    }

    private suspend fun delete(path: String): Unit = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$base$path").delete().build()
        http.newCall(req).execute().use { res ->
            if (!res.isSuccessful) throw ApiError("HTTP ${res.code}")
        }
    }

    suspend fun token(room: String, lang: String, userId: String, displayName: String): TokenResponse {
        val payload = ElbJson.encodeToString(
            TokenRequest.serializer(),
            TokenRequest(role = "caller", room = room, lang = lang, userId = userId, displayName = displayName),
        )
        val out = ElbJson.decodeFromString(TokenResponse.serializer(), post("/api/token", payload))
        if (out.error != null) throw ApiError(out.error)
        if (out.token.isBlank() || out.url.isBlank()) throw ApiError("respuesta de token incompleta")
        return out
    }

    suspend fun contacts(userId: String): List<TrustedContact> {
        val out = ElbJson.decodeFromString(ContactsResponse.serializer(), get("/api/contacts?user_id=$userId"))
        if (out.error != null) throw ApiError(out.error)
        return out.contacts
    }

    suspend fun addContact(contact: NewContact) {
        post("/api/contacts", ElbJson.encodeToString(NewContact.serializer(), contact))
    }

    suspend fun removeContact(id: String) = delete("/api/contacts?id=$id")
}
