package com.miguelangel.wear.presentation

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.BatteryManager
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.uwb.UwbManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken
import org.eclipse.paho.client.mqttv3.MqttCallback
import org.eclipse.paho.client.mqttv3.MqttClient
import org.eclipse.paho.client.mqttv3.MqttMessage
import org.eclipse.paho.client.mqttv3.MqttConnectOptions
import org.json.JSONArray
import org.json.JSONObject

class UwbService : Service() {

    private val coroutineScope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private lateinit var wakeLock: PowerManager.WakeLock
    private lateinit var mqttClient: MqttClient
    private lateinit var uwbRanging: UWBRanging

    // Antes eran "val" fijos ("watch_01" hardcoded). Ahora se leen de
    // AppConfig (SharedPreferences), configurables desde SettingsActivity
    // sin recompilar la app.
    private var watchId: String = AppConfig.DEFAULT_WATCH_ID
    private var brokerHost: String = AppConfig.DEFAULT_BROKER_HOST

    private val commandTopic get() = "uwb/commands/$watchId"
    private val presenceTopic get() = "uwb/presence/$watchId"
    private val sessionTopic get() = "uwb/session/$watchId"

    private var localMac: String = "--:--"
    private var currentAnchor: String = "--"
    private var distanceJob: Job? = null
    private var heartbeatJob: Job? = null
    private var reconnectJob: Job? = null
    private var mqttReconnectAttempts: Int = 0
    private val commands = Channel<JSONObject>(Channel.UNLIMITED)
    @Volatile private var activeSessionId: String? = null
    private var sessionTimeoutJob: Job? = null
    private val closedSessions = LinkedHashSet<String>()

    companion object {
        private const val HEARTBEAT_INTERVAL_MS = 15_000L  // cada 15s
    }

    enum class ServiceState {
        STARTING,
        WAITING_COMMAND,
        PREPARING_UWB,
        RANGING,
        STOPPED,
        ERROR
    }

    private var currentState: ServiceState = ServiceState.STARTING

    override fun onCreate() {
        super.onCreate()
        Log.d("UwbService", "Servicio creado")

        // Cargamos la configuracion guardada (o los valores por defecto
        // si el usuario nunca ha tocado los ajustes).
        watchId = AppConfig.getWatchId(this)
        brokerHost = AppConfig.getBrokerHost(this)
        Log.d("UwbService", "Config cargada: watchId=$watchId brokerHost=$brokerHost")

        startForegroundNotification()
        acquireWakeLock()

        saveString("watch_id", watchId)
        // Conservamos la última MAC, placa y distancia entre sesiones y
        // reinicios del servicio para que la UI no pierda la última medida.
        setState(ServiceState.STARTING)

        val uwbManager = UwbManager.createInstance(this)
        uwbRanging = UWBRanging(uwbManager) { mac ->
            localMac = mac
            saveString("uwb_mac", mac)
            Log.d("UwbService", "MAC local UWB actualizada: $mac")
        }

        coroutineScope.launch {
            for (command in commands) processCommand(command)
        }
        initMqtt()
    }

