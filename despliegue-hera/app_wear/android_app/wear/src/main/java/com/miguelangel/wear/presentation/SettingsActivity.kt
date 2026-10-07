package com.miguelangel.wear.presentation

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.miguelangel.wear.presentation.theme.DWM3001CDKRangingTheme

// Componentes nativos de Wear OS para navegacion/chrome (mismo criterio
// que en MainActivity.kt). OutlinedTextField no tiene equivalente en
// Wear Compose Material clasico (esta libreria no incluye entrada de
// texto como componente propio), asi que ese lo dejamos de material3.
import androidx.wear.compose.material.Chip as WearChip
import androidx.wear.compose.material.ChipDefaults as WearChipDefaults
import androidx.wear.compose.material.Scaffold as WearScaffold
import androidx.wear.compose.material.Text as WearText
import androidx.wear.compose.material.TimeText as WearTimeText

/**
 * Pantalla de ajustes: permite cambiar la IP del broker MQTT y el
 * watch_id sin tener que recompilar la app. Los cambios se guardan en
 * AppConfig (SharedPreferences) y se aplican la proxima vez que arranque
 * el UwbService (por eso, al guardar, reiniciamos el servicio).
 *
 * Antes de llegar a los ajustes reales, se pide un PIN de 4 digitos
 * (guardado como hash, nunca en texto plano). Si todavia no hay PIN
 * configurado, se pide crear uno la primera vez.
 *
 * OJO: usamos colores forzados explicitamente (fondo negro, texto
 * blanco) en vez de fiarnos del tema dinamico de Compose, porque en
 * reloj el tema dinamico puede dar combinaciones con muy poco contraste
 * (texto practicamente invisible). Tambien aplicamos margenes extra
 * generosos porque la pantalla es REDONDA: el contenido pegado al borde
 * se recorta por la propia curvatura fisica del cristal.
 */
class SettingsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            DWM3001CDKRangingTheme {
                var unlocked by remember { mutableStateOf(false) }

                if (unlocked) {
                    SettingsScreen(
                        onSaved = {
                            stopService(Intent(this, UwbService::class.java))
                            startForegroundService(Intent(this, UwbService::class.java))
                            Toast.makeText(this, "Guardado. Reiniciando servicio...", Toast.LENGTH_SHORT).show()
                            finish()
                        }
                    )
                } else {
                    PinGateScreen(onUnlocked = { unlocked = true })
                }
            }
        }
    }
}

/**
 * Pantalla de bloqueo por PIN delante de los ajustes.
 * - Si no hay PIN guardado todavia: pide crearlo (dos veces, para
 *   confirmar que no hay error de tecleo).
 * - Si ya hay PIN: pide introducirlo, y compara contra el hash guardado.
 */
@Composable
fun PinGateScreen(onUnlocked: () -> Unit) {
    val context = LocalContext.current
    val pinAlreadySet = remember { AppConfig.hasPinSet(context) }

    if (pinAlreadySet) {
        EnterPinScreen(onCorrect = onUnlocked)
    } else {
        CreatePinScreen(onCreated = onUnlocked)
    }
}

@Composable
private fun PinDots(length: Int, filled: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(length) { i ->
            androidx.compose.foundation.Canvas(modifier = Modifier.size(12.dp)) {
                drawCircle(color = if (i < filled) Color.White else Color(0xFF444444))
            }
        }
    }
}

@Composable
private fun NumericPinField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    errorText: String?
) {
    WearText(label, color = Color(0xFFB0B0B0))
    Spacer(modifier = Modifier.height(8.dp))
    PinDots(length = 4, filled = value.length)
    Spacer(modifier = Modifier.height(10.dp))
    OutlinedTextField(
        value = value,
        onValueChange = { new ->
            if (new.length <= 4 && new.all { it.isDigit() }) onValueChange(new)
        },
        singleLine = true,
        textStyle = androidx.compose.ui.text.TextStyle(color = Color.White, fontSize = 13.sp),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
        colors = SettingsFieldColors,
        modifier = Modifier.fillMaxWidth(0.6f)
    )
    if (errorText != null) {
        Spacer(modifier = Modifier.height(6.dp))
        WearText(errorText, color = Color(0xFFFF6B6B), textAlign = TextAlign.Center)
    }
}

