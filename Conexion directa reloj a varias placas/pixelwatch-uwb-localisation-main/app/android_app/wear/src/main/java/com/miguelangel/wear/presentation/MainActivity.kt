package com.miguelangel.wear.presentation

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.miguelangel.wear.presentation.theme.DWM3001CDKRangingTheme
import kotlinx.coroutines.delay
import java.io.BufferedWriter
import java.io.File
import java.io.FileWriter
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*
import kotlin.math.sqrt

class MainActivity : ComponentActivity(), SensorEventListener {

    private val FG_PERMS = arrayOf(
        Manifest.permission.UWB_RANGING,
        Manifest.permission.ACCESS_FINE_LOCATION,
        Manifest.permission.ACCESS_WIFI_STATE,
        Manifest.permission.CHANGE_WIFI_STATE,
        Manifest.permission.WAKE_LOCK
    )

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val allGranted = FG_PERMS.all { permissions[it] == true }
        if (allGranted) {
            Log.d("MainActivity", "Permisos concedidos")
            startUwbService()
        } else {
            Toast.makeText(this, "Permisos requeridos no concedidos", Toast.LENGTH_LONG).show()
        }
    }

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private var gyroscope: Sensor? = null
    private var magnetometer: Sensor? = null
    private var rotationVector: Sensor? = null

    private var isRecording by mutableStateOf(false)
    private var currentLabel by mutableStateOf("0")
    private var statusText by mutableStateOf("Idle")
    private val medianValues = mutableStateMapOf<String, String>()

    private var csvWriter: BufferedWriter? = null
    private var currentSecondEpoch: Long = -1L
    private val magnitudesBySensor = mutableMapOf<String, MutableList<Float>>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        permissionLauncher.launch(FG_PERMS)

        sensorManager = getSystemService(SENSOR_SERVICE) as SensorManager
        accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        gyroscope = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
        magnetometer = sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD)
        rotationVector = sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)

        refreshAvailabilityUI()

        setContent {
            DWM3001CDKRangingTheme {
                MainWearUi(
                    isRecording = isRecording,
                    currentLabel = currentLabel,
                    statusText = statusText,
                    medianValues = medianValues,
                    onPlayStopClick = {
                        if (isRecording) stopRecording() else startRecording()
                    },
                    onLabelClick = { label ->
                        currentLabel = if (currentLabel == label) "0" else label
                    },
                    onLabelZeroClick = {
                        currentLabel = "0"
                    }
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        registerAvailableSensors()
        refreshAvailabilityUI()
    }

    override fun onPause() {
        super.onPause()
        unregisterAllSensors()
        if (isRecording) stopRecording() else closeWriterSafely()
    }

    override fun onDestroy() {
        unregisterAllSensors()
        closeWriterSafely()
        super.onDestroy()
    }

    private fun registerAvailableSensors() {
        accelerometer?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
        gyroscope?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
        magnetometer?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
        rotationVector?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
    }

    private fun unregisterAllSensors() {
        sensorManager.unregisterListener(this)
    }

    private fun startRecording() {
        currentSecondEpoch = -1L
        magnitudesBySensor.clear()

        val filename = "sensor_log_${timestampForFilename(System.currentTimeMillis())}.csv"
        val outputFile = File(filesDir, filename)

        try {
            csvWriter = BufferedWriter(FileWriter(outputFile, false)).apply {
                write("timestamp_ns,sensor,label,x,y,z,w,accuracy\n")
                flush()
            }
            isRecording = true
            statusText = "Recording..."
        } catch (_: IOException) {
            isRecording = false
            closeWriterSafely()
            statusText = "Error"
        }
    }

    private fun stopRecording() {
        flushSecondMedians(force = true)
        isRecording = false
        statusText = "Idle"
        closeWriterSafely()
    }

    private fun closeWriterSafely() {
        try {
            csvWriter?.flush()
            csvWriter?.close()
        } catch (_: IOException) {
        } finally {
            csvWriter = null
        }
    }

    override fun onSensorChanged(event: SensorEvent) {
        val sensorName = sensorTypeToName(event.sensor.type) ?: return
        val timestampNs = event.timestamp

        val nowMillis = System.currentTimeMillis()
        val secondEpoch = nowMillis / 1000L

        if (currentSecondEpoch == -1L) {
            currentSecondEpoch = secondEpoch
        } else if (secondEpoch != currentSecondEpoch) {
            flushSecondMedians(force = false)
            currentSecondEpoch = secondEpoch
        }

        val x = event.values.getOrElse(0) { 0f }
        val y = event.values.getOrElse(1) { 0f }
        val z = event.values.getOrElse(2) { 0f }
        val magnitude = sqrt(x * x + y * y + z * z)

        magnitudesBySensor.getOrPut(sensorName) { mutableListOf() }.add(magnitude)

        if (isRecording) {
            when (event.sensor.type) {
                Sensor.TYPE_ACCELEROMETER -> appendCsvLine(timestampNs, "acc", currentLabel, x, y, z, null, null)
                Sensor.TYPE_GYROSCOPE -> appendCsvLine(timestampNs, "gyr", currentLabel, x, y, z, null, null)
                Sensor.TYPE_MAGNETIC_FIELD -> appendCsvLine(timestampNs, "mag", currentLabel, x, y, z, null, null)
                Sensor.TYPE_ROTATION_VECTOR -> {
                    val w = event.values.getOrElse(3) { 0f }
                    val accuracy = if (event.values.size > 4) event.values[4] else Float.NaN
                    appendCsvLine(timestampNs, "rot", currentLabel, x, y, z, w, accuracy)
                }
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    private fun appendCsvLine(timestampNs: Long, sensor: String, label: String,
                              x: Float, y: Float, z: Float, w: Float?, accuracy: Float?) {
        val wStr = w?.toString() ?: ""
        val accStr = if (accuracy != null && !accuracy.isNaN()) accuracy.toString() else ""
        val row = "$timestampNs,$sensor,$label,$x,$y,$z,$wStr,$accStr\n"
        try {
            csvWriter?.write(row)
        } catch (_: IOException) {
            stopRecording()
        }
    }

    private fun flushSecondMedians(force: Boolean) {
        val sensors = listOf("accelerometer", "gyroscope", "magnetometer", "rotationVector")
        for (sensor in sensors) {
            val values = magnitudesBySensor[sensor]
            val medianVal = if (values.isNullOrEmpty()) null else median(values)
            updateMedianText(sensor, medianVal)
        }
        if (force || magnitudesBySensor.isNotEmpty()) {
            magnitudesBySensor.clear()
        }
    }

    private fun updateMedianText(sensor: String, medianVal: Float?) {
        val value = when {
            !isSensorAvailable(sensor) -> "N/A"
            medianVal == null -> "--"
            else -> String.format(Locale.US, "%.4f", medianVal)
        }
        val sensorLabel = when (sensor) {
            "accelerometer" -> "Acc"
            "gyroscope" -> "Gyr"
            "magnetometer" -> "Mag"
            "rotationVector" -> "Rot"
            else -> sensor
        }
        medianValues[sensor] = "$sensorLabel: $value"
    }

    private fun refreshAvailabilityUI() {
        listOf("accelerometer", "gyroscope", "magnetometer", "rotationVector").forEach {
            updateMedianText(it, null)
        }
    }

    private fun isSensorAvailable(sensor: String): Boolean {
        return when (sensor) {
            "accelerometer" -> accelerometer != null
            "gyroscope" -> gyroscope != null
            "magnetometer" -> magnetometer != null
            "rotationVector" -> rotationVector != null
            else -> false
        }
    }

    private fun sensorTypeToName(sensorType: Int): String? {
        return when (sensorType) {
            Sensor.TYPE_ACCELEROMETER -> "accelerometer"
            Sensor.TYPE_GYROSCOPE -> "gyroscope"
            Sensor.TYPE_MAGNETIC_FIELD -> "magnetometer"
            Sensor.TYPE_ROTATION_VECTOR -> "rotationVector"
            else -> null
        }
    }

    private fun median(values: List<Float>): Float {
        val sorted = values.sorted()
        val mid = sorted.size / 2
        return if (sorted.size % 2 == 0) (sorted[mid - 1] + sorted[mid]) / 2f else sorted[mid]
    }

    private fun timestampForFilename(timeMillis: Long): String {
        return SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date(timeMillis))
    }

    private fun startUwbService() {
        startForegroundService(Intent(this, UwbService::class.java))
    }
}

@Composable
fun MainWearUi(
    isRecording: Boolean,
    currentLabel: String,
    statusText: String,
    medianValues: Map<String, String>,
    onPlayStopClick: () -> Unit,
    onLabelClick: (String) -> Unit,
    onLabelZeroClick: () -> Unit
) {
    val context = LocalContext.current
    var mac by remember { mutableStateOf("XX:XX") }
    var distance by remember { mutableStateOf("--") }

    LaunchedEffect(Unit) {
        while (true) {
            val prefs = context.getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
            mac = prefs.getString("local_mac", "XX:XX") ?: "XX:XX"
            val dist = prefs.getFloat("distance", -1f)
            distance = if (dist >= 0f) "%.2f".format(dist) else "--"
            delay(1000)
        }
    }

    Scaffold(modifier = Modifier.fillMaxSize()) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 12.dp, vertical = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text("UWB Enabled", fontSize = 20.sp, fontWeight = FontWeight.Bold)
            Spacer(modifier = Modifier.height(8.dp))
            Text("MAC: $mac", fontSize = 14.sp)
            Text("Distance: $distance m", fontSize = 14.sp)
            Text("Battery Level: 98%", fontSize = 14.sp)
            Text("Anchor ID: 00:01", fontSize = 14.sp)
            Text("Estimated Zone: Bath", fontSize = 14.sp)
            Text("Status: Cycling", fontSize = 14.sp)

            HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))

            Text("Sensor Logger", fontSize = 18.sp, fontWeight = FontWeight.Medium)
            Spacer(modifier = Modifier.height(4.dp))
            Text(statusText, fontSize = 14.sp, color = MaterialTheme.colorScheme.primary)

            Spacer(modifier = Modifier.height(8.dp))
            Button(onClick = onPlayStopClick, modifier = Modifier.fillMaxWidth(0.7f)) {
                Text(if (isRecording) "Stop" else "Start")
            }

            Spacer(modifier = Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                LabelButton("A", currentLabel == "A") { onLabelClick("A") }
                LabelButton("B", currentLabel == "B") { onLabelClick("B") }
                LabelButton("0", currentLabel == "0") { onLabelZeroClick() }
            }

            Spacer(modifier = Modifier.height(8.dp))
            medianValues["accelerometer"]?.let { SensorText(it) }
            medianValues["gyroscope"]?.let { SensorText(it) }
            medianValues["magnetometer"]?.let { SensorText(it) }
            medianValues["rotationVector"]?.let { SensorText(it) }
        }
    }
}

@Composable
fun LabelButton(text: String, isSelected: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        modifier = Modifier.size(48.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = if (isSelected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Text(text, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun SensorText(text: String) {
    Text(
        text = text,
        fontSize = 12.sp,
        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)
    )
}