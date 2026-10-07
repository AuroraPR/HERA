package com.miguelangel.wear.presentation

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.uwb.UwbManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken
import org.eclipse.paho.client.mqttv3.MqttCallback
import org.eclipse.paho.client.mqttv3.MqttClient
import org.eclipse.paho.client.mqttv3.MqttMessage
import org.json.JSONObject

class UwbService : Service() {

    private val coroutineScope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private lateinit var wakeLock: PowerManager.WakeLock
    private lateinit var mqttClient: MqttClient
    private lateinit var uwbRanging: UWBRanging

    private val watchId = "watch_01"

    private val commandTopic = "uwb/commands/$watchId"
    private val presenceTopic = "uwb/presence/$watchId"
    private val sessionTopic = "uwb/session/$watchId"

    private var localMac: String = "--:--"
    private var currentAnchor: String = "--"
    private var distanceJob: Job? = null

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

        startForegroundNotification()
        acquireWakeLock()

        saveString("watch_id", watchId)
        saveString("uwb_mac", "--:--")
        saveString("current_anchor", "--")
        saveFloat("distance", -1f)
        setState(ServiceState.STARTING)

        val uwbManager = UwbManager.createInstance(this)
        uwbRanging = UWBRanging(uwbManager) { mac ->
            localMac = mac
            saveString("uwb_mac", mac)
            Log.d("UwbService", "MAC local UWB actualizada: $mac")
        }

        initMqtt()
    }

    override fun onDestroy() {
        super.onDestroy()

        distanceJob?.cancel()
        distanceJob = null

        coroutineScope.launch {
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
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "uwb::cpu_lock")
        wakeLock.acquire()
    }

    private fun initMqtt() {
        coroutineScope.launch {
            val uri = "tcp://192.168.2.154:1883"
            val clientId = "wear_${System.currentTimeMillis()}"

            try {
                mqttClient = MqttClient(uri, clientId, null)

                mqttClient.setCallback(object : MqttCallback {
                    override fun connectionLost(cause: Throwable?) {
                        Log.e("MQTT", "Conexión perdida", cause)
                        setState(ServiceState.ERROR)
                    }

                    override fun messageArrived(topic: String?, message: MqttMessage?) {
                        val payload = message?.toString() ?: return
                        Log.d("MQTT_TEST", "Mensaje recibido en $topic: $payload")

                        if (topic == commandTopic) {
                            handleCommand(payload)
                        } else {
                            Log.d("MQTT_TEST", "Ignorado topic no esperado: $topic")
                        }
                    }

                    override fun deliveryComplete(token: IMqttDeliveryToken?) {
                    }
                })

                mqttClient.connect()
                mqttClient.subscribe(commandTopic)

                Log.d("MQTT", "Conectado al broker")
                Log.d("MQTT", "Suscrito a $commandTopic")

                publishPresenceOnline()
                setState(ServiceState.WAITING_COMMAND)

            } catch (e: Exception) {
                Log.e("MQTT", "Error inicializando MQTT", e)
                setState(ServiceState.ERROR)
            }
        }
    }

    private fun handleCommand(payload: String) {
        coroutineScope.launch {
            try {
                Log.d("MQTT_TEST", "handleCommand recibido: $payload")

                val json = JSONObject(payload)
                val action = json.optString("action", "").lowercase()
                val anchor = json.optString("anchor", "")
                val sessionId = json.optString("session_id", "")

                Log.d("MQTT_TEST", "action=$action anchor=$anchor sessionId=$sessionId")

                when (action) {
                    "start" -> {
                        Log.d("MQTT_TEST", "Entrando en START")
                        startRealRanging(anchor, sessionId)
                    }
                    "stop" -> {
                        Log.d("MQTT_TEST", "Entrando en STOP")
                        stopRealRanging()
                    }
                    else -> {
                        Log.w("MQTT_TEST", "Acción desconocida: $action")
                    }
                }
            } catch (e: Exception) {
                Log.e("MQTT_TEST", "Error procesando comando", e)
            }
        }
    }
    private fun publishPresenceOnline() {
        val payload = JSONObject().apply {
            put("watch_id", watchId)
            put("state", "online")
        }.toString()

        publish(presenceTopic, payload)
        Log.d("UwbService", "Presencia publicada: $payload")
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

    private suspend fun startRealRanging(anchor: String, sessionId: String) {
        try {
            distanceJob?.cancel()
            distanceJob = null

            uwbRanging.stopRanging()

            currentAnchor = anchor
            saveString("current_anchor", currentAnchor)
            saveFloat("distance", -1f)

            setState(ServiceState.PREPARING_UWB)

            val prepared = uwbRanging.prepareSession(controller = true)
            if (!prepared) {
                Log.e("UwbService", "No se pudo preparar UWB para ranging")
                setState(ServiceState.ERROR)
                return
            }

            val currentSessionMac = uwbRanging.getLocalAddress() ?: "--:--"
            localMac = currentSessionMac
            saveString("uwb_mac", currentSessionMac)

            publishPreparedSession(currentSessionMac, anchor, sessionId)

            val started = uwbRanging.startRanging(anchor)
            if (!started) {
                Log.e("UwbService", "No se pudo iniciar ranging con $anchor")
                setState(ServiceState.ERROR)
                return
            }

            setState(ServiceState.RANGING)
            startDistanceUpdates()

        } catch (e: Exception) {
            Log.e("UwbService", "Error iniciando ranging real", e)
            setState(ServiceState.ERROR)
        }
    }
    private suspend fun stopRealRanging() {
        try {
            distanceJob?.cancel()
            distanceJob = null

            uwbRanging.stopRanging()

            currentAnchor = "--"
            saveString("current_anchor", currentAnchor)
            saveFloat("distance", -1f)

            // OJO:
            // No borramos uwb_mac aquí, porque representa la última MAC UWB conocida.
            // La siguiente vez que se haga prepareSession() se actualizará.
            setState(ServiceState.STOPPED)

        } catch (e: Exception) {
            Log.e("UwbService", "Error deteniendo ranging real", e)
            setState(ServiceState.ERROR)
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
                } catch (e: Exception) {
                    Log.e("UwbService", "Error actualizando distancia", e)
                }
            }
        }
    }

    private fun publish(topic: String, payload: String) {
        try {
            if (::mqttClient.isInitialized && mqttClient.isConnected) {
                mqttClient.publish(topic, MqttMessage(payload.toByteArray()))
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