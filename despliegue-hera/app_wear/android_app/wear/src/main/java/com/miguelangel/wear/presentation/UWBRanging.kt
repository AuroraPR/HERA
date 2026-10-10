package com.miguelangel.wear.presentation

import android.util.Log
import androidx.core.uwb.RangingMeasurement
import androidx.core.uwb.RangingParameters
import androidx.core.uwb.RangingPosition
import androidx.core.uwb.RangingResult
import androidx.core.uwb.UwbAddress
import androidx.core.uwb.UwbClientSessionScope
import androidx.core.uwb.UwbComplexChannel
import androidx.core.uwb.UwbDevice
import androidx.core.uwb.UwbManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class UWBRanging(
    private val uwbManager: UwbManager,
    private val onAddressReady: ((String) -> Unit)? = null
) {
    private var rangingJob: Job? = null
    private var clientSession: UwbClientSessionScope? = null

    private var localAdr: String? = null
    private var lastDistance: Float? = null
    private var rangingActive: Boolean = false

    suspend fun prepareSession(controller: Boolean): Boolean {
        return try {
            stopRanging()

            clientSession = withContext(Dispatchers.Main) {
                if (controller) {
                    uwbManager.controllerSessionScope()
                } else {
                    uwbManager.controleeSessionScope()
                }
            }

            localAdr = clientSession?.localAddress.toString()
            onAddressReady?.invoke(localAdr ?: "--:--")

            Log.d("UWBRanging", "Sesión preparada. Local UWB address: $localAdr")
            true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.e("UWBRanging", "Error preparando sesión UWB", e)
            clientSession = null
            false
        }
    }

    fun getLocalAddress(): String? = localAdr

    fun getLastDistance(): Float? = lastDistance

    fun isRangingActive(): Boolean = rangingActive

    suspend fun startRanging(remoteAdr: String, uwbSessionId: Int = 42, multicast: Boolean = false, peers: List<String> = listOf(remoteAdr)): Boolean {
        val session = clientSession
        if (session == null) {
            Log.w("UWBRanging", "No hay sesión UWB preparada")
            return false
        }

        return try {
            lastDistance = null

            val devices = peers.map { UwbDevice(UwbAddress(it)) }

            val partnerParameters = RangingParameters(
                uwbConfigType = if (multicast) RangingParameters.CONFIG_MULTICAST_DS_TWR else RangingParameters.CONFIG_UNICAST_DS_TWR,
                sessionKeyInfo = byteArrayOf(0x08, 0x07, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06),
                complexChannel = UwbComplexChannel(9, 9),
                peerDevices = devices,
                updateRateType = RangingParameters.RANGING_UPDATE_RATE_FREQUENT,
                sessionId = uwbSessionId,
                subSessionId = 0,
                subSessionKeyInfo = null
            )

            rangingJob?.cancelAndJoin()

            rangingJob = CoroutineScope(Dispatchers.Main).launch {
                try {
                    session.prepareSession(partnerParameters).collect { result ->
                        when (result) {
                            is RangingResult.RangingResultPosition -> {
                                val distance = result.position.distance?.value
                                if (distance != null) {
                                    lastDistance = distance
                                    Log.d("UWBRanging", "Distancia recibida: $distance m")
                                }
                            }

                            is RangingResult.RangingResultPeerDisconnected -> {
                                Log.w("UWBRanging", "Peer desconectado")
                                throw CancellationException("UWB peer disconnected")
                            }
                        }
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.e("UWBRanging", "Error en el flujo de ranging", e)
                    rangingActive = false
                } finally {
                    rangingActive = false
                }
            }

            rangingActive = true
            Log.d("UWBRanging", "Ranging iniciado con $remoteAdr")
            true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.e("UWBRanging", "Error iniciando ranging con $remoteAdr", e)
            rangingActive = false
            false
        }
    }

    suspend fun stopRanging() {
        try {
            rangingActive = false
            lastDistance = null

            val job = rangingJob

            if (job != null) {
                withContext(NonCancellable) { job.cancelAndJoin() }
            }

            rangingJob = null
            clientSession = null
            Log.d("UWBRanging", "Ranging detenido")
        } catch (e: Exception) {
            Log.e("UWBRanging", "Error deteniendo ranging", e)
            throw e
        }
    }
}