@Composable
private fun EnterPinScreen(onCorrect: () -> Unit) {
    val context = LocalContext.current
    var pin by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    WearScaffold(timeText = { WearTimeText() }) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black)
                .padding(start = 24.dp, end = 24.dp, top = 34.dp, bottom = 40.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            WearText("Introduce el PIN", fontWeight = FontWeight.Bold, color = Color.White)
            Spacer(modifier = Modifier.height(16.dp))
            NumericPinField(
                value = pin,
                onValueChange = {
                    pin = it
                    error = null
                    if (it.length == 4) {
                        if (AppConfig.checkPin(context, it)) {
                            onCorrect()
                        } else {
                            error = "PIN incorrecto"
                            pin = ""
                        }
                    }
                },
                label = "PIN de 4 dígitos",
                errorText = error
            )
        }
    }
}

@Composable
private fun CreatePinScreen(onCreated: () -> Unit) {
    val context = LocalContext.current
    var step by remember { mutableIntStateOf(1) }  // 1 = primera vez, 2 = confirmar
    var firstPin by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    WearScaffold(timeText = { WearTimeText() }) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black)
                .padding(start = 24.dp, end = 24.dp, top = 34.dp, bottom = 40.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            WearText(
                if (step == 1) "Crea un PIN" else "Confirma el PIN",
                fontWeight = FontWeight.Bold,
                color = Color.White,
                textAlign = TextAlign.Center
            )
            Spacer(modifier = Modifier.height(6.dp))
            WearText(
                "Se pedirá para entrar en Ajustes",
                color = Color(0xFF888888),
                textAlign = TextAlign.Center
            )
            Spacer(modifier = Modifier.height(16.dp))
            NumericPinField(
                value = pin,
                onValueChange = {
                    pin = it
                    error = null
                    if (it.length == 4) {
                        if (step == 1) {
                            firstPin = it
                            pin = ""
                            step = 2
                        } else {
                            if (it == firstPin) {
                                AppConfig.setPin(context, it)
                                onCreated()
                            } else {
                                error = "No coincide, empieza de nuevo"
                                pin = ""
                                step = 1
                            }
                        }
                    }
                },
                label = "PIN de 4 dígitos",
                errorText = error
            )
        }
    }
}

private val SettingsFieldColors: TextFieldColors
    @Composable
    get() = OutlinedTextFieldDefaults.colors(
        focusedTextColor = Color.White,
        unfocusedTextColor = Color.White,
        focusedBorderColor = Color(0xFF3A6EA5),
        unfocusedBorderColor = Color(0xFF666666),
        cursorColor = Color.White,
        focusedContainerColor = Color(0xFF1A1A1A),
        unfocusedContainerColor = Color(0xFF1A1A1A),
        focusedLabelColor = Color(0xFFB0B0B0),
        unfocusedLabelColor = Color(0xFFB0B0B0),
    )

/** Valida que el texto tenga forma de IPv4 (4 octetos 0-255,
 * separados por puntos). No resuelve hostnames, solo IPs literales,
 * que es lo que se usa en este proyecto. */
private fun isValidIPv4(host: String): Boolean {
    val parts = host.trim().split(".")
    if (parts.size != 4) return false
    return parts.all { part ->
        val n = part.toIntOrNull()
        n != null && n in 0..255 && part == n.toString()  // sin ceros a la izquierda raros
    }
}

