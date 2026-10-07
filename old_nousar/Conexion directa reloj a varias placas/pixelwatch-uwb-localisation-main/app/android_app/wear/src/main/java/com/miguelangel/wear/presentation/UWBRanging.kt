package eu.sasodoma.dwm3001cdkranging

import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.uwb.RangingMeasurement
import androidx.core.uwb.RangingParameters
import androidx.core.uwb.RangingPosition
import androidx.core.uwb.RangingResult
import androidx.core.uwb.UwbAddress
import androidx.core.uwb.UwbClientSessionScope
import androidx.core.uwb.UwbComplexChannel
import androidx.core.uwb.UwbDevice
import androidx.core.uwb.UwbManager
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.catch


class UWBRanging(
    private val uwbManager: UwbManager,
    private val onAddressReady: ((String) -> Unit)? = null
) {
    private lateinit var rangingJob: Job
    private var clientSession: UwbClientSessionScope? = null
    var rangingResults by mutableStateOf(mapOf<String, RangingPosition>())

    var localAdr by mutableStateOf("XX:XX")
    var rangingActive by mutableStateOf(false)
    var rangingPosition by mutableStateOf(
        RangingPosition(
            RangingMeasurement(0F),
            RangingMeasurement(0F),
            RangingMeasurement(0F),
            0L
        )
    )

    fun prepareSession(controller: Boolean) {
        CoroutineScope(Dispatchers.Main.immediate).launch {
            clientSession = if (controller)
                uwbManager.controllerSessionScope()
            else
                uwbManager.controleeSessionScope()

            localAdr = clientSession?.localAddress.toString()
            onAddressReady?.invoke(localAdr)
        }
    }

    private val uwbExceptionHandler = CoroutineExceptionHandler { _, e ->
        Log.e("UWBRanging", "Excepción global en UWB", e)
    }

    fun startRanging(remoteAdr: String): Boolean {
        if (clientSession == null) {
            return false
        }

        //val remoteUwbAdr = UwbAddress(remoteAdr)

        val devices = listOf(
            UwbDevice(UwbAddress(remoteAdr))
        )

        val partnerParameters = RangingParameters(
            uwbConfigType = RangingParameters.CONFIG_UNICAST_DS_TWR,
            sessionKeyInfo = byteArrayOf(0x08, 0x07, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06),
            complexChannel = UwbComplexChannel(9, 9),
            //peerDevices = listOf(UwbDevice(UwbAddress("00:01"))),
            peerDevices = devices,
            updateRateType = RangingParameters.RANGING_UPDATE_RATE_FREQUENT,
            sessionId = 42,
            subSessionId = 0,
            subSessionKeyInfo = null
        )

        rangingJob = CoroutineScope(Dispatchers.Main.immediate).launch {
            val sessionFlow = clientSession?.prepareSession(partnerParameters)

            sessionFlow?.collect {
                when (it) {
                    is RangingResult.RangingResultPosition -> {
                        Log.d("collect", it.position.distance?.value.toString() + " m")
                        rangingPosition = it.position
                    }
                    is RangingResult.RangingResultPeerDisconnected -> {
                        Log.d("collect", "Peer disconnected")
                        stopRanging()
                    }
                }
            }
        }


        /*
        // Si había un trabajo anterior, lo cancelamos
        //rangingJob?.cancel()
        //rangingJob?.cancel()
        rangingJob = CoroutineScope(Dispatchers.Main.immediate + uwbExceptionHandler).launch {
            clientSession!!.prepareSession(partnerParameters)
                .onStart { Log.d("UWBRanging", "⏳ Sesión UWB arrancando con $devices") }
                .catch { e ->
                    Log.e("UWBRanging", "❌ Flow de ranging falló", e)
                }
                .collect { result ->
                    when (result) {
                        is RangingResult.RangingResultPosition -> {
                            val addr = result.device.address.toString()
                            val dist = result.position.distance?.value
                            Log.v("UWBRanging", "📶 $addr → $dist m")
                            rangingResults = rangingResults + (addr to result.position)
                        }
                        is RangingResult.RangingResultPeerDisconnected -> {
                            Log.w("UWBRanging", "⚠️ Peer desconectado: ${result.device.address}")
                        }
                    }
                }
        }
        */
        rangingActive = true
        return true
    }

    fun stopRanging() {
        if (::rangingJob.isInitialized) {
            rangingActive = false
            clientSession = null
            rangingJob.cancel()
        }
    }
}
