package net.ferba.checador

import android.Manifest
import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.provider.Settings
import android.util.Log

/** Tableta fija en el checador. Solo actua si IT dejo la app como DUENA del
 *  equipo (una vez, por cable):
 *    adb shell dpm set-device-owner net.ferba.checador/.AdminReceiver
 *  Si no lo es, la app funciona igual pero se puede salir de ella. */
object Kiosko {
    private const val TAG = "Checador"

    private fun dpm(c: Context) =
        c.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager

    private fun admin(c: Context) = ComponentName(c, AdminReceiver::class.java)

    fun esDuena(c: Context): Boolean = try {
        dpm(c).isDeviceOwnerApp(c.packageName)
    } catch (e: Exception) {
        false
    }

    /** Fija la pantalla. Sin ser duena no hace nada (evita el aviso de "fijar app"). */
    fun fijar(a: Activity) {
        if (!esDuena(a)) return
        try {
            val d = dpm(a)
            val yo = admin(a)
            d.setLockTaskPackages(yo, arrayOf(a.packageName))
            // Camara concedida sin preguntar.
            d.setPermissionGrantState(
                yo, a.packageName, Manifest.permission.CAMERA,
                DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED,
            )
            // El checador es la pantalla de inicio: al encender abre directo.
            val inicio = IntentFilter(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_HOME)
                addCategory(Intent.CATEGORY_DEFAULT)
            }
            d.addPersistentPreferredActivity(yo, inicio, ComponentName(a, ChecadorActivity::class.java))
            d.setKeyguardDisabled(yo, true)
            // Pantalla encendida mientras este conectada a la corriente (AC, USB o inalambrica).
            val enchufes = BatteryManager.BATTERY_PLUGGED_AC or BatteryManager.BATTERY_PLUGGED_USB or
                BatteryManager.BATTERY_PLUGGED_WIRELESS
            d.setGlobalSetting(yo, Settings.Global.STAY_ON_WHILE_PLUGGED_IN, enchufes.toString())
            a.startLockTask()
        } catch (e: Exception) {
            Log.w(TAG, "no se pudo fijar la pantalla", e)
        }
    }

    /** Mantenimiento: suelta la pantalla para que IT pueda salir. */
    fun soltar(a: Activity) {
        try {
            a.stopLockTask()
        } catch (e: Exception) {
            Log.w(TAG, "no estaba fijada", e)
        }
        if (!esDuena(a)) return
        try {
            dpm(a).clearPackagePersistentPreferredActivities(admin(a), a.packageName)
        } catch (e: Exception) {
            Log.w(TAG, "no se pudo soltar el inicio", e)
        }
    }
}
