package co.elb.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import co.elb.app.call.CallPhase
import co.elb.app.call.CallUiState
import co.elb.app.call.Line
import co.elb.app.data.SUPPORTED_LANGUAGES
import co.elb.app.data.TrustedContact
import co.elb.app.ui.theme.Crit
import co.elb.app.ui.theme.Info
import co.elb.app.ui.theme.Ok
import co.elb.app.ui.theme.Warn

// ===========================================================================
// HOME
// ===========================================================================
@Composable
fun HomeScreen(vm: ElbViewModel, state: AppState, onEmergency: () -> Unit) {
    val langLabel = SUPPORTED_LANGUAGES.firstOrNull { it.code == state.language }
    val contactCount = state.contacts.count { it.active }
    val readyCount = state.contacts.count { it.active && it.emailReady }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    "EMERGENCY LANGUAGE BRIDGE",
                    style = MaterialTheme.typography.labelSmall,
                    color = Crit,
                )
                Text(
                    "Habla en tu idioma",
                    style = MaterialTheme.typography.displaySmall,
                    color = MaterialTheme.colorScheme.onBackground,
                )
            }
            IconButton(onClick = { vm.go(Screen.SETTINGS) }) {
                Icon(Icons.Filled.Settings, contentDescription = "Ajustes")
            }
        }

        Text(
            "Un intérprete traduce la llamada en las dos direcciones, en vivo. " +
                "Tú hablas en ${langLabel?.native ?: state.language}; el operador escucha en español.",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        // --- the button ------------------------------------------------------
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Surface(
                onClick = onEmergency,
                shape = CircleShape,
                color = Crit,
                shadowElevation = 12.dp,
                modifier = Modifier.size(220.dp),
            ) {
                Column(
                    Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Icon(
                        Icons.Filled.Call,
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(56.dp),
                    )
                    Spacer(Modifier.height(10.dp))
                    Text(
                        "LLAMAR",
                        style = MaterialTheme.typography.displaySmall,
                        color = Color.White,
                    )
                    Text(
                        "línea de emergencia",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Color.White.copy(alpha = 0.85f),
                    )
                }
            }
        }

        // --- readiness -------------------------------------------------------
        ReadyRow(
            icon = Icons.Filled.Language,
            title = "Tu idioma: ${langLabel?.native ?: state.language}",
            subtitle = "Toca Ajustes para cambiarlo",
            ok = true,
            onClick = { vm.go(Screen.SETTINGS) },
        )
        ReadyRow(
            icon = Icons.Filled.Group,
            title = if (contactCount == 0) "Sin contactos de confianza"
            else "$contactCount contacto(s) de confianza",
            subtitle = when {
                contactCount == 0 -> "Añádelos antes de una emergencia"
                readyCount < contactCount -> "$readyCount con correo · al resto no se le puede avisar"
                else -> "Todos recibirán el reporte automáticamente"
            },
            ok = contactCount > 0 && readyCount == contactCount,
            warn = contactCount > 0 && readyCount < contactCount,
            onClick = { vm.go(Screen.SETTINGS) },
        )

        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun ReadyRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String,
    ok: Boolean,
    warn: Boolean = false,
    onClick: () -> Unit,
) {
    val accent = when {
        ok -> Ok
        warn -> Warn
        else -> MaterialTheme.colorScheme.outline
    }
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(MaterialTheme.colorScheme.surface)
            .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.4f), RoundedCornerShape(14.dp))
            .clickable(onClick = onClick)
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, tint = accent)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(
                subtitle,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Icon(Icons.Filled.ChevronRight, contentDescription = null, tint = MaterialTheme.colorScheme.outline)
    }
}

