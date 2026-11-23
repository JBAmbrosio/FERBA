package net.ferba.checador

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/** La app se actualiza sola con el APK que IT publica en Odoo.
 *
 *  Tres candados antes de instalar:
 *   1. la huella SHA-256 del archivo es la que anuncio Odoo (no llego danado ni cambiado);
 *   2. el archivo ES esta app y trae una version MAYOR a la instalada;
 *   3. Android rechaza cualquier APK que no este firmado con la misma llave.
 *
 *  Si la app es duena del equipo, instala en silencio cuando nadie la esta usando.
 *  Si no lo es, Android exige que alguien confirme: queda lista y la instala IT
 *  desde el menu de mantenimiento. */
object Actualizador {
    private const val TAG = "Checador"
    private const val ACCION = "net.ferba.checador.INSTALACION"

    private fun archivo(c: Context, codigo: Int) = File(c.cacheDir, "checador-$codigo.apk")

    /** Version descargada y verificada que espera instalarse, o 0. */
    fun lista(c: Context): Int {
        val codigo = Config.actualizacionLista(c)
        if (codigo <= BuildConfig.VERSION_CODE || !archivo(c, codigo).exists()) return 0
        return codigo
    }

    /** Se llama tras cada sincronizacion (hilo de fondo). `app` es lo que anuncio Odoo. */
    fun revisar(c: Context, app: JSONObject?) {
        val codigo = app?.optInt("version_code", 0) ?: 0
        if (app == null || codigo <= BuildConfig.VERSION_CODE) {
            // Nada mas nuevo (o IT retiro la version): se limpia lo que hubiera.
            if (Config.actualizacionLista(c) != 0 || Config.actualizacion(c).isNotEmpty()) {
                Config.guardarActualizacion(c, 0, "")
                c.cacheDir.listFiles { f -> f.name.startsWith("checador-") }?.forEach { it.delete() }
            }
            return
        }
        val nombre = app.optString("version", codigo.toString())
        if (lista(c) == codigo) return
        // Ese mismo archivo ya se bajo y no sirvio: no se gasta red en repetirlo.
        if (app.optString("sha256").isNotEmpty() && app.optString("sha256") == Config.apkRechazado(c)) return
        val destino = archivo(c, codigo)
        val temporal = File(c.cacheDir, destino.name + ".tmp")
        try {
            Config.guardarActualizacion(c, 0, "Descargando $nombre")
            if (!Api.descargar(c, "/ferba/checador/v1/app/$codigo", temporal)) {
                Config.guardarActualizacion(c, 0, "No se pudo descargar $nombre")
                return
            }
            val huella = sha256(temporal)
            if (!huella.equals(app.optString("sha256"), ignoreCase = true)) {
                temporal.delete()
                Config.guardarActualizacion(c, 0, "Descarga de $nombre danada: se reintenta")
                return
            }
            val info = c.packageManager.getPackageArchiveInfo(temporal.path, 0)
            @Suppress("DEPRECATION")
            val codigoReal = if (info == null) -1 else
                if (Build.VERSION.SDK_INT >= 28) info.longVersionCode.toInt() else info.versionCode
            if (info == null || info.packageName != c.packageName) {
                temporal.delete()
                Config.guardarApkRechazado(c, huella)
                Config.guardarActualizacion(c, 0, "$nombre no es la app del checador")
                return
            }
            if (codigoReal <= BuildConfig.VERSION_CODE) {
                temporal.delete()
                Config.guardarApkRechazado(c, huella)
                Config.guardarActualizacion(c, 0,
                    "$nombre trae el numero de version $codigoReal, no mayor al instalado")
                return
            }
            c.cacheDir.listFiles { f -> f.name.startsWith("checador-") && f != temporal }
                ?.forEach { it.delete() }
            if (!temporal.renameTo(destino)) return
            Config.guardarActualizacion(c, codigo, "Lista para instalar $nombre")
        } catch (e: Exception) {
            Log.w(TAG, "actualizacion: fallo la descarga", e)
            temporal.delete()
            Config.guardarActualizacion(c, 0, "No se pudo descargar $nombre")
        }
    }

    private const val REINTENTO_MS = 6 * 60 * 60 * 1000L

    /** ¿Toca intentar instalar sola? No, si Android la rechazo hace poco. */
    fun puedeSola(c: Context): Boolean {
        val hasta = Config.esperaInstalar(c)
        val ahora = System.currentTimeMillis()
        // Si el reloj se movio hacia atras mas de lo que dura la espera, se ignora.
        return lista(c) != 0 && (ahora >= hasta || hasta - ahora > REINTENTO_MS)
    }

    fun rechazada(c: Context) {
        Config.guardarEsperaInstalar(c, System.currentTimeMillis() + REINTENTO_MS)
    }

    /** Entrega el APK al instalador de Android. Devuelve false si no habia nada o fallo. */
    fun instalar(c: Context): Boolean {
        val codigo = lista(c)
        if (codigo == 0) return false
        val apk = archivo(c, codigo)
        return try {
            val instalador = c.packageManager.packageInstaller
            val p = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
            p.setAppPackageName(c.packageName)
            p.setSize(apk.length())
            if (Build.VERSION.SDK_INT >= 31) {
                p.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
            val id = instalador.createSession(p)
            instalador.openSession(id).use { s ->
                s.openWrite("checador", 0, apk.length()).use { salida ->
                    apk.inputStream().use { it.copyTo(salida) }
                    s.fsync(salida)
                }
                val aviso = Intent(c, InstalacionReceiver::class.java).setAction(ACCION)
                val banderas = PendingIntent.FLAG_UPDATE_CURRENT or
                    (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                s.commit(PendingIntent.getBroadcast(c, id, aviso, banderas).intentSender)
            }
            Config.guardarActualizacion(c, codigo, "Instalando")
            true
        } catch (e: Exception) {
            Log.w(TAG, "actualizacion: no se pudo instalar", e)
            Config.guardarActualizacion(c, codigo, "No se pudo instalar: ${e.javaClass.simpleName}")
            rechazada(c)
            false
        }
    }

    private fun sha256(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { flujo ->
            val bloque = ByteArray(64 * 1024)
            while (true) {
                val n = flujo.read(bloque)
                if (n < 0) break
                md.update(bloque, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}

/** Lo que contesta el instalador de Android. */
class InstalacionReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        val estado = i.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val codigo = Config.actualizacionLista(c)
        when (estado) {
            PackageInstaller.STATUS_SUCCESS -> Config.guardarActualizacion(c, 0, "")
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                // La app no es duena del equipo: Android pide que alguien confirme.
                Config.guardarActualizacion(c, codigo, "Esperando que IT confirme la instalacion")
                @Suppress("DEPRECATION")
                val confirmar = i.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                try {
                    confirmar?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)?.let { c.startActivity(it) }
                } catch (e: Exception) {
                    Log.w("Checador", "no se pudo abrir la confirmacion", e)
                }
            }
            else -> {
                Actualizador.rechazada(c)
                val motivo = i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "codigo $estado"
                Config.guardarActualizacion(c, codigo, "Android rechazo la instalacion: $motivo".take(110))
            }
        }
    }
}

/** Tras actualizarse, la app se vuelve a abrir sola. */
class ActualizadaReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        if (i.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        Config.guardarActualizacion(c, 0, "")
        Config.guardarEsperaInstalar(c, 0)
        c.cacheDir.listFiles { f -> f.name.startsWith("checador-") }?.forEach { it.delete() }
        try {
            c.startActivity(Intent(c, ChecadorActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            Log.w("Checador", "no se pudo reabrir tras actualizar", e)
        }
    }
}
