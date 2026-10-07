//package com.miguelangel.wear.presentation
//
//import android.content.Context
//import android.util.Log
//import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken
//import org.eclipse.paho.client.mqttv3.MqttCallback
//import org.eclipse.paho.client.mqttv3.MqttClient
//import org.eclipse.paho.client.mqttv3.MqttException
//import org.eclipse.paho.client.mqttv3.MqttMessage
//import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence
//
//class MqttHelper() {
//    private val serverUri = "tcp://192.168.18.23:1883" // IP de tu Raspberry Pi
            //    private val clientId = "pixel_client_" + System.currentTimeMillis()
            //    private val mqttClient: MqttClient = MqttClient(serverUri, clientId, MemoryPersistence())
            //
            //    init {
//        connect()
            //    }
            //
            //    private fun connect() {
//        try {
//            mqttClient.setCallback(object : MqttCallback {
//                override fun messageArrived(topic: String?, message: MqttMessage?) {}
//                override fun connectionLost(cause: Throwable?) {
//                    Log.e("MQTT", "Conexión perdida: ${cause?.message}")
                            //                }
//
//                override fun deliveryComplete(token: IMqttDeliveryToken?) {
//                    Log.d("MQTT", "Mensaje enviado correctamente")
                            //                }
//            })
                    //
                    //            mqttClient.connect()
                    //            Log.d("MQTT", "Conectado al broker")
                    //        } catch (e: MqttException) {
//            Log.e("MQTT", "Error conectando al broker: ${e.reasonCode} - ${e.message}", e)
                    //        }
                //
                //    }
//
//    fun publish(topic: String, payload: String) {
//        try {
//            if (!mqttClient.isConnected) mqttClient.connect()
                    //            val message = MqttMessage(payload.toByteArray())
                    //            mqttClient.publish(topic, message)
                    //        } catch (e: Exception) {
//            Log.e("MQTT", "Error publicando mensaje: ${e.message}")
                    //        }
                //    }
//}
//