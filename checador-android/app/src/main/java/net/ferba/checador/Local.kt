package net.ferba.checador

import android.content.Context

/** Reglas que la tableta aplica por su cuenta mientras no hay red: intentos de
 *  NIP fallidos, bloqueo y doble marca. Cuando hay red manda el servidor. */
object Local {
    private fun p(c: Context) =
        c.applicationContext.getSharedPreferences("checador_local", Context.MODE_PRIVATE)

    // Aparte, porque esto NO se borra al volver la red: la doble marca se sigue
    // cuidando aunque la app se haya reiniciado entre una y otra.
    private fun marcas(c: Context) =
        c.applicationContext.getSharedPreferences("checador_marcas", Context.MODE_PRIVATE)

    private fun ahora(c: Context) = System.currentTimeMillis() + Config.desfase(c)

    /** Milisegundos (hora corregida) hasta los que la tarjeta esta bloqueada, o 0. */
    fun bloqueadoHasta(c: Context, empleado: Int): Long {
        val hasta = p(c).getLong("hasta_$empleado", 0)
        return if (hasta > ahora(c)) hasta else 0
    }

    /** Anota un NIP fallido. Devuelve cuantos intentos quedan (0 = quedo bloqueada). */
    fun fallo(c: Context, empleado: Int, maxFallos: Int, minutosBloqueo: Int): Int {
        val fallos = p(c).getInt("fallos_$empleado", 0) + 1
        if (fallos >= maxFallos) {
            p(c).edit().putInt("fallos_$empleado", 0)
                .putLong("hasta_$empleado", ahora(c) + minutosBloqueo * 60_000L).apply()
            return 0
        }
        p(c).edit().putInt("fallos_$empleado", fallos).apply()
        return maxFallos - fallos
    }

    /** Marca aceptada (con o sin red). */
    fun acierto(c: Context, empleado: Int) {
        p(c).edit().remove("fallos_$empleado").remove("hasta_$empleado").apply()
        marcas(c).edit().putLong("m_$empleado", ahora(c)).apply()
    }

    fun esDoble(c: Context, empleado: Int, segundos: Int): Boolean {
        val antes = marcas(c).getLong("m_$empleado", 0)
        val pasaron = ahora(c) - antes
        return segundos > 0 && antes > 0 && pasaron >= 0 && pasaron < segundos * 1000L
    }

    /** Con red manda el servidor: se olvidan los contadores y bloqueos locales. */
    fun limpiar(c: Context) {
        if (p(c).all.isNotEmpty()) p(c).edit().clear().apply()
    }
}