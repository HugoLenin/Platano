package co.elb.app

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import co.elb.app.ui.CallScreen
import co.elb.app.ui.ElbViewModel
import co.elb.app.ui.EthicsNotice
import co.elb.app.ui.HomeScreen
import co.elb.app.ui.Screen
import co.elb.app.ui.SettingsScreen
import co.elb.app.ui.theme.ElbTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ElbTheme {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    ElbRoot()
                }
            }
        }
    }
}

/**
 * Permission strategy.
 *
 * Step 1 (blocking): microphone. Without it there is no call at all, so it is
 * asked for up front with a plain explanation.
 *
 * Step 2 (non-blocking): location + notifications, requested together at the
 * moment the user presses the emergency button. Location that is denied
 * degrades to "the operator asks where you are", which is exactly how a call
 * works today - it must never block the call.
 *
 * There is deliberately NO step 3 for ACCESS_BACKGROUND_LOCATION. On Android
 * 11+ that permission cannot be bundled with foreground location - the system
 * strips "Allow all the time" from the dialog and requires a separate trip to
 * a settings page. We avoid needing it entirely by running the call inside a
 * foreground service typed `microphone|location`, which is allowed to read
 * location while backgrounded. See docs/DECISIONS.md.
 */
@Composable
private fun ElbRoot(vm: ElbViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    val call by vm.call.state.collectAsStateWithLifecycle()

    var micGranted by remember { mutableStateOf(false) }
    var micAsked by remember { mutableStateOf(false) }

    val micLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        micGranted = granted
        micAsked = true
    }

    val secondaryLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { vm.startCall() }

    val ctx = androidx.compose.ui.platform.LocalContext.current
    androidx.compose.runtime.LaunchedEffect(Unit) {
        micGranted = androidx.core.content.ContextCompat.checkSelfPermission(
            ctx, Manifest.permission.RECORD_AUDIO,
        ) == android.content.pm.PackageManager.PERMISSION_GRANTED
        if (!micGranted) micLauncher.launch(Manifest.permission.RECORD_AUDIO)
    }

    fun beginCall() {
        val wanted = buildList {
            add(Manifest.permission.ACCESS_FINE_LOCATION)
            add(Manifest.permission.ACCESS_COARSE_LOCATION)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        secondaryLauncher.launch(wanted.toTypedArray())
    }

    Scaffold { inner ->
        Column(Modifier.fillMaxSize().padding(inner)) {
            when {
                !micGranted && micAsked -> MicDenied { micLauncher.launch(Manifest.permission.RECORD_AUDIO) }
                state.screen == Screen.CALL -> CallScreen(vm, call) { vm.leaveCallScreen() }
                state.screen == Screen.SETTINGS -> SettingsScreen(vm, state) { vm.go(Screen.HOME) }
                else -> HomeScreen(vm, state, onEmergency = ::beginCall)
            }
        }
    }
}

@Composable
private fun MicDenied(onRetry: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp, androidx.compose.ui.Alignment.CenterVertically),
    ) {
        Text("Necesitamos el micrófono", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Sin acceso al micrófono no podemos interpretar tu llamada. " +
                "Esta app solo escucha mientras una llamada de emergencia está activa.",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Button(onClick = onRetry) { Text("Conceder permiso") }
        EthicsNotice()
    }
}
