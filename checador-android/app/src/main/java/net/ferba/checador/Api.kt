package net.ferba.checador

import android.content.Context
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** Llamadas al servidor. Bloquean: usarlas siempre fuera del hilo de pantalla. */
object Api {
    class Respuesta(val codigo: Int, val json: JSONObject?)

    private const val CONECTAR_MS = 8000
    private const val LEER_MS = 20000

    private fun abrir(
        c: Context, servidor: String, ruta: String, conToken: Boolean, conectarMs: Int = CONECTAR_MS,
    ): HttpURLConnection {
        val con = URL(servidor + ruta).openConnection() as HttpURLConnection
        con.connectTimeout = conectarMs
        con.readTimeout = LEER_MS
        con.setRequestProperty("Accept", "application/json")
        con.setRequestProperty("User-Agent", "FerbaChecador/" + BuildConfig.VERSION_NAME)
        if (conToken) con.setRequestProperty("X-Checador-Token", Config.token(c))
        return con
    }

    @Throws(IOException::class)
    fun post(
        c: Context, ruta: String, cuerpo: JSONObject,
        conToken: Boolean = true, servidor: String = Config.servidor(c),
        conectarMs: Int = CONECTAR_MS,
    ): Respuesta {
        val con = abrir(c, servidor, ruta, conToken, conectarMs)
        try {
            con.requestMethod = "POST"
            con.doOutput = true
            con.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            con.outputStream.use { it.write(cuerpo.toString().toByteArray(Charsets.UTF_8)) }
            val codigo = con.responseCode
            val flujo = if (codigo in 200..299) con.inputStream else con.errorStream
            val texto = flujo?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            val json = try { JSONObject(texto) } catch (e: Exception) { null }
            return Respuesta(codigo, json)
        } finally {
            con.disconnect()
        }
    }

    /** Descarga un archivo grande a disco. Devuelve false si el servidor no lo entrego. */
    @Throws(IOException::class)
    fun descargar(c: Context, ruta: String, destino: java.io.File): Boolean {
        val con = abrir(c, Config.servidor(c), ruta, true)
        con.readTimeout = 60000
        try {
            if (con.responseCode != 200) return false
            con.inputStream.use { entrada -> destino.outputStream().use { entrada.copyTo(it) } }
            return destino.length() > 0
        } finally {
            con.disconnect()
        }
    }

    /** Foto del empleado (bytes) o null si no hay o no se pudo. */
    fun foto(c: Context, empleado: Int): ByteArray? {
        return try {
            val con = abrir(c, Config.servidor(c), "/ferba/checador/v1/foto/$empleado", true)
            try {
                if (con.responseCode != 200) null else con.inputStream.use { it.readBytes() }
            } finally {
                con.disconnect()
            }
        } catch (e: Exception) {
            null
        }
    }
}
