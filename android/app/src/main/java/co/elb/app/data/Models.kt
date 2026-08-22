package co.elb.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

val ElbJson = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    isLenient = true
    coerceInputValues = true
}

// ---------------------------------------------------------------- API models
@Serializable
data class TokenRequest(
    val role: String = "caller",
    val room: String,
    val lang: String,
    @SerialName("user_id") val userId: String,
    @SerialName("display_name") val displayName: String,
)

@Serializable
data class TokenResponse(
    val token: String = "",
    val url: String = "",
    val room: String = "",
    val identity: String = "",
    val error: String? = null,
)

@Serializable
data class TrustedContact(
    val id: String = "",
    val name: String = "",
    val relationship: String = "",
    @SerialName("phone_e164") val phone: String? = null,
    val email: String? = null,
    val locale: String = "es",
    val priority: Int = 100,
    val active: Boolean = true,
    @SerialName("notify_early") val notifyEarly: Boolean = true,
    @SerialName("notify_final") val notifyFinal: Boolean = true,
    @SerialName("whatsapp_opt_in_at") val whatsappOptInAt: String? = null,
    @SerialName("whatsapp_ready") val whatsappReady: Boolean = false,
)

@Serializable
data class ContactsResponse(val contacts: List<TrustedContact> = emptyList(), val error: String? = null)

@Serializable
data class NewContact(
    @SerialName("user_id") val userId: String,
    val name: String,
    val relationship: String = "",
    @SerialName("phone_e164") val phone: String? = null,
    val email: String? = null,
    val locale: String = "es",
    @SerialName("notify_early") val notifyEarly: Boolean = true,
    @SerialName("notify_final") val notifyFinal: Boolean = true,
)

// ------------------------------------------------------------- bus envelopes
// Mirrors agent/src/elb/bus.py. Only the fields the phone renders are typed;
// everything else is ignored, so adding an event server-side is not breaking.
@Serializable
data class BusEvent(
    val type: String = "",
    val ts: Long = 0,
    val direction: String? = null,
    val speaker: String? = null,
    val lang: String? = null,
    val seq: Int? = null,
    val text: String? = null,
    val source: String? = null,
    val state: String? = null,
    val fallback: Boolean = false,
    @SerialName("latency_ms") val latencyMs: Int? = null,
    val phase: String? = null,
    val kind: String? = null,
    val delivered: Int? = null,
    val ok: Boolean? = null,
    val data: JsonElement? = null,
)

@Serializable
data class OutboundLocation(
    val type: String = "location",
    val lat: Double,
    val lon: Double,
    val accuracy: Float = 0f,
)

@Serializable
data class OutboundHello(val type: String = "hello", val role: String, val lang: String)

@Serializable
data class OutboundEndCall(val type: String = "end_call")
