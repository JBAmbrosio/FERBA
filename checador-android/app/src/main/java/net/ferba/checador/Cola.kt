package net.ferba.checador

import android.content.Context
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/** Marcas hechas sin red. Cada una es un archivo en el almacenamiento privado
 *  de la app: sobreviven a que la tableta se apague. Se envian en orden en
 *  cuanto hay conexion y solo se borran cuando el servidor contesta que ya la
 *  tiene (el `uuid` evita duplicados si la respuesta se pierde).
 *
 *  LA HORA: lo exacto es cuanto tiempo paso desde la marca, medido con el reloj
 *  de arranque (no se puede cambiar a mano). Si la tableta se reinicio antes de
 *  enviar, ese conteo se pierde y se manda la hora de pared, corregida con el
 *  desfase contra el servidor que se midio en la ultima sincronizacion. */
object Cola {
    private const val TAG = "Checador"

    class Envio(val enviadas: Int, val rechazadas: Int, val revocada: Boolean)

    private fun carpeta(c: Context) = File(c.filesDir, "cola").apply { mkdirs() }

    private fun arranque(c: Context): Int = try {
        Settings.Global.getInt(c.contentResolver, Settings.Global.BOOT_COUNT)
    } catch (e: Exception) {
        -1
    }

    @Synchronized
    fun pendientes(c: Context): List<File> =
        (carpeta(c).listFiles { f -> f.name.endsWith(".json") } ?: emptyArray()).sortedBy { it.name }

    fun cuenta(c: Context): Int = pendientes(c).size

    /** Ultimo tipo de marca pendiente por empleado: para que la lista muestre
     *  bien "Dentro/Fuera" aunque el servidor todavia no la tenga. */
    fun estadoPendiente(c: Context): Map<Int, Boolean> {
        val m = HashMap<Int, Boolean>()
        for (f in pendientes(c)) {
            try {
                val j = JSONObject(f.readText())
                m[j.getInt("empleado")] = j.optString("tipo") == "entrada"
            } catch (e: Exception) {
            }
        }
        return m
    }

    /** Guarda una marca. Devuelve false si no se pudo escribir (no se debe confirmar). */
    @Synchronized
    fun guardar(c: Context, empleado: Int, nip: String, uuid: String, foto: String?, tipo: String): Boolean {
        return try {
            val ahora = System.currentTimeMillis()
            val j = JSONObject()
                .put("empleado", empleado)
                .put("nip", nip)
                .put("uuid", uuid)
                .put("tipo", tipo)
                .put("pared_ms", ahora + Config.desfase(c))
                .put("transcurrido_ms", SystemClock.elapsedRealtime())
                .put("arranque", arranque(c))
            if (foto != null) j.put("foto", foto)
            val destino = File(carpeta(c), "%015d-%s.json".format(ahora, uuid))
            val temporal = File(carpeta(c), destino.name + ".tmp")
            temporal.writeText(j.toString())
            temporal.renameTo(destino) && destino.length() > 0
        } catch (e: Exception) {
            Log.w(TAG, "no se pudo guardar la marca sin conexion", e)
            false
        }
    }

    /** Envia lo pendiente, en orden. Se detiene al primer fallo de red. Bloquea. */
    @Synchronized
    fun enviar(c: Context): Envio {
        var enviadas = 0
        var rechazadas = 0
        val utc = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
        utc.timeZone = TimeZone.getTimeZone("UTC")
        for (f in pendientes(c)) {
            val j = try {
                JSONObject(f.readText())
            } catch (e: Exception) {
                // Archivo ilegible: se aparta para no atorar a los demas.
                f.renameTo(File(f.parentFile, f.name + ".roto"))
                continue
            }
            val cuerpo = JSONObject()
                .put("empleado", j.getInt("empleado"))
                .put("nip", j.optString("nip"))
                .put("uuid", j.optString("uuid"))
                .put("offline", true)
                .put("fecha_dispositivo", utc.format(Date(j.optLong("pared_ms"))))
            if (j.has("foto")) cuerpo.put("foto", j.getString("foto"))
            val transcurrido = SystemClock.elapsedRealtime() - j.optLong("transcurrido_ms", -1)
            val mismoArranque = j.optInt("arranque", -2) == arranque(c) && arranque(c) >= 0
            if (mismoArranque && transcurrido >= 0) cuerpo.put("hace_ms", transcurrido)

            val r = try {
                Api.post(c, "/ferba/checador/v1/marcar", cuerpo)
            } catch (e: Exception) {
                return Envio(enviadas, rechazadas, false)
            }
            if (r.codigo == 401) return Envio(enviadas, rechazadas, true)
            if (r.codigo != 200 || r.json == null) return Envio(enviadas, rechazadas, false)
            // El servidor ya la tiene en su bitacora (aceptada o no): se puede borrar.
            if (!r.json.optBoolean("ok")) rechazadas++
            enviadas++
            f.delete()
        }
        return Envio(enviadas, rechazadas, false)
    }
}
