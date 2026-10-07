package com.miguelangel.wear.presentation

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.miguelangel.wear.presentation.theme.DWM3001CDKRangingTheme
import org.json.JSONArray

import androidx.wear.compose.material.Chip as WearChip
import androidx.wear.compose.material.ChipDefaults as WearChipDefaults
import androidx.wear.compose.material.Scaffold as WearScaffold
import androidx.wear.compose.material.Text as WearText
import androidx.wear.compose.material.TimeText as WearTimeText

/**
 * Pantalla de diagnostico: muestra los ultimos 10 errores registrados
 * por UwbService (con marca de tiempo relativa), del mas reciente al
 * mas antiguo. Util para ver que ha ido fallando sin depender de tener
 * el movil con Logcat a mano.
 */
class DiagnosticsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            DWM3001CDKRangingTheme {
                DiagnosticsScreen()
            }
        }
    }
}

private data class DiagnosticEntry(val timestampMs: Long, val message: String)

private fun loadErrorHistory(context: Context): List<DiagnosticEntry> {
    val prefs = context.getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
    val json = prefs.getString("error_history", "[]") ?: "[]"
    return try {
        val array = JSONArray(json)
        val list = mutableListOf<DiagnosticEntry>()
        for (i in 0 until array.length()) {
            val obj = array.getJSONObject(i)
            list.add(DiagnosticEntry(obj.getLong("ts"), obj.getString("message")))
        }
        list.reversed()  // el mas reciente primero
    } catch (e: Exception) {
        emptyList()
    }
}

private fun fmtAgo(timestampMs: Long, nowMs: Long): String {
    val secs = (nowMs - timestampMs) / 1000
    return when {
        secs < 60 -> "hace ${secs}s"
        secs < 3600 -> "hace ${secs / 60}m"
        secs < 86400 -> "hace ${secs / 3600}h"
        else -> "hace ${secs / 86400}d"
    }
}

@Composable
fun DiagnosticsScreen() {
    val context = LocalContext.current
    var entries by remember { mutableStateOf(loadErrorHistory(context)) }
    val now = remember { System.currentTimeMillis() }

    WearScaffold(timeText = { WearTimeText() }) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black)
                .verticalScroll(rememberScrollState())
                .padding(start = 18.dp, end = 18.dp, top = 32.dp, bottom = 40.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            WearText("Diagnóstico", fontWeight = FontWeight.Bold, color = Color.White)
            Spacer(modifier = Modifier.height(4.dp))
            WearText(
                "Últimos ${entries.size} errores",
                color = Color(0xFF888888)
            )
            Spacer(modifier = Modifier.height(14.dp))

            if (entries.isEmpty()) {
                WearText(
                    "Sin errores registrados 🎉",
                    color = Color(0xFF2ECC71),
                    textAlign = TextAlign.Center
                )
            } else {
                entries.forEach { entry ->
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 6.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        WearText(fmtAgo(entry.timestampMs, now), color = Color(0xFFF1C40F))
                        WearText(
                            entry.message,
                            color = Color(0xFFFF6B6B),
                            textAlign = TextAlign.Center
                        )
                    }
                    Spacer(
                        modifier = Modifier
                            .fillMaxWidth(0.7f)
                            .height(1.dp)
                            .background(Color(0xFF333333))
                    )
                }
            }

            Spacer(modifier = Modifier.height(16.dp))
            WearChip(
                onClick = {
                    context.getSharedPreferences("uwb_data", Context.MODE_PRIVATE)
                        .edit().remove("error_history").apply()
                    entries = emptyList()
                },
                label = { WearText("Borrar historial", color = Color.White) },
                colors = WearChipDefaults.chipColors(backgroundColor = Color(0xFF444444)),
                modifier = Modifier.fillMaxWidth(0.85f)
            )
        }
    }
}