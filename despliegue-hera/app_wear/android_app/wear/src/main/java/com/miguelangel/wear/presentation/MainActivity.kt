// MainActivity.kt
package com.miguelangel.wear.presentation

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.wear.ambient.AmbientModeSupport
import com.miguelangel.wear.presentation.theme.DWM3001CDKRangingTheme
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// Componentes NATIVOS de Wear OS (androidx.wear.compose.material), con
// alias para no chocar con los de material3 (pensados para movil) que
// ya usa el resto del fichero. Estos son los que dan el aspecto y
// comportamiento "correctos" para una pantalla de reloj: TimeText en
// la parte de arriba, ScalingLazyColumn con el efecto de encogido en
// los bordes de la pantalla redonda, Chip con la forma tactil adecuada.
import androidx.wear.compose.material.Chip as WearChip
import androidx.wear.compose.material.ChipDefaults as WearChipDefaults
import androidx.wear.compose.material.Scaffold as WearScaffold
import androidx.wear.compose.material.Text as WearText
import androidx.wear.compose.material.TimeText as WearTimeText

// Colores forzados explicitamente (no dependemos del tema dinamico, que
// en reloj puede dar combinaciones con muy poco contraste y dejar el
// texto practicamente invisible sobre el fondo).
val WearBackground = Color.Black
val WearTextPrimary = Color.White
val WearTextSecondary = Color(0xFFB0B0B0)

class MainActivity : FragmentActivity(), AmbientModeSupport.AmbientCallbackProvider {

    private val FG_PERMS = arrayOf(
        Manifest.permission.UWB_RANGING,
        Manifest.permission.ACCESS_FINE_LOCATION,
        Manifest.permission.ACCESS_WIFI_STATE,
        Manifest.permission.CHANGE_WIFI_STATE,
        Manifest.permission.WAKE_LOCK
    )

    private var permissionsGranted by mutableStateOf(false)
    // Se actualiza desde los callbacks de AmbientModeSupport (fuera de
    // Compose), y Compose reacciona automaticamente al ser un
    // mutableStateOf, redibujando la UI en modo simplificado o normal.
    private var isAmbient by mutableStateOf(false)
    private lateinit var ambientController: AmbientModeSupport.AmbientController

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val allGranted = FG_PERMS.all { permissions[it] == true }
        logPermissionStates()
        permissionsGranted = allGranted
        if (allGranted) {
            Log.d("MainActivity", "Permisos de primer plano concedidos")
            startUwbService()
        } else {
            Log.w("MainActivity", "Permisos no concedidos, se mostrará pantalla de reintento")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.d("MainActivity", "onCreate iniciado")

        // Registramos esta Activity para recibir los eventos de entrada/
        // salida del modo ambiente. Sin esto, Wear OS aplica su
        // comportamiento generico por defecto (difuminar + mostrar solo
        // la hora), que es justo lo que queremos personalizar.
        ambientController = AmbientModeSupport.attach(this)

        permissionsGranted = FG_PERMS.all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }

        if (permissionsGranted) {
            startUwbService()
        } else {
            permissionLauncher.launch(FG_PERMS)
        }

        setContent {
            DWM3001CDKRangingTheme {
                if (permissionsGranted) {
                    MainWearUi(
                        isAmbient = isAmbient,
                        onOpenSettings = {
                            startActivity(Intent(this, SettingsActivity::class.java))
                        }
                    )
                } else {
                    PermissionsNeededUi(
                        onRetry = { permissionLauncher.launch(FG_PERMS) },
                        onOpenAppSettings = {
                            val intent = Intent(
                                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                Uri.fromParts("package", packageName, null)
                            )
                            startActivity(intent)
                        }
                    )
                }
            }
        }
    }

    private fun logPermissionStates() {
        val perms = listOf(
            Manifest.permission.UWB_RANGING,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_WIFI_STATE,
            Manifest.permission.CHANGE_WIFI_STATE,
            Manifest.permission.ACCESS_BACKGROUND_LOCATION
        )
        perms.forEach { perm ->
            val granted = ContextCompat.checkSelfPermission(this, perm) ==
                    PackageManager.PERMISSION_GRANTED
            Log.d("PermChecks", "Permiso $perm → concedido? $granted")
        }
    }

    private fun startUwbService() {
        Log.d("MainActivity", "startUwbService() llamado")
        startForegroundService(Intent(this, UwbService::class.java))
    }

    /**
     * Implementacion exigida por AmbientModeSupport.AmbientCallbackProvider.
     * Solo actualizamos la variable de estado "isAmbient"; Compose se
     * encarga solo de redibujar la UI en cuanto cambia, gracias a que es
     * un mutableStateOf.
     */
    override fun getAmbientCallback(): AmbientModeSupport.AmbientCallback {
        return object : AmbientModeSupport.AmbientCallback() {
            override fun onEnterAmbient(ambientDetails: Bundle?) {
                super.onEnterAmbient(ambientDetails)
                Log.d("MainActivity", "Entrando en modo ambiente")
                isAmbient = true
            }

            override fun onExitAmbient() {
                super.onExitAmbient()
                Log.d("MainActivity", "Saliendo de modo ambiente")
                isAmbient = false
            }

            override fun onUpdateAmbient() {
                super.onUpdateAmbient()
                // Wear OS llama a esto periodicamente (normalmente cada
                // minuto) mientras seguimos en ambiente, para que
                // podamos refrescar la hora u otro dato minimo sin
                // gastar bateria de mas. Como usamos remember{} con la
                // hora actual dentro del propio Composable, forzamos un
                // pequeño "toggle" para que se vuelva a leer.
                isAmbient = true
            }
        }
    }
}

