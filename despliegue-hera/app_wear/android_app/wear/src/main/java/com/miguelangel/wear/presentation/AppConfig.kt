package com.miguelangel.wear.presentation

import android.content.Context
import java.security.MessageDigest

/**
 * Configuracion persistente de la app: IP del broker MQTT, watch_id, y
 * el PIN de acceso a Ajustes.
 * Antes estos valores estaban fijos ("hardcoded") en UwbService.kt, lo
 * que obligaba a recompilar la app cada vez que cambiaba la red o se
 * queria usar el reloj con un watch_id distinto. Ahora se guardan en
 * SharedPreferences y se pueden editar desde SettingsActivity sin tocar
 * el codigo.
 */
object AppConfig {
    private const val PREFS_NAME = "uwb_config"
    private const val KEY_BROKER_HOST = "broker_host"
    private const val KEY_WATCH_ID = "watch_id"
    private const val KEY_PIN_HASH = "settings_pin_hash"

    // Valores por defecto: se usan solo la primera vez, antes de que el
    // usuario configure nada desde la pantalla de ajustes.
    const val DEFAULT_BROKER_HOST = "192.168.18.3"
    const val DEFAULT_WATCH_ID = "watch_01"

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun getBrokerHost(context: Context): String =
        prefs(context).getString(KEY_BROKER_HOST, DEFAULT_BROKER_HOST) ?: DEFAULT_BROKER_HOST

    fun setBrokerHost(context: Context, host: String) {
        prefs(context).edit().putString(KEY_BROKER_HOST, host.trim()).apply()
    }

    fun getWatchId(context: Context): String =
        prefs(context).getString(KEY_WATCH_ID, DEFAULT_WATCH_ID) ?: DEFAULT_WATCH_ID

    fun setWatchId(context: Context, watchId: String) {
        prefs(context).edit().putString(KEY_WATCH_ID, watchId.trim()).apply()
    }

    /** True si el usuario ya ha guardado una configuracion explicita
     * alguna vez (para poder mostrar "primera vez, configura esto"
     * si hace falta en el futuro). */
    fun hasBeenConfigured(context: Context): Boolean =
        prefs(context).contains(KEY_BROKER_HOST)

    // --- PIN de acceso a Ajustes ---
    // Se guarda como hash SHA-256, nunca en texto plano, por si alguien
    // llegara a acceder a los datos de la app (adb backup, root, etc.).

    private fun sha256(text: String): String {
        val bytes = MessageDigest.getInstance("SHA-256").digest(text.toByteArray())
        return bytes.joinToString("") { "%02x".format(it) }
    }

    fun hasPinSet(context: Context): Boolean =
        prefs(context).contains(KEY_PIN_HASH)

    fun setPin(context: Context, pin: String) {
        prefs(context).edit().putString(KEY_PIN_HASH, sha256(pin)).apply()
    }

    fun checkPin(context: Context, pin: String): Boolean {
        val storedHash = prefs(context).getString(KEY_PIN_HASH, null) ?: return false
        return storedHash == sha256(pin)
    }

    /** Por si alguna vez hace falta un "olvidé el PIN" desde otro sitio
     * (por ejemplo, borrando datos de la app manualmente en ajustes del
     * sistema, que ya borraria esto igualmente). */
    fun clearPin(context: Context) {
        prefs(context).edit().remove(KEY_PIN_HASH).apply()
    }
}