package com.miguelangel.wear.presentation

import android.app.*
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.uwb.*
import eu.sasodoma.dwm3001cdkranging.UWBRanging
import kotlinx.coroutines.*
import org.eclipse.paho.client.mqttv3.*
import java.util.*
import android.net.wifi.WifiManager
import android.net.wifi.ScanResult
import org.eclipse.paho.android.service.MqttAndroidClient
import android.os.BatteryManager


class UwbService : Service() {

    private val coroutineScope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private lateinit var wakeLock: PowerManager.WakeLock
    private lateinit var mqttClient: MqttClient
    private lateinit var uwbRanging: UWBRanging
    private var localMac: String = "--:--"

    override fun onCreate() {
        super.onCreate()
        Log.d("UwbService", "Servicio creado")
        startForegroundNotification()
        acquireWakeLock()
        initMqtt()

        // Inicializamos UWB y capturamos la MAC local
        val uwbManager = UwbManager.createInstance(this)
        uwbRanging = UWBRanging(uwbManager) { mac ->
            localMac = mac
            Log.d("UwbService", "MAC local actualizada: $mac")
        }

        startUwbLoop()
    }

    override fun onDestroy() {
        super.onDestroy()
        coroutineScope.cancel()
        if (::wakeLock.isInitialized && wakeLock.isHeld) wakeLock.release()
        uwbRanging.stopRanging()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "uwb::cpu_lock")
        wakeLock.acquire()
    }

    private fun initMqtt() {
        val uri = "tcp://192.168.1.119:1883"
        val clientId = "wear_${System.currentTimeMillis()}"
        try {
            mqttClient = MqttClient(uri, clientId, null)
            mqttClient.connect()
            Log.d("MQTT", "Conectado al broker")
        } catch (e: MqttException) {
            Log.e("MQTT", "Error MQTT: ${e.reasonCode} - ${e.message}", e)
        }
    }

    private fun startUwbLoop() {
        coroutineScope.launch {
            val responders = listOf("00:01", "00:02", "00:03", "00:04")
            while (isActive) {
                // Publicar nivel de batería en su propio topic
                val battery = readBattery()
                publish("uwb/battery_level", battery.toString())
                Log.d("UwbService", "Batería publicada: $battery%")

                for (addr in responders) {
                    // Publicar MAC local y responder en el mismo mensaje


                    try {
                        val battery = readBattery()
                        publish("uwb/battery_level", battery.toString())
                        Log.d("UwbService", "Batería publicada: $battery%")
                        uwbRanging.prepareSession(controller = true)
                        delay(500)
                        val payload = "{\"mac\":\"$localMac\",\"responder\":\"$addr\"}"
                        publish("uwb/target_mac", payload)
                        Log.d("UwbService", "Publicados mac y responder: mac=$localMac responder=$addr")
                        if (uwbRanging.startRanging(addr)) {
                            Log.d("UwbService", "Ranging con $addr iniciado")
                            delay(2000)
                            uwbRanging.stopRanging()
                            Log.d("UwbService", "Ranging con $addr detenido")
                        } else {
                            Log.w("UwbService", "Fallo al iniciar ranging con $addr")
                        }
                    } catch (e: Exception) {
                        Log.e("UwbService", "Error con $addr", e)
                    }
                    delay(500)
                }
                Log.d("UwbService", "Ciclo completo, pausa 2s")
                delay(50000)
            }
        }
    }

    private fun readBattery(): Int {
        val bm = getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        return bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
    }

    private fun publish(topic: String, payload: String) {
        if (::mqttClient.isInitialized && mqttClient.isConnected) {
            mqttClient.publish(topic, MqttMessage(payload.toByteArray()))
        }
    }

    private fun startForegroundNotification() {
        val id = "uwb"
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(id, "UWB", NotificationManager.IMPORTANCE_LOW)
        nm.createNotificationChannel(channel)
        val notif = NotificationCompat.Builder(this, id)
            .setContentTitle("UWB Service")
            .setContentText("Activo")
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .build()
        startForeground(1, notif)
    }
}