    override fun onDestroy() {
        super.onDestroy()

        distanceJob?.cancel()
        distanceJob = null

        heartbeatJob?.cancel()
        heartbeatJob = null

        reconnectJob?.cancel()
        reconnectJob = null

        CoroutineScope(Dispatchers.Default).launch {
            try {
                uwbRanging.stopRanging()
            } catch (e: Exception) {
                Log.e("UwbService", "Error al parar UWB en onDestroy", e)
            }
        }

        coroutineScope.cancel()

        if (::wakeLock.isInitialized && wakeLock.isHeld) {
            wakeLock.release()
        }

        if (::mqttClient.isInitialized && mqttClient.isConnected) {
            try {
                mqttClient.disconnect()
            } catch (e: Exception) {
                Log.e("MQTT", "Error al desconectar MQTT", e)
            }
        }
        saveBoolean("mqtt_connected", false)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "uwb::cpu_lock")
        wakeLock.acquire()
    }

    private fun initMqtt() {
        saveString("mqtt_broker", brokerHost)
        coroutineScope.launch {
            connectMqtt()
        }
    }

    /**
     * Conecta al broker MQTT. Si falla (al conectar por primera vez, o
     * si se pierde la conexion mas adelante via connectionLost), se
     * reintenta solo con backoff creciente (5s, 10s, 20s... hasta un
     * tope de 60s) en vez de quedarse en ERROR para siempre esperando
     * que el usuario reabra la app a mano.
     *
     * Todo el estado relevante de MQTT (conectado/no, broker, numero de
     * reintentos, timestamp del ultimo mensaje) se guarda en
     * SharedPreferences para que la UI lo pueda mostrar en tiempo real.
     */
    private suspend fun connectMqtt(retryDelayMs: Long = 5_000L) {
        val uri = "tcp://$brokerHost:1883"
        val clientId = "wear_${System.currentTimeMillis()}"

        saveBoolean("mqtt_connected", false)
        saveString("mqtt_broker", brokerHost)

        try {
            mqttClient = MqttClient(uri, clientId, null)
            mqttClient.setTimeToWait(500L)

            mqttClient.setCallback(object : MqttCallback {
                override fun connectionLost(cause: Throwable?) {
                    activeSessionId?.let { sid ->
                        commands.trySend(JSONObject().put("action", "stop").put("session_id", sid))
                    }
                    Log.e("MQTT", "Conexión perdida, se intentará reconectar", cause)
                    val errorMsg = cause?.message ?: "Conexión perdida"
                    saveBoolean("mqtt_connected", false)
                    saveString("mqtt_last_error", errorMsg)
                    logDiagnosticError(errorMsg)
                    setState(ServiceState.ERROR)
                    scheduleReconnect()
                }

                override fun messageArrived(topic: String?, message: MqttMessage?) {
                    val payload = message?.toString() ?: return
                    Log.d("MQTT_TEST", "Mensaje recibido en $topic: $payload")
                    saveLong("mqtt_last_message_at", System.currentTimeMillis())

                    if (topic == commandTopic && message?.isRetained != true) {
                        handleCommand(payload)
                    } else {
                        Log.d("MQTT_TEST", "Ignorado topic no esperado: $topic")
                    }
                }

                override fun deliveryComplete(token: IMqttDeliveryToken?) {
                }
            })

            mqttClient.connect(MqttConnectOptions().apply {
                connectionTimeout = 3
                keepAliveInterval = 10
                setWill(presenceTopic, JSONObject().put("watch_id", watchId)
                    .put("state", "offline").toString().toByteArray(), 1, true)
            })
            mqttClient.subscribe(commandTopic, 1)

            Log.d("MQTT", "Conectado al broker $uri")
            Log.d("MQTT", "Suscrito a $commandTopic")

            mqttReconnectAttempts = 0
            saveInt("mqtt_reconnect_attempts", 0)
            saveBoolean("mqtt_connected", true)
            saveString("mqtt_last_error", "")
            saveLong("mqtt_connected_at", System.currentTimeMillis())

            publishPresenceOnline()
            startHeartbeat()
            setState(ServiceState.WAITING_COMMAND)

        } catch (e: Exception) {
            Log.e("MQTT", "Error conectando a $uri, reintentando en ${retryDelayMs}ms", e)
            val errorMsg = e.message ?: "Error desconocido"
            saveBoolean("mqtt_connected", false)
            saveString("mqtt_last_error", errorMsg)
            logDiagnosticError(errorMsg)
            setState(ServiceState.ERROR)
            scheduleReconnect(retryDelayMs)
        }
    }

    private fun scheduleReconnect(nextDelayMs: Long = 5_000L) {
        mqttReconnectAttempts++
        saveInt("mqtt_reconnect_attempts", mqttReconnectAttempts)

        reconnectJob?.cancel()
        reconnectJob = coroutineScope.launch {
            delay(nextDelayMs)
            // Backoff creciente, con tope en 60s, para no machacar la
            // red/bateria si el broker tarda en volver.
            val nextDelay = (nextDelayMs * 2).coerceAtMost(60_000L)
            connectMqtt(nextDelay)
        }
    }

    private fun handleCommand(payload: String) {
        try {
            val command = JSONObject(payload)
            command.put("received_at_ms", SystemClock.elapsedRealtime())
            commands.trySend(command)
        } catch (e: Exception) {
            Log.e("MQTT_TEST", "Comando inválido", e)
        }
    }

    private fun publishStopped(sessionId: String) {
        publish(sessionTopic, JSONObject().apply {
            put("watch_id", watchId)
            put("session_id", sessionId)
            put("state", "stopped")
        }.toString())
    }

    private suspend fun closeAttempt(sessionId: String) {
        if (activeSessionId == sessionId) {
            sessionTimeoutJob?.cancel()
            sessionTimeoutJob = null
            stopRealRanging()
            activeSessionId = null
        }
        closedSessions.add(sessionId)
        while (closedSessions.size > 64) closedSessions.remove(closedSessions.first())
        // ACK only after cancelAndJoin has completed. A stale STOP never
        // cancels the current session, but can ACK its own old attempt.
        publishStopped(sessionId)
    }

    private suspend fun processCommand(command: JSONObject) {
        val sid = command.optString("session_id", "")
        if (sid.isBlank()) return
        try {
            when (command.optString("action", "").lowercase()) {
                "reset" -> {
                    if (sid !in closedSessions) {
                        activeSessionId?.let { closeAttempt(it) }
                        stopRealRanging()
                    }
                    closeAttempt(sid)
                }
                "stop" -> closeAttempt(sid)
                "start" -> {
                    if (sid in closedSessions) {
                        publishStopped(sid)
                        return
                    }
                    if (activeSessionId == sid) return // QoS duplicate; no deadline extension.
                    if (activeSessionId != null) {
                        publish(sessionTopic, JSONObject().put("state", "busy")
                            .put("watch_id", watchId).put("session_id", sid).toString())
                        return
                    }
                    val budget = command.optLong("timelimit_ms", 5000L).coerceIn(1L, 5000L)
                    val received = command.optLong("received_at_ms", SystemClock.elapsedRealtime())
                    val remaining = budget - (SystemClock.elapsedRealtime() - received)
                    if (remaining <= 0) { closeAttempt(sid); return }
                    activeSessionId = sid
                    val deadline = SystemClock.elapsedRealtime() + remaining
                    val ok = withTimeoutOrNull(remaining) {
                        startRealRanging(command.optString("anchor"), sid,
                            command.optInt("uwb_session_id", 42), command.optBoolean("multicast", false),
                            command.optJSONArray("anchors")?.let { array ->
                                (0 until array.length()).map { array.getString(it) }
                            } ?: listOf(command.optString("anchor")))
                    } ?: false
                    if (!ok) { closeAttempt(sid); return }
                    sessionTimeoutJob = coroutineScope.launch {
                        delay((deadline - SystemClock.elapsedRealtime()).coerceAtLeast(1L))
                        commands.send(JSONObject().put("action", "stop").put("session_id", sid))
                    }
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // No stopped ACK on failure: the coordinator retains ownership.
            Log.e("UwbService", "Error procesando sesión $sid", e)
            setState(ServiceState.ERROR)
        }
    }
    private fun publishPresenceOnline() {
        val batteryLevel = readBatteryLevel()
        saveInt("battery_level", batteryLevel)

        val payload = JSONObject().apply {
            put("watch_id", watchId)
            put("state", "online")
            put("battery_level", batteryLevel)
        }.toString()

        publish(presenceTopic, payload, retain = true)
        Log.d("UwbService", "Presencia publicada: $payload")
    }

    private fun readBatteryLevel(): Int {
        return try {
            val bm = getSystemService(Context.BATTERY_SERVICE) as BatteryManager
            bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        } catch (e: Exception) {
            Log.e("UwbService", "Error leyendo batería", e)
            -1
        }
    }

    private fun startHeartbeat() {
        heartbeatJob?.cancel()
        heartbeatJob = coroutineScope.launch {
            while (isActive) {
                delay(HEARTBEAT_INTERVAL_MS)
                if (::mqttClient.isInitialized && mqttClient.isConnected) {
                    publishPresenceOnline()
                    Log.d("UwbService", "Heartbeat de presencia enviado")
                } else {
                    Log.w("UwbService", "Heartbeat omitido: MQTT no conectado")
                }
            }
        }
    }

    private fun publishPreparedSession(uwbMac: String, anchor: String, sessionId: String) {
        val payload = JSONObject().apply {
            put("watch_id", watchId)
            put("uwb_mac", uwbMac)
            put("anchor", anchor)
            put("state", "prepared")
            put("session_id", sessionId)
        }.toString()

        Log.d("MQTT_TEST", "Publicando prepared con sessionId=$sessionId mac=$uwbMac payload=$payload")

        publish(sessionTopic, payload)

        Log.d("MQTT_TEST", "Publish ejecutado en topic $sessionTopic")
    }

    private suspend fun startRealRanging(anchor: String, sessionId: String, uwbSessionId: Int, multicast: Boolean = false, peers: List<String> = listOf(anchor)): Boolean {
        try {
            distanceJob?.cancelAndJoin()
            distanceJob = null

            uwbRanging.stopRanging()

            currentAnchor = anchor
            saveString("current_anchor", currentAnchor)

            setState(ServiceState.PREPARING_UWB)

            val prepared = uwbRanging.prepareSession(controller = true)
            if (!prepared) {
                Log.e("UwbService", "No se pudo preparar UWB para ranging")
                setState(ServiceState.ERROR)
                return false
            }

            val currentSessionMac = uwbRanging.getLocalAddress() ?: "--:--"
            localMac = currentSessionMac
            saveString("uwb_mac", currentSessionMac)

            val started = uwbRanging.startRanging(anchor, uwbSessionId, multicast, peers)
            if (!started) {
                Log.e("UwbService", "No se pudo iniciar ranging con $anchor")
                setState(ServiceState.ERROR)
                return false
            }

            setState(ServiceState.RANGING)
            startDistanceUpdates()
            publishPreparedSession(currentSessionMac, anchor, sessionId)
            return true

        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.e("UwbService", "Error iniciando ranging real", e)
            setState(ServiceState.ERROR)
            return false
        }
    }
    private suspend fun stopRealRanging() {
        try {
            distanceJob?.cancelAndJoin()
            distanceJob = null

            uwbRanging.stopRanging()

            currentAnchor = "--"
            // No borramos la última placa ni la última distancia válida.
            // Se actualizarán cuando comience una nueva sesión.

            // OJO:
            // No borramos uwb_mac aquí, porque representa la última MAC UWB conocida.
            // La siguiente vez que se haga prepareSession() se actualizará.
            setState(ServiceState.STOPPED)

        } catch (e: Exception) {
            Log.e("UwbService", "Error deteniendo ranging real", e)
            setState(ServiceState.ERROR)
            throw e
        }
    }

    private fun startDistanceUpdates() {
        distanceJob?.cancel()

        distanceJob = coroutineScope.launch {
            while (currentState == ServiceState.RANGING) {
                try {
                    val distance = uwbRanging.getLastDistance()
                    if (distance != null) {
                        saveFloat("distance", distance)
                        Log.d("UwbService", "Distancia actualizada: $distance m")
                    }
                    delay(1000)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.e("UwbService", "Error actualizando distancia", e)
                }
            }
        }
    }

    private fun publish(topic: String, payload: String, retain: Boolean = false) {
        try {
            if (::mqttClient.isInitialized && mqttClient.isConnected) {
                mqttClient.publish(topic, MqttMessage(payload.toByteArray()).apply {
                    qos = 1
                    isRetained = retain
                })
            }
        } catch (e: Exception) {
            Log.e("MQTT", "Error publicando en $topic", e)
        }
    }

    private fun saveString(key: String, value: String) {
        getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
            .edit()
            .putString(key, value)
            .apply()
    }

    private fun saveFloat(key: String, value: Float) {
        getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
            .edit()
            .putFloat(key, value)
            .apply()
    }

    private fun saveBoolean(key: String, value: Boolean) {
        getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
            .edit()
            .putBoolean(key, value)
            .apply()
    }

    private fun saveInt(key: String, value: Int) {
        getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
            .edit()
            .putInt(key, value)
            .apply()
    }

    private fun saveLong(key: String, value: Long) {
        getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
            .edit()
            .putLong(key, value)
            .apply()
    }

    /**
     * Guarda un error en el historico de diagnostico (ultimos 10, con
     * marca de tiempo), para que la pantalla de Diagnostico pueda
     * mostrarlos aunque hayan pasado varios errores desde el ultimo.
     * Antes solo se guardaba "mqtt_last_error" (se pisaba cada vez);
     * esto mantiene ADEMAS ese campo (para la pantalla principal) y un
     * historico completo aparte.
     */
    private fun logDiagnosticError(message: String) {
        if (message.isBlank()) return
        val prefs = getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
        val historyJson = prefs.getString("error_history", "[]") ?: "[]"
        val array = try {
            JSONArray(historyJson)
        } catch (e: Exception) {
            JSONArray()
        }

        array.put(JSONObject().apply {
            put("ts", System.currentTimeMillis())
            put("message", message)
        })

        // Nos quedamos solo con los ultimos 10
        val trimmed = JSONArray()
        val start = (array.length() - 10).coerceAtLeast(0)
        for (i in start until array.length()) {
            trimmed.put(array.get(i))
        }

        prefs.edit().putString("error_history", trimmed.toString()).apply()
    }

    private fun setState(newState: ServiceState) {
        currentState = newState
        saveString("service_state", newState.name)
        Log.d("UwbService", "Nuevo estado: ${newState.name}")
    }

    private fun startForegroundNotification() {
        val channelId = "uwb"
        val notificationManager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(channelId, "UWB", NotificationManager.IMPORTANCE_LOW)
        notificationManager.createNotificationChannel(channel)

        val notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle("UWB Service")
            .setContentText("Activo")
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .build()

        startForeground(1, notification)
    }
}
