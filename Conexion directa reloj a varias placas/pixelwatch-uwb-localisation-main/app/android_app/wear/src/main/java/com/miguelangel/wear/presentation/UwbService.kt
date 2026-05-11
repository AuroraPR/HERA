package com.miguelangel.wear.presentation

import android.app.*
import android.content.*
import android.os.*
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.uwb.UwbManager
import eu.sasodoma.dwm3001cdkranging.UWBRanging
import kotlinx.coroutines.*
import org.eclipse.paho.client.mqttv3.*
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence
import org.json.JSONObject
import android.os.BatteryManager

class UwbService : Service() {

    private val coroutineScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private lateinit var wakeLock: PowerManager.WakeLock
    private lateinit var mqttClient: MqttAsyncClient
    private lateinit var uwbRanging: UWBRanging
    private var localMac: String = "--:--"

    private var distanceListener: DistanceListener? = null
    private val binder = LocalBinder()

    private var isMqttConnected = false

    inner class LocalBinder : Binder() {
        fun getService(): UwbService = this@UwbService
    }

    interface DistanceListener {
        fun onDistanceReceived(timestampMs: Long, distanceCm: Double, rssi: Double, mac: String)
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        startForegroundNotification()
        acquireWakeLock()
        initMqtt()

        val uwbManager = UwbManager.createInstance(this)
        uwbRanging = UWBRanging(uwbManager) { mac ->
            localMac = mac
        }

        startUwbLoop()
    }

    override fun onDestroy() {
        super.onDestroy()
        coroutineScope.cancel()
        if (::wakeLock.isInitialized && wakeLock.isHeld) wakeLock.release()
        uwbRanging.stopRanging()
        if (::mqttClient.isInitialized && isMqttConnected) {
            try {
                mqttClient.disconnect()
                mqttClient.close()
            } catch (e: MqttException) {
                Log.e("MQTT", "Error disconnecting", e)
            }
        }
        distanceListener = null
    }

    fun setDistanceListener(listener: DistanceListener?) {
        distanceListener = listener
    }

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "uwb::cpu_lock")
        wakeLock.acquire()
    }

    private fun initMqtt() {
        val uri = "tcp://10.187.4.101:1883"
        val clientId = "wear_${System.currentTimeMillis()}"
        val persistence = MemoryPersistence()

        try {
            mqttClient = MqttAsyncClient(uri, clientId, persistence)
            val options = MqttConnectOptions().apply {
                isAutomaticReconnect = true
                connectionTimeout = 10
                keepAliveInterval = 60
                isCleanSession = true
            }

            mqttClient.setCallback(object : MqttCallback {
                override fun connectionLost(cause: Throwable?) {
                    Log.e("MQTT", "Conexión perdida", cause)
                    isMqttConnected = false
                }

                override fun messageArrived(topic: String?, message: MqttMessage?) {
                    Log.d("MQTT", "Mensaje recibido en tópico: $topic")
                    if (topic == "uwb/distance" && message != null) {
                        val payload = String(message.payload)
                        Log.d("MQTT", "Payload: $payload")
                        try {
                            val json = JSONObject(payload)
                            val ts = json.getLong("ts_epoch_ms")
                            val distance = json.getDouble("distance_cm")
                            val rssi = json.getDouble("rssi_dbm")
                            val mac = json.getString("mac")
                            Log.d("MQTT", "Distancia parseada: ${distance}cm, mac=$mac")
                            Handler(Looper.getMainLooper()).post {
                                distanceListener?.onDistanceReceived(ts, distance, rssi, mac)
                                Log.d("MQTT", "Notificado listener")
                            }
                        } catch (e: Exception) {
                            Log.e("MQTT", "Error parseando JSON", e)
                        }
                    }
                }
                override fun deliveryComplete(token: IMqttDeliveryToken?) {
                    // No necesario
                }
            })

            mqttClient.connect(options, null, object : IMqttActionListener {
                override fun onSuccess(asyncActionToken: IMqttToken?) {
                    Log.d("MQTT", "Conectado al broker")
                    isMqttConnected = true
                    try {
                        mqttClient.subscribe("uwb/distance", 1, null, object : IMqttActionListener {
                            override fun onSuccess(asyncActionToken: IMqttToken?) {
                                Log.d("MQTT", "Suscrito a uwb/distance")
                            }
                            override fun onFailure(asyncActionToken: IMqttToken?, exception: Throwable?) {
                                Log.e("MQTT", "Fallo suscripción", exception)
                            }
                        })
                    } catch (e: MqttException) {
                        Log.e("MQTT", "Error al suscribir", e)
                    }
                }

                override fun onFailure(asyncActionToken: IMqttToken?, exception: Throwable?) {
                    Log.e("MQTT", "Fallo de conexión", exception)
                }
            })
        } catch (e: MqttException) {
            Log.e("MQTT", "Error inicializando MQTT", e)
        }
    }

    private fun startUwbLoop() {
        coroutineScope.launch {
            val responders = listOf("00:01", "00:02")
            while (isActive) {
                val battery = readBattery()
                publish("uwb/battery_level", battery.toString())
                for (addr in responders) {
                    try {
                        uwbRanging.prepareSession(controller = true)
                        delay(500)
                        val payload = "{\"mac\":\"$localMac\",\"responder\":\"$addr\"}"
                        publish("uwb/target_mac", payload)
                        if (uwbRanging.startRanging(addr)) {
                            delay(2000)
                            uwbRanging.stopRanging()
                        }
                    } catch (e: Exception) {
                        Log.e("UwbService", "Error con $addr", e)
                    }
                    delay(500)
                }
                delay(50000)
            }
        }
    }

    private fun readBattery(): Int {
        val bm = getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        return bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
    }

    private fun publish(topic: String, payload: String) {
        if (::mqttClient.isInitialized && isMqttConnected) {
            try {
                val message = MqttMessage(payload.toByteArray()).apply { qos = 1 }
                mqttClient.publish(topic, message, null, null)
            } catch (e: MqttException) {
                Log.e("MQTT", "Error publicando en $topic", e)
            }
        }
    }

    private fun startForegroundNotification() {
        val channelId = "uwb"
        val channelName = "UWB Service"
        val notificationManager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(channelId, channelName, NotificationManager.IMPORTANCE_LOW)
            notificationManager.createNotificationChannel(channel)
        }
        val notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle("UWB Service")
            .setContentText("Activo")
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .build()
        startForeground(1, notification)
    }
}