@Composable
fun PermissionsNeededUi(onRetry: () -> Unit, onOpenAppSettings: () -> Unit) {
    WearScaffold(
        timeText = { WearTimeText() }
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(WearBackground)
                .verticalScroll(rememberScrollState())
                .padding(top = 32.dp, bottom = 32.dp, start = 18.dp, end = 18.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            WearText(
                "Faltan permisos",
                fontWeight = FontWeight.Bold,
                color = WearTextPrimary,
                textAlign = TextAlign.Center
            )
            Spacer(modifier = Modifier.height(8.dp))
            WearText(
                "Esta app necesita permisos de UWB y ubicación para funcionar.",
                color = WearTextSecondary,
                textAlign = TextAlign.Center
            )
            Spacer(modifier = Modifier.height(14.dp))
            WearChip(
                onClick = onRetry,
                label = { WearText("Reintentar", color = Color.White) },
                colors = WearChipDefaults.chipColors(backgroundColor = Color(0xFF3A6EA5)),
                modifier = Modifier.fillMaxWidth(0.9f)
            )
            Spacer(modifier = Modifier.height(8.dp))
            WearChip(
                onClick = onOpenAppSettings,
                label = { WearText("Ajustes de la app", color = Color.White) },
                colors = WearChipDefaults.chipColors(backgroundColor = Color(0xFF444444)),
                modifier = Modifier.fillMaxWidth(0.9f)
            )
            Spacer(modifier = Modifier.height(10.dp))
            WearText(
                "Si no aparece el diálogo, puede que los hayas denegado de forma permanente: usa 'Ajustes de la app'.",
                color = WearTextSecondary,
                textAlign = TextAlign.Center
            )
        }
    }
}

/** Color del punto de estado, segun el ServiceState que publica UwbService. */
private fun stateColor(state: String): Color = when (state) {
    "RANGING" -> Color(0xFF2ECC71)          // verde: todo funcionando
    "WAITING_COMMAND" -> Color(0xFF3A9FD8)  // azul: conectado, esperando
    "PREPARING_UWB" -> Color(0xFFF1C40F)    // amarillo: en transicion
    "STARTING" -> Color(0xFF888888)         // gris: arrancando
    "STOPPED" -> Color(0xFF888888)          // gris: parado (normal)
    "ERROR" -> Color(0xFFE74C3C)            // rojo: algo va mal
    else -> Color(0xFF888888)
}

@Composable
fun StatusDot(color: Color, sizeDp: androidx.compose.ui.unit.Dp = 10.dp) {
    androidx.compose.foundation.Canvas(modifier = Modifier.size(sizeDp)) {
        drawCircle(color = color)
    }
}

/**
 * Icono de señal WiFi dibujado a mano (3 barras de altura creciente),
 * sin depender de la libreria de iconos de Material (evitamos añadir
 * una dependencia nueva solo para esto). El color indica conectado
 * (verde) o no (rojo/gris).
 */
@Composable
fun WifiIcon(connected: Boolean, sizeDp: androidx.compose.ui.unit.Dp = 16.dp) {
    val color = if (connected) Color(0xFF2ECC71) else Color(0xFF666666)
    androidx.compose.foundation.Canvas(modifier = Modifier.size(sizeDp)) {
        val barWidth = size.width / 5
        val gap = barWidth / 2
        // 3 barras, cada una mas alta que la anterior
        val heights = listOf(size.height * 0.35f, size.height * 0.65f, size.height)
        heights.forEachIndexed { i, h ->
            val x = i * (barWidth + gap)
            drawRect(
                color = color,
                topLeft = androidx.compose.ui.geometry.Offset(x, size.height - h),
                size = androidx.compose.ui.geometry.Size(barWidth, h)
            )
        }
    }
}

