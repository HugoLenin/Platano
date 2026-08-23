package co.elb.app.call

import android.content.Context
import android.util.Log
import co.elb.app.data.Api
import co.elb.app.data.BusEvent
import co.elb.app.data.ElbJson
import co.elb.app.data.Glossary
import co.elb.app.data.OutboundEndCall
import co.elb.app.data.OutboundHello
import co.elb.app.data.OutboundLocation
import com.twilio.audioswitch.AudioDevice
import io.livekit.android.ConnectOptions
import io.livekit.android.LiveKit
import io.livekit.android.AudioOptions
import io.livekit.android.LiveKitOverrides
import io.livekit.android.audio.AudioSwitchHandler
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.room.Room
import io.livekit.android.room.participant.RemoteParticipant
import io.livekit.android.room.track.DataPublishReliability
import io.livekit.android.room.track.RemoteTrackPublication
import io.livekit.android.room.track.Track
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** One transcript line as the caller sees it. */
data class Line(
    val key: String,
    val fromCaller: Boolean,
    val lang: String,
    val original: String,
    val rendered: String? = null,
    val renderedLang: String? = null,
    val fallback: Boolean = false,
    val latencyMs: Int? = null,
)

enum class CallPhase { IDLE, CONNECTING, LIVE, ENDING, ENDED, FAILED }

data class CallUiState(
    val phase: CallPhase = CallPhase.IDLE,
    val room: String = "",
    val error: String? = null,
    val micOn: Boolean = true,
    val interpreterReady: Boolean = false,
    val lines: List<Line> = emptyList(),
    val incomingState: String = "listening",   // interpreter -> caller
    val outgoingState: String = "listening",   // caller -> operator
    val startedAtMs: Long = 0L,
    val locationShared: Boolean = false,
    val reportSent: Boolean = false,
)

/**
 * Caller-side LiveKit wiring.
 *
 * The one thing that must not be got wrong: `autoSubscribe = false`.
 * The room contains BOTH interpreter tracks. With LiveKit's default
 * auto-subscribe the caller would hear the caller-language -> Spanish
 * rendering meant for the dispatcher on top of their own interpretation.
 * We subscribe to exactly one track, matched by name.
 */
