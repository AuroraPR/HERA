// MainActivity.kt
package com.miguelangel.wear.presentation

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
import android.os.Build
//import androidx.appcompat.app.AlertDialog
import android.provider.Settings
import android.net.Uri
import android.app.AlertDialog

class MainActivity : ComponentActivity() {

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
        logPermissionStates()
        if (allGranted) {
            Log.d("MainActivity", "Permisos de primer plano concedidos")
            startUwbService()
            //checkBackgroundLocationPermission()
        } else {
            Toast.makeText(this, "Permisos requeridos no concedidos", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.d("MainActivity", "onCreate iniciado")
        permissionLauncher.launch(FG_PERMS)

        setContent {
            DWM3001CDKRangingTheme {
                MainWearUi()
            }
        }
    }

    /*
    private fun checkBackgroundLocationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.ACCESS_BACKGROUND_LOCATION
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            AlertDialog.Builder(this)
                .setTitle("Permiso de ubicación en segundo plano")
                .setMessage("Para escanear redes Wi-Fi incluso cuando la app no esté en primer plano, permite 'Siempre' la ubicación en ajustes.")
                .setPositiveButton("Abrir ajustes") { _, _ ->
                    val intent = Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.fromParts("package", packageName, null)
                    )
                    startActivity(intent)
                }
                .setNegativeButton("Cancelar", null)
                .show()
        }
    }

     */

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
}

@Composable
fun MainWearUi() {
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

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("UWB Enabled", fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Spacer(modifier = Modifier.height(12.dp))
        Text("Battery Level: 98%", fontSize = 14.sp)
        Spacer(modifier = Modifier.height(8.dp))
        Text("Anchor ID: 00:01", fontSize = 14.sp)
        Spacer(modifier = Modifier.height(8.dp))
        Text("Distance: 1.2 m", fontSize = 14.sp)
        Spacer(modifier = Modifier.height(8.dp))
        Text("Estimated Zone: Bath", fontSize = 14.sp)
        Spacer(modifier = Modifier.height(8.dp))
        Text("Status: Cycling", fontSize = 14.sp)
    }
}