/**
 * Icono de bateria dibujado a mano: un contorno rectangular con "polo"
 * a la derecha, y relleno proporcional al nivel real (0-100), coloreado
 * segun el nivel (verde/amarillo/rojo), igual criterio que usamos en el
 * dashboard web del orquestador.
 */
@Composable
fun BatteryIcon(levelPercent: Int, sizeDp: androidx.compose.ui.unit.Dp = 16.dp) {
    val fillColor = when {
        levelPercent < 0 -> Color(0xFF666666)
        levelPercent <= 20 -> Color(0xFFE74C3C)
        levelPercent <= 50 -> Color(0xFFF1C40F)
        else -> Color(0xFF2ECC71)
    }
    androidx.compose.foundation.Canvas(
        modifier = Modifier.size(width = sizeDp * 1.6f, height = sizeDp)
    ) {
        val bodyWidth = size.width * 0.85f
        val poleWidth = size.width * 0.10f
        val strokeW = size.height * 0.08f

        // Contorno del cuerpo de la bateria
        drawRect(
            color = Color(0xFFAAAAAA),
            topLeft = androidx.compose.ui.geometry.Offset(0f, 0f),
            size = androidx.compose.ui.geometry.Size(bodyWidth, size.height),
            style = androidx.compose.ui.graphics.drawscope.Stroke(width = strokeW)
        )
        // Polo positivo (el saliente a la derecha)
        drawRect(
            color = Color(0xFFAAAAAA),
            topLeft = androidx.compose.ui.geometry.Offset(bodyWidth + 1f, size.height * 0.3f),
            size = androidx.compose.ui.geometry.Size(poleWidth, size.height * 0.4f)
        )
        // Relleno proporcional al nivel de carga
        if (levelPercent > 0) {
            val fillRatio = (levelPercent.coerceIn(0, 100) / 100f)
            val innerPad = strokeW * 1.3f
            val innerWidth = (bodyWidth - innerPad * 2) * fillRatio
            drawRect(
                color = fillColor,
                topLeft = androidx.compose.ui.geometry.Offset(innerPad, innerPad),
                size = androidx.compose.ui.geometry.Size(
                    innerWidth,
                    size.height - innerPad * 2
                )
            )
        }
    }
}

/**
 * Version simplificada de la pantalla para el modo ambiente: sin color,
 * sin animaciones, sin botones (no hay interaccion en ambiente), solo
 * la hora y un resumen de una linea. Se lee directamente de
 * SharedPreferences cada vez que se recompone (llamado por
 * onUpdateAmbient, normalmente 1 vez por minuto).
 */
@Composable
fun AmbientWearUi() {
    val context = LocalContext.current
    val prefs = context.getSharedPreferences("uwb_data", Context.MODE_PRIVATE)

    val state = prefs.getString("service_state", "STARTING") ?: "STARTING"
    val mqttConnected = prefs.getBoolean("mqtt_connected", false)
    val now = remember { SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date()) }

    val summary = when {
        !mqttConnected -> "Sin conexión"
        state == "RANGING" -> "Midiendo"
        state == "WAITING_COMMAND" -> "Conectado"
        else -> state
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        WearText(now, color = Color.White, fontWeight = FontWeight.Bold)
        Spacer(modifier = Modifier.height(4.dp))
        WearText(summary, color = Color(0xFF888888))
    }
}