// ===========================================================================
// CALL
// ===========================================================================
@Composable
fun CallScreen(vm: ElbViewModel, call: CallUiState, onLeave: () -> Unit) {
    var elapsed by remember { mutableIntStateOf(0) }
    LaunchedEffect(call.phase, call.startedAtMs) {
        while (call.phase == CallPhase.LIVE) {
            elapsed = ((System.currentTimeMillis() - call.startedAtMs) / 1000).toInt()
            kotlinx.coroutines.delay(1000)
        }
    }
    val listState = rememberLazyListState()
    LaunchedEffect(call.lines.size) {
        if (call.lines.isNotEmpty()) listState.animateScrollToItem(call.lines.lastIndex)
    }

    Column(Modifier.fillMaxSize()) {
        // header
        Surface(color = MaterialTheme.colorScheme.surface, tonalElevation = 3.dp) {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.size(10.dp).clip(CircleShape).background(
                            when (call.phase) {
                                CallPhase.LIVE -> Crit
                                CallPhase.CONNECTING -> Warn
                                CallPhase.FAILED -> Crit
                                else -> MaterialTheme.colorScheme.outline
                            },
                        ),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        when (call.phase) {
                            CallPhase.CONNECTING -> "Conectando…"
                            CallPhase.LIVE -> "En llamada"
                            CallPhase.ENDING -> "Cerrando y generando reporte…"
                            CallPhase.ENDED -> "Llamada finalizada"
                            CallPhase.FAILED -> "No se pudo conectar"
                            CallPhase.IDLE -> "—"
                        },
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Spacer(Modifier.weight(1f))
                    if (call.phase == CallPhase.LIVE) {
                        Text(
                            "%02d:%02d".format(elapsed / 60, elapsed % 60),
                            style = MaterialTheme.typography.titleMedium,
                            fontFamily = FontFamily.Monospace,
                        )
                    }
                }
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    StatePill("hacia ti", call.incomingState)
                    StatePill("hacia el operador", call.outgoingState)
                }
                Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Chip(if (call.interpreterReady) "Intérprete activo" else "Esperando intérprete", call.interpreterReady)
                    Chip(if (call.locationShared) "Ubicación enviada" else "Ubicación pendiente", call.locationShared)
                    if (call.reportSent) Chip("Red de apoyo avisada", true)
                }
            }
        }

        if (call.error != null) {
            Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.fillMaxWidth()) {
                Text(
                    call.error,
                    Modifier.padding(14.dp),
                    color = MaterialTheme.colorScheme.onErrorContainer,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }

        // transcript
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (call.lines.isEmpty()) {
                item {
                    Text(
                        if (call.phase == CallPhase.LIVE)
                            "Habla con normalidad. Lo que digas se traduce al operador, y lo que él responda lo escucharás en tu idioma."
                        else "…",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth().padding(top = 40.dp),
                    )
                }
            }
            items(call.lines, key = { it.key }) { line -> LineBubble(vm, line) }
        }

        // controls
        Surface(color = MaterialTheme.colorScheme.surface, tonalElevation = 3.dp) {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (call.phase == CallPhase.LIVE) {
                        OutlinedButton(
                            onClick = { vm.toggleMic() },
                            modifier = Modifier.weight(1f).height(56.dp),
                        ) {
                            Icon(if (call.micOn) Icons.Filled.Mic else Icons.Filled.MicOff, null)
                            Spacer(Modifier.width(8.dp))
                            Text(if (call.micOn) "Micrófono" else "Silenciado")
                        }
                        Button(
                            onClick = { vm.endCall() },
                            colors = ButtonDefaults.buttonColors(containerColor = Crit),
                            modifier = Modifier.weight(1f).height(56.dp),
                        ) {
                            Icon(Icons.Filled.CallEnd, null, tint = Color.White)
                            Spacer(Modifier.width(8.dp))
                            Text("Colgar", color = Color.White)
                        }
                    } else {
                        Button(
                            onClick = onLeave,
                            modifier = Modifier.fillMaxWidth().height(56.dp),
                        ) { Text("Volver al inicio") }
                    }
                }
            }
        }
    }
}