@Composable
fun SettingsScreen(onSaved: () -> Unit) {
    val context = LocalContext.current

    var brokerHost by remember { mutableStateOf(AppConfig.getBrokerHost(context)) }
    var watchId by remember { mutableStateOf(AppConfig.getWatchId(context)) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    WearScaffold(timeText = { WearTimeText() }) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black)
                .verticalScroll(rememberScrollState())
                .padding(start = 24.dp, end = 24.dp, top = 32.dp, bottom = 40.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            WearText("Ajustes", fontWeight = FontWeight.Bold, color = Color.White)
            Spacer(modifier = Modifier.height(18.dp))

            WearText("IP del broker MQTT", color = Color(0xFFB0B0B0))
            Spacer(modifier = Modifier.height(4.dp))
            OutlinedTextField(
                value = brokerHost,
                onValueChange = {
                    brokerHost = it
                    errorMessage = null  // limpiamos el error en cuanto el usuario vuelve a escribir
                },
                singleLine = true,
                isError = errorMessage != null,
                textStyle = androidx.compose.ui.text.TextStyle(color = Color.White, fontSize = 13.sp),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                colors = SettingsFieldColors,
                modifier = Modifier.fillMaxWidth()
            )

            if (errorMessage != null) {
                Spacer(modifier = Modifier.height(4.dp))
                WearText(
                    errorMessage ?: "",
                    color = Color(0xFFFF6B6B),
                    textAlign = TextAlign.Center
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            WearText("Watch ID", color = Color(0xFFB0B0B0))
            Spacer(modifier = Modifier.height(4.dp))
            OutlinedTextField(
                value = watchId,
                onValueChange = { watchId = it },
                singleLine = true,
                textStyle = androidx.compose.ui.text.TextStyle(color = Color.White, fontSize = 13.sp),
                colors = SettingsFieldColors,
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(modifier = Modifier.height(22.dp))

            WearChip(
                onClick = {
                    when {
                        brokerHost.isBlank() || watchId.isBlank() -> {
                            errorMessage = "Rellena ambos campos"
                        }
                        !isValidIPv4(brokerHost) -> {
                            errorMessage = "IP no válida (formato: 192.168.1.10)"
                        }
                        else -> {
                            AppConfig.setBrokerHost(context, brokerHost)
                            AppConfig.setWatchId(context, watchId)
                            onSaved()
                        }
                    }
                },
                label = { WearText("Guardar y reiniciar", color = Color.White) },
                colors = WearChipDefaults.chipColors(backgroundColor = Color(0xFF3A6EA5)),
                modifier = Modifier.fillMaxWidth(0.85f)
            )

            Spacer(modifier = Modifier.height(12.dp))

            WearText(
                "Ejemplo IP: 192.168.18.3\n(sin \"tcp://\" ni puerto, se añaden solos)",
                color = Color(0xFF888888),
                textAlign = TextAlign.Center
            )

            Spacer(modifier = Modifier.height(22.dp))
            WearText("— Conexión —", color = Color(0xFF666666))
            Spacer(modifier = Modifier.height(8.dp))
            WearChip(
                onClick = {
                    // Forzar reconexion: paramos y volvemos a arrancar el
                    // servicio, que hace un onCreate() limpio y reconecta
                    // MQTT desde cero, sin esperar al backoff automatico.
                    context.stopService(Intent(context, UwbService::class.java))
                    context.startForegroundService(Intent(context, UwbService::class.java))
                    Toast.makeText(context, "Reconectando...", Toast.LENGTH_SHORT).show()
                },
                label = { WearText("Forzar reconexión", color = Color.White) },
                colors = WearChipDefaults.chipColors(backgroundColor = Color(0xFFB35A00)),
                modifier = Modifier.fillMaxWidth(0.85f)
            )

            Spacer(modifier = Modifier.height(8.dp))
            WearChip(
                onClick = {
                    context.startActivity(Intent(context, DiagnosticsActivity::class.java))
                },
                label = { WearText("Diagnóstico", color = Color.White) },
                colors = WearChipDefaults.chipColors(backgroundColor = Color(0xFF444444)),
                modifier = Modifier.fillMaxWidth(0.85f)
            )
        }
    }
}