@Composable
fun MainWearUi(isAmbient: Boolean, onOpenSettings: () -> Unit) {
    val context = LocalContext.current

    // En modo ambiente mostramos una version minima, en blanco/negro,
    // sin scroll ni botones (no se puede interactuar en ambiente de
    // todas formas) — hora + un resumen de una linea del estado. Esto
    // sustituye el comportamiento por defecto de Wear OS (difuminar +
    // solo la hora), dandonos control real sobre que se ve.
    if (isAmbient) {
        AmbientWearUi()
        return
    }

    var watchId by remember { mutableStateOf("--") }
    var state by remember { mutableStateOf("STARTING") }
    var anchor by remember { mutableStateOf("--") }
    var distance by remember { mutableStateOf("--") }
    var uwbMac by remember { mutableStateOf("--:--") }

    var mqttConnected by remember { mutableStateOf(false) }
    var mqttBroker by remember { mutableStateOf("--") }
    var mqttReconnectAttempts by remember { mutableIntStateOf(0) }
    var mqttLastMessageAgo by remember { mutableStateOf("nunca") }
    var mqttLastError by remember { mutableStateOf("") }
    var batteryLevel by remember { mutableIntStateOf(-1) }

    LaunchedEffect(Unit) {
        while (true) {
            val prefs = context.getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
            watchId = prefs.getString("watch_id", AppConfig.getWatchId(context)) ?: "watch_01"
            state = prefs.getString("service_state", "STARTING") ?: "STARTING"
            anchor = prefs.getString("current_anchor", "--") ?: "--"
            uwbMac = prefs.getString("uwb_mac", "--:--") ?: "--:--"
            val dist = prefs.getFloat("distance", -1f)
            distance = if (dist >= 0f) "%.2f m".format(dist) else "--"
            batteryLevel = prefs.getInt("battery_level", -1)

            mqttConnected = prefs.getBoolean("mqtt_connected", false)
            mqttBroker = prefs.getString("mqtt_broker", "--") ?: "--"
            mqttReconnectAttempts = prefs.getInt("mqtt_reconnect_attempts", 0)
            mqttLastError = prefs.getString("mqtt_last_error", "") ?: ""

            val lastMsgAt = prefs.getLong("mqtt_last_message_at", 0L)
            mqttLastMessageAgo = if (lastMsgAt == 0L) {
                "nunca"
            } else {
                val secs = (System.currentTimeMillis() - lastMsgAt) / 1000
                when {
                    secs < 60 -> "hace ${secs}s"
                    secs < 3600 -> "hace ${secs / 60}m"
                    else -> "hace ${secs / 3600}h"
                }
            }

            delay(2000)
        }
    }

    // Cambiamos ScalingLazyColumn por un Column + scroll normal: el
    // efecto de "encogido" por item de ScalingLazyColumn tiene un coste
    // de animacion en cada frame de scroll que, en el hardware de un
    // reloj, se nota como tirones/saltos. Un scroll simple es mas
    // ligero y, para una pantalla de solo texto como esta, no se
    // pierde nada importante visualmente.
    WearScaffold(
        timeText = { WearTimeText() }
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(WearBackground)
                .verticalScroll(rememberScrollState())
                .padding(top = 32.dp, bottom = 32.dp, start = 12.dp, end = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(color = stateColor(state))
                Spacer(modifier = Modifier.width(6.dp))
                WearText("UWB Wear", fontWeight = FontWeight.Bold, color = WearTextPrimary)
            }
            Spacer(modifier = Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                BatteryIcon(levelPercent = batteryLevel)
                Spacer(modifier = Modifier.width(4.dp))
                WearText(
                    if (batteryLevel >= 0) "$batteryLevel%" else "--",
                    color = WearTextSecondary
                )
            }
            Spacer(modifier = Modifier.height(6.dp))
            WearText("Watch: $watchId", color = WearTextSecondary)
            WearText("Estado: $state", color = WearTextSecondary)
            WearText("Anchor: $anchor", color = WearTextSecondary)
            WearText("Distancia: $distance", color = WearTextSecondary)
            WearText("MAC UWB: $uwbMac", color = WearTextSecondary)

            Spacer(modifier = Modifier.height(8.dp))
            WearText("— MQTT —", color = Color(0xFF666666))
            Spacer(modifier = Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                WifiIcon(connected = mqttConnected)
                Spacer(modifier = Modifier.width(6.dp))
                WearText(
                    if (mqttConnected) "Conectado" else "Desconectado",
                    color = WearTextSecondary
                )
            }
            WearText("Broker: $mqttBroker", color = Color(0xFF888888))
            WearText("Último mensaje: $mqttLastMessageAgo", color = Color(0xFF888888))
            if (!mqttConnected && mqttReconnectAttempts > 0) {
                WearText("Reintentos: $mqttReconnectAttempts", color = Color(0xFFF1C40F))
            }
            if (mqttLastError.isNotBlank()) {
                WearText(
                    mqttLastError,
                    color = Color(0xFFFF6B6B),
                    textAlign = TextAlign.Center
                )
            }

            Spacer(modifier = Modifier.height(10.dp))
            WearChip(
                onClick = onOpenSettings,
                label = { WearText("Ajustes", color = Color.White) },
                colors = WearChipDefaults.chipColors(backgroundColor = Color(0xFF444444)),
                modifier = Modifier.fillMaxWidth(0.85f)
            )
        }
    }
}