@Composable
private fun Chip(text: String, on: Boolean) {
    Surface(
        shape = RoundedCornerShape(8.dp),
        color = if (on) Ok.copy(alpha = 0.16f) else MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Text(
            text,
            Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelSmall,
            color = if (on) Ok else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun LineBubble(vm: ElbViewModel, line: Line) {
    val mine = line.fromCaller
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Surface(
            shape = RoundedCornerShape(16.dp),
            color = if (mine) MaterialTheme.colorScheme.primaryContainer
            else MaterialTheme.colorScheme.surfaceVariant,
            modifier = Modifier.fillMaxWidth(0.92f),
        ) {
            Column(Modifier.padding(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        if (mine) "TÚ · ${line.lang.uppercase()}" else "OPERADOR",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (mine) MaterialTheme.colorScheme.onPrimaryContainer else Info,
                    )
                    if (line.fallback) {
                        Spacer(Modifier.width(8.dp))
                        Text(
                            "MODO RESPALDO",
                            style = MaterialTheme.typography.labelSmall,
                            color = Crit,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                    Spacer(Modifier.weight(1f))
                    line.latencyMs?.let {
                        Text(
                            "${it} ms",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.outline,
                        )
                    }
                }
                Spacer(Modifier.height(4.dp))

                // For the caller, the primary text is the one in THEIR language.
                val primary = if (mine) line.original else (line.rendered ?: "")
                val secondary = if (mine) line.rendered else line.original

                if (primary.isNotBlank()) {
                    GlossaryText(
                        text = primary,
                        lang = if (mine) line.lang else (line.renderedLang ?: line.lang),
                        glossary = vm.glossary,
                        style = MaterialTheme.typography.bodyLarge,
                    )
                } else {
                    Text(
                        "interpretando…",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }

                if (!secondary.isNullOrBlank()) {
                    Spacer(Modifier.height(4.dp))
                    Text(
                        secondary,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

// ===========================================================================
// SETTINGS
// ===========================================================================
@Composable
fun SettingsScreen(vm: ElbViewModel, state: AppState, onBack: () -> Unit) {
    var showAdd by remember { mutableStateOf(false) }

    Column(Modifier.fillMaxSize()) {
        Surface(color = MaterialTheme.colorScheme.surface, tonalElevation = 3.dp) {
            Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) { Icon(Icons.Filled.ArrowBack, "Volver") }
                Text("Ajustes", style = MaterialTheme.typography.headlineSmall)
            }
        }

        AnimatedVisibility(state.banner != null) {
            Surface(color = Ok.copy(alpha = 0.15f), modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.CheckCircle, null, tint = Ok)
                    Spacer(Modifier.width(10.dp))
                    Text(state.banner ?: "", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    IconButton(onClick = { vm.dismissBanner() }) { Icon(Icons.Filled.Close, "Cerrar") }
                }
            }
        }

        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            // --- identity ----------------------------------------------------
            Section("Tu perfil") {
                OutlinedTextField(
                    value = state.displayName,
                    onValueChange = vm::setDisplayName,
                    label = { Text("Tu nombre") },
                    supportingText = { Text("Aparece en el aviso que reciben tus contactos") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = state.room,
                    onValueChange = vm::setRoom,
                    label = { Text("Sala de la demo") },
                    supportingText = { Text("El operador debe escribir exactamente esta sala") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }

            // --- language ----------------------------------------------------
            Section("Tu idioma") {
                Text(
                    "Hablarás y escucharás en este idioma durante toda la llamada.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(10.dp))
                SUPPORTED_LANGUAGES.forEach { lang ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .clickable { vm.setLanguage(lang.code) }
                            .padding(vertical = 10.dp, horizontal = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(selected = state.language == lang.code, onClick = { vm.setLanguage(lang.code) })
                        Spacer(Modifier.width(8.dp))
                        Column {
                            Text(lang.native, style = MaterialTheme.typography.bodyLarge)
                            Text(
                                lang.english,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            // --- trusted contacts --------------------------------------------
            Section("Contactos de confianza") {
                Text(
                    "Se guardan en el servidor, no solo en este teléfono: si pierdes el " +
                        "celular, tus contactos siguen ahí. Recibirán el reporte automáticamente " +
                        "cuando llames.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))

                if (state.contactsLoading) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                state.contactsError?.let {
                    Text("No se pudo cargar: $it", color = Crit, style = MaterialTheme.typography.bodyMedium)
                }
                state.contacts.forEach { c -> ContactRow(c, vm) }

                Spacer(Modifier.height(12.dp))
                Button(onClick = { showAdd = true }, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                    Icon(Icons.Filled.PersonAdd, null)
                    Spacer(Modifier.width(8.dp))
                    Text("Añadir contacto de confianza")
                }
            }

                Spacer(Modifier.height(24.dp))
        }
    }

    if (showAdd) {
        AddContactSheet(vm = vm, saving = state.savingContact, onDismiss = { showAdd = false })
    }
}

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column {
        Text(
            title.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = Crit,
        )
        Spacer(Modifier.height(8.dp))
        Surface(
            shape = RoundedCornerShape(14.dp),
            color = MaterialTheme.colorScheme.surface,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.padding(14.dp), content = content)
        }
    }
}

@Composable
private fun ContactRow(c: TrustedContact, vm: ElbViewModel) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(40.dp).clip(CircleShape).background(
                if (c.emailReady) Ok.copy(alpha = 0.2f) else Warn.copy(alpha = 0.2f),
            ),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                if (c.emailReady) Icons.Filled.CheckCircle else Icons.Filled.Schedule,
                null,
                tint = if (c.emailReady) Ok else Warn,
                modifier = Modifier.size(20.dp),
            )
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(c.name, style = MaterialTheme.typography.titleMedium)
            Text(
                listOfNotNull(c.relationship.ifBlank { null }, c.phone, c.email).joinToString(" · "),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                if (c.emailReady) "Recibirá el aviso por correo"
                else "Sin correo: no se le puede avisar",
                style = MaterialTheme.typography.labelSmall,
                color = if (c.emailReady) Ok else Warn,
            )
        }
        IconButton(onClick = { vm.removeContact(c.id) }) {
            Icon(Icons.Filled.Delete, "Eliminar", tint = MaterialTheme.colorScheme.outline)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddContactSheet(vm: ElbViewModel, saving: Boolean, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var relationship by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("+57") }
    var email by remember { mutableStateOf("") }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier.padding(horizontal = 20.dp).padding(bottom = 32.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Nuevo contacto de confianza", style = MaterialTheme.typography.headlineSmall)

            OutlinedTextField(
                value = name, onValueChange = { name = it },
                label = { Text("Nombre") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = relationship, onValueChange = { relationship = it },
                label = { Text("Parentesco (hermana, vecino…)") }, singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = phone, onValueChange = { phone = it },
                label = { Text("Teléfono (+57…, opcional)") },
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Phone),
                singleLine = true, modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = email, onValueChange = { email = it },
                label = { Text("Correo electrónico") },
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Email),
                singleLine = true, modifier = Modifier.fillMaxWidth(),
            )

            // The email address is not optional any more: it is the only way an
            // alert can leave the system. Saying so here beats letting someone
            // save a contact that silently never gets reached.
            Surface(shape = RoundedCornerShape(12.dp), color = Info.copy(alpha = 0.12f)) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("El correo es obligatorio", style = MaterialTheme.typography.titleMedium, color = Info)
                    Text(
                        "El aviso de emergencia se envía por correo electrónico. " +
                            "Sin una dirección válida no podremos avisarle a esta persona. " +
                            "Pídele que revise también la carpeta de spam la primera vez.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }

            Button(
                onClick = {
                    vm.addContact(name, relationship, phone, email, "es")
                    onDismiss()
                },
                enabled = !saving && name.isNotBlank() && email.contains("@"),
                modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (saving) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                else Text("Guardar contacto")
            }
        }
    }
}
