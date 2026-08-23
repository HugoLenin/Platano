package co.elb.app.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import co.elb.app.BuildConfig
import co.elb.app.call.CallController
import co.elb.app.call.CallPhase
import co.elb.app.call.CallService
import co.elb.app.call.LocationSource
import co.elb.app.data.Api
import co.elb.app.data.NewContact
import co.elb.app.data.Prefs
import co.elb.app.data.TrustedContact
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class Screen { HOME, CALL, SETTINGS }

data class AppState(
    val screen: Screen = Screen.HOME,
    val language: String = Prefs.DEFAULT_LANG,
    val displayName: String = "",
    val room: String = BuildConfig.DEFAULT_ROOM,
    val contacts: List<TrustedContact> = emptyList(),
    val contactsLoading: Boolean = false,
    val contactsError: String? = null,
    val savingContact: Boolean = false,
    val banner: String? = null,
)

class ElbViewModel(app: Application) : AndroidViewModel(app) {

    private val prefs = Prefs(app)
    private val api = Api()
    private val location = LocationSource(app)
    val call = CallController(app, api)

    private val _state = MutableStateFlow(
        AppState(
            language = prefs.language,
            displayName = prefs.displayName,
            room = prefs.room.ifBlank { BuildConfig.DEFAULT_ROOM },
        ),
    )
    val state: StateFlow<AppState> = _state.asStateFlow()

    val userId: String get() = prefs.userId.ifBlank { BuildConfig.DEMO_USER_ID }
    val glossary get() = call.glossary

    init {
        if (prefs.userId.isBlank()) prefs.userId = BuildConfig.DEMO_USER_ID
        refreshContacts()
    }

    // ------------------------------------------------------------- settings
    fun setLanguage(code: String) {
        prefs.language = code
        _state.update { it.copy(language = code) }
    }

    fun setDisplayName(name: String) {
        prefs.displayName = name
        _state.update { it.copy(displayName = name) }
    }

    fun setRoom(room: String) {
        prefs.room = room
        _state.update { it.copy(room = room) }
    }

    fun go(screen: Screen) = _state.update { it.copy(screen = screen, banner = null) }

    fun dismissBanner() = _state.update { it.copy(banner = null) }

    // ------------------------------------------------------------- contacts
    fun refreshContacts() {
        viewModelScope.launch {
            _state.update { it.copy(contactsLoading = true, contactsError = null) }
            runCatching { api.contacts(userId) }
                .onSuccess { list -> _state.update { it.copy(contacts = list, contactsLoading = false) } }
                .onFailure { e ->
                    _state.update {
                        it.copy(contactsLoading = false, contactsError = e.message ?: "no se pudo cargar")
                    }
                }
        }
    }

    fun addContact(name: String, relationship: String, phone: String, email: String, locale: String) {
        viewModelScope.launch {
            _state.update { it.copy(savingContact = true) }
            runCatching {
                api.addContact(
                    NewContact(
                        userId = userId,
                        name = name.trim(),
                        relationship = relationship.trim(),
                        phone = phone.trim().ifBlank { null },
                        email = email.trim().ifBlank { null },
                        locale = locale,
                    ),
                )
            }.onSuccess {
                _state.update {
                    it.copy(
                        savingContact = false,
                        banner = "Contacto guardado. Recibirá el aviso por correo " +
                            "si activas una emergencia.",
                    )
                }
                refreshContacts()
            }.onFailure { e ->
                _state.update { it.copy(savingContact = false, contactsError = e.message) }
            }
        }
    }

    fun removeContact(id: String) {
        viewModelScope.launch {
            runCatching { api.removeContact(id) }.onSuccess { refreshContacts() }
        }
    }

    // ------------------------------------------------------------------ call
    fun startCall() {
        val s = _state.value
        viewModelScope.launch {
            _state.update { it.copy(screen = Screen.CALL) }
            CallService.start(getApplication())
            call.start(
                roomName = s.room.ifBlank { BuildConfig.DEFAULT_ROOM },
                lang = s.language,
                userId = userId,
                displayName = s.displayName,
            )
            // Send the fix as soon as we are connected. The operator asking
            // "where are you?" is the single slowest part of a real call.
            pushLocation()
        }
    }

    fun pushLocation() {
        viewModelScope.launch {
            repeat(3) { attempt ->
                if (call.state.value.phase != CallPhase.LIVE) {
                    delay(1200)
                    return@repeat
                }
                val fix = location.current()
                if (fix != null) {
                    call.sendLocation(fix.first, fix.second, fix.third)
                    return@launch
                }
                delay(1500L * (attempt + 1))
            }
        }
    }

    fun toggleMic() = viewModelScope.launch { call.toggleMic() }

    fun endCall() {
        viewModelScope.launch {
            call.end()
            CallService.stop(getApplication())
        }
    }

    fun leaveCallScreen() {
        call.dispose()
        CallService.stop(getApplication())
        _state.update { it.copy(screen = Screen.HOME) }
    }

    override fun onCleared() {
        call.dispose()
        CallService.stop(getApplication())
        super.onCleared()
    }
}
