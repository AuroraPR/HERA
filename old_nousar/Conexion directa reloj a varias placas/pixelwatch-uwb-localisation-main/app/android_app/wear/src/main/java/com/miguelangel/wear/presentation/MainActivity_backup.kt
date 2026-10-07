//package com.miguelangel.wear.presentation
//
//import android.Manifest
//import android.content.pm.PackageManager
//import android.os.Bundle
//import android.widget.Toast
//import androidx.activity.ComponentActivity
//import androidx.activity.compose.setContent
//import androidx.activity.result.contract.ActivityResultContracts
//import androidx.compose.foundation.layout.*
//import androidx.compose.material3.ExperimentalMaterial3Api
//import androidx.compose.material3.TextField
//import androidx.compose.material3.TopAppBar
//import androidx.compose.material3.TopAppBarDefaults
//import androidx.compose.runtime.*
//import androidx.compose.ui.Alignment
//import androidx.compose.ui.Modifier
//import androidx.compose.ui.unit.dp
//import androidx.core.uwb.UwbManager
////import android.uwb.UwbManager
//import androidx.wear.compose.material.*
//import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
//
//import com.miguelangel.wear.presentation.theme.DWM3001CDKRangingTheme
//import eu.sasodoma.dwm3001cdkranging.UWBRanging
//
//class MainActivity_backup : ComponentActivity() {
//    private val permissionRequester = registerForActivityResult(
//        ActivityResultContracts.RequestPermission()
//    ) { isGranted ->
//        if (!isGranted) {
//            Toast.makeText(this, "Permiso UWB denegado", Toast.LENGTH_SHORT).show()
//        }
//    }
//
//    private lateinit var uwbManager: UwbManager
//    private lateinit var uwbRanging: UWBRanging
//    private lateinit var mqttHelper: MqttHelper
//
//    override fun onCreate(savedInstanceState: Bundle?) {
//        super.onCreate(savedInstanceState)
//
//        if (checkSelfPermission(Manifest.permission.UWB_RANGING) != PackageManager.PERMISSION_GRANTED) {
//            permissionRequester.launch(Manifest.permission.UWB_RANGING)
//        }
//
//        uwbManager = UwbManager.createInstance(this)
//        uwbRanging = UWBRanging(uwbManager)
//        mqttHelper = MqttHelper()
//
//        setContent {
//            DWM3001CDKRangingTheme {
//                Scaffold {
//                    ScalingLazyColumn(
//                        modifier = Modifier
//                            .fillMaxSize()
//                            .padding(WindowInsets.safeContent.asPaddingValues()),
//                        horizontalAlignment = Alignment.CenterHorizontally
//                    ) {
//                        item { TimeText() }
//                        item { PrepareSession() }
//                        item { LocalAddr() }
//                        item { StartStopRanging() }
//                        item { PositionText() }
//                    }
//                }
//            }
//        }
//    }
//
//    @Composable
//    fun PrepareSession() {
//        var checked by remember { mutableStateOf(false) }
//        Row(verticalAlignment = Alignment.CenterVertically) {
//            Text("Controller")
//            Checkbox(
//                checked = checked,
//                onCheckedChange = { checked = it }
//            )
//        }
//        Button(onClick = {
//            if (uwbRanging.rangingActive) {
//                Toast.makeText(this@MainActivity_backup, "Ranging session active!", Toast.LENGTH_SHORT).show()
//            } else {
//                uwbRanging.prepareSession(checked) { mac ->
//                    mqttHelper.publish("uwb/target_mac", mac)
//                }
//            }
//        }) {
//            Text("Prepare session")
//        }
//    }
//
//    @Composable
//    fun LocalAddr() {
//        Text("Local address: ${uwbRanging.localAdr}")
//    }
//
//    @Composable
//    fun StartStopRanging() {
//        var destinationAddress by remember { mutableStateOf("00:00") }
//        Text("Destination address:")
//        TextField(
//            value = destinationAddress,
//            onValueChange = { destinationAddress = it.uppercase() },
//            placeholder = { Text("00:00") }
//        )
//        if (uwbRanging.rangingActive) {
//            Button(onClick = { uwbRanging.stopRanging() }) {
//                Text("Stop Ranging")
//            }
//        } else {
//            Button(onClick = {
//                val pattern = "[0-9A-F]{2}:[0-9A-F]{2}"
//                if (destinationAddress.matches(pattern.toRegex())) {
//                    if (!uwbRanging.startRanging(destinationAddress)) {
//                        Toast.makeText(this@MainActivity_backup, "Session not initialized!", Toast.LENGTH_SHORT).show()
//                    }
//                } else {
//                    Toast.makeText(this@MainActivity_backup, "Invalid address format", Toast.LENGTH_SHORT).show()
//                }
//            }) {
//                Text("Start Ranging")
//            }
//        }
//    }
//
//    @Composable
//    fun PositionText() {
//        val position = uwbRanging.rangingPosition
//        Text("Distance: ${position.distance?.value} m")
//        Text("Azimuth: ${position.azimuth?.value} °")
//        Text("Elevation: ${position.elevation?.value} °")
//        Text("Elapsed time: ${position.elapsedRealtimeNanos} ns")
//    }
//}