class CallController(
    private val appContext: Context,
    private val api: Api = Api(),
) {
    private val tag = "ElbCall"
    private var scope = CoroutineScope(SupervisorJob())
    private var room: Room? = null
    private var eventJob: Job? = null

    private val _state = MutableStateFlow(CallUiState())
    val state: StateFlow<CallUiState> = _state.asStateFlow()

    val glossary: Glossary by lazy { Glossary.load(appContext) }

    suspend fun start(roomName: String, lang: String, userId: String, displayName: String) {
        if (_state.value.phase == CallPhase.LIVE || _state.value.phase == CallPhase.CONNECTING) return
        _state.update { CallUiState(phase = CallPhase.CONNECTING, room = roomName) }

        try {
            val creds = api.token(room = roomName, lang = lang, userId = userId, displayName = displayName)
            val r = LiveKit.create(
                appContext,
                overrides = LiveKitOverrides(
                    audioOptions = AudioOptions(
                        audioHandler = AudioSwitchHandler(appContext).apply {
                            preferredDeviceList = listOf(
                                AudioDevice.BluetoothHeadset::class.java,
                                AudioDevice.WiredHeadset::class.java,
                                AudioDevice.Speakerphone::class.java,
                                AudioDevice.Earpiece::class.java,
                            )
                        },
                    ),
                ),
            )
            room = r

            eventJob = scope.launch { r.events.collect { handleEvent(it) } }

            r.connect(creds.url, creds.token, ConnectOptions(autoSubscribe = false))
            r.localParticipant.setMicrophoneEnabled(true)

            // Anything published before we attached: subscribe now.
            r.remoteParticipants.values.forEach { p -> sweep(p) }

            r.localParticipant.publishData(
                ElbJson.encodeToString(OutboundHello.serializer(), OutboundHello(role = "caller", lang = lang))
                    .toByteArray(),
                DataPublishReliability.RELIABLE,
                topic = DATA_TOPIC,
            )

            _state.update {
                it.copy(
                    phase = CallPhase.LIVE,
                    room = creds.room,
                    startedAtMs = System.currentTimeMillis(),
                    micOn = true,
                )
            }
            Log.i(tag, "connected to ${creds.room}")
        } catch (e: Exception) {
            Log.e(tag, "connect failed", e)
            _state.update { it.copy(phase = CallPhase.FAILED, error = e.message ?: "no se pudo conectar") }
        }
    }

    private fun maybeSubscribe(pub: Any?) {
        val p = pub as? RemoteTrackPublication ?: return
        if (p.kind == Track.Kind.AUDIO && p.name == TRACK_TO_CALLER) {
            p.setSubscribed(true)
            _state.update { it.copy(interpreterReady = true) }
            Log.i(tag, "subscribed to $TRACK_TO_CALLER")
        }
    }

    /**
     * Subscribe to whatever this participant has already published. Track
     * subscription is the only signal that means anything here: a participant
     * called "interpreter-*" being in the room does not yet mean we can hear
     * it. Covers the race where the interpreter publishes before we attach the
     * event collector.
     */
    private fun sweep(participant: RemoteParticipant) {
        participant.trackPublications.values.forEach { pub -> maybeSubscribe(pub) }
    }

    private fun handleEvent(event: RoomEvent) {
        when (event) {
            is RoomEvent.TrackPublished -> maybeSubscribe(event.publication)
            is RoomEvent.ParticipantConnected -> sweep(event.participant)
            is RoomEvent.DataReceived -> {
                if (event.topic != null && event.topic != DATA_TOPIC) return
                val parsed = runCatching {
                    ElbJson.decodeFromString(BusEvent.serializer(), String(event.data))
                }.getOrNull() ?: return
                applyBusEvent(parsed)
            }
            is RoomEvent.Disconnected -> {
                _state.update {
                    if (it.phase == CallPhase.ENDING || it.phase == CallPhase.ENDED) it.copy(phase = CallPhase.ENDED)
                    else it.copy(phase = CallPhase.ENDED)
                }
            }
            else -> Unit
        }
    }

    private fun applyBusEvent(ev: BusEvent) = when (ev.type) {
        "transcript" -> _state.update { s ->
            s.copy(
                lines = s.lines + Line(
                    key = "${ev.speaker}-${ev.seq}-${ev.ts}",
                    fromCaller = ev.speaker == "caller",
                    lang = ev.lang ?: "",
                    original = ev.text ?: "",
                ),
            )
        }

        "translation" -> _state.update { s ->
            val idx = s.lines.indexOfLast {
                it.fromCaller == (ev.speaker == "caller") && it.original == ev.source && it.rendered == null
            }
            val updated = if (idx >= 0) {
                s.lines.toMutableList().also {
                    it[idx] = it[idx].copy(
                        rendered = ev.text,
                        renderedLang = ev.lang,
                        fallback = ev.fallback,
                        latencyMs = ev.latencyMs,
                    )
                }
            } else {
                s.lines + Line(
                    key = "${ev.speaker}-${ev.seq}-${ev.ts}-t",
                    fromCaller = ev.speaker == "caller",
                    lang = ev.lang ?: "",
                    original = ev.source ?: "",
                    rendered = ev.text,
                    renderedLang = ev.lang,
                    fallback = ev.fallback,
                    latencyMs = ev.latencyMs,
                )
            }
            s.copy(lines = updated)
        }

        "state" -> _state.update { s ->
            when (ev.direction) {
                "to_caller" -> s.copy(incomingState = ev.state ?: s.incomingState)
                "to_operator" -> s.copy(outgoingState = ev.state ?: s.outgoingState)
                else -> s
            }
        }

        "notify" -> _state.update { it.copy(reportSent = true) }

        else -> Unit
    }

    suspend fun sendLocation(lat: Double, lon: Double, accuracy: Float) {
        val r = room ?: return
        runCatching {
            r.localParticipant.publishData(
                ElbJson.encodeToString(
                    OutboundLocation.serializer(),
                    OutboundLocation(lat = lat, lon = lon, accuracy = accuracy),
                ).toByteArray(),
                DataPublishReliability.RELIABLE,
                topic = DATA_TOPIC,
            )
            _state.update { it.copy(locationShared = true) }
        }.onFailure { Log.w(tag, "location publish failed", it) }
    }

    suspend fun toggleMic() {
        val r = room ?: return
        val next = !_state.value.micOn
        r.localParticipant.setMicrophoneEnabled(next)
        _state.update { it.copy(micOn = next) }
    }

    suspend fun end() {
        val r = room
        _state.update { it.copy(phase = CallPhase.ENDING) }
        if (r != null) {
            // Ask the agent to finalise (report + notifications) BEFORE we
            // drop the room, otherwise it only learns via the disconnect and
            // has to reconstruct why the call ended.
            runCatching {
                r.localParticipant.publishData(
                    ElbJson.encodeToString(OutboundEndCall.serializer(), OutboundEndCall()).toByteArray(),
                    DataPublishReliability.RELIABLE,
                    topic = DATA_TOPIC,
                )
            }
            kotlinx.coroutines.delay(900)
            runCatching { r.disconnect() }
        }
        eventJob?.cancel()
        room = null
        _state.update { it.copy(phase = CallPhase.ENDED) }
    }

    fun dispose() {
        runCatching { room?.disconnect() }
        room = null
        scope.cancel()
        scope = CoroutineScope(SupervisorJob())
        _state.value = CallUiState()
    }

    companion object {
        const val DATA_TOPIC = "elb"
        const val TRACK_TO_CALLER = "interpreter-to-caller"
    }
}
