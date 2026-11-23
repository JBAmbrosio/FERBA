package net.ferba.checador

import android.content.Context
import android.content.SharedPreferences

/** Lo que la tableta recuerda: a que servidor habla, su credencial y la ultima
 *  lista de empleados (para pintar la pantalla al instante al encender). */
object Config {
    const val SERVIDOR_DEFECTO = "https://ferba.odoo.com"

    private fun p(c: Context): SharedPreferences =
        c.applicationContext.getSharedPreferences("checador", Context.MODE_PRIVATE)

    fun servidor(c: Context): String =
        (p(c).getString("servidor", null) ?: SERVIDOR_DEFECTO).trimEnd('/')

    fun token(c: Context): String = p(c).getString("token", "") ?: ""

    fun dadaDeAlta(c: Context): Boolean = token(c).isNotEmpty()

    fun guardarAlta(c: Context, servidor: String, token: String) {
        p(c).edit().putString("servidor", servidor.trimEnd('/')).putString("token", token).apply()
    }

    /** La tableta fue revocada o desvinculada: olvida la credencial y la lista. */
    fun olvidar(c: Context) {
        p(c).edit().remove("token").remove("ultima_sync").apply()
    }

    fun ultimaSync(c: Context): String? = p(c).getString("ultima_sync", null)

    fun guardarSync(c: Context, json: String) {
        p(c).edit().putString("ultima_sync", json).apply()
    }

    /** Version descargada y verificada que espera instalarse (0 = ninguna). */
    fun actualizacionLista(c: Context): Int = p(c).getInt("act_lista", 0)

    /** Lo que se le reporta a Odoo sobre la actualizacion ("" = al dia). */
    fun actualizacion(c: Context): String = p(c).getString("act_estado", "") ?: ""

    fun guardarActualizacion(c: Context, lista: Int, estado: String) {
        p(c).edit().putInt("act_lista", lista).putString("act_estado", estado).apply()
    }

    /** Huella de un APK que esta tableta ya descargo y rechazo: no se vuelve a bajar. */
    fun apkRechazado(c: Context): String = p(c).getString("act_mala", "") ?: ""

    fun guardarApkRechazado(c: Context, sha: String) {
        p(c).edit().putString("act_mala", sha).apply()
    }

    /** Hasta cuando (reloj de pared) no se reintenta sola una instalacion que Android rechazo. */
    fun esperaInstalar(c: Context): Long = p(c).getLong("act_espera", 0)

    fun guardarEsperaInstalar(c: Context, hasta: Long) {
        p(c).edit().putLong("act_espera", hasta).apply()
    }

    /** Tras una marca aceptada: la lista guardada debe recordar quien quedo dentro,
     *  por si la tableta se reinicia antes de volver a sincronizar. */
    fun recordarDentro(c: Context, empleado: Int, dentro: Boolean) {
        try {
            val j = org.json.JSONObject(ultimaSync(c) ?: return)
            val arr = j.optJSONArray("empleados") ?: return
            for (i in 0 until arr.length()) {
                val e = arr.getJSONObject(i)
                if (e.optInt("id") == empleado) {
                    e.put("dentro", dentro)
                    guardarSync(c, j.toString())
                    return
                }
            }
        } catch (e: Exception) {
        }
    }

    /** Cuanto hay que sumarle al reloj de la tableta para obtener la hora del servidor. */
    fun desfase(c: Context): Long = p(c).getLong("desfase_ms", 0)

    fun guardarDesfase(c: Context, ms: Long) {
        p(c).edit().putLong("desfase_ms", ms).apply()
    }
}
