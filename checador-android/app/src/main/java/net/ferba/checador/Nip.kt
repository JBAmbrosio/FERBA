package net.ferba.checador

import android.util.Base64
import java.security.MessageDigest
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/** Valida un NIP sin red contra la huella que mando Odoo en la ultima
 *  sincronizacion ("v1$vueltas$sal$resumen", PBKDF2-HMAC-SHA256). La tableta
 *  nunca guarda la lista de NIP; solo sus huellas. Tarda decimas de segundo:
 *  llamarlo fuera del hilo de pantalla. */
object Nip {
    fun coincide(huella: String, nip: String): Boolean {
        if (nip.isEmpty()) return false
        return try {
            val p = huella.split('$')
            if (p.size != 4 || p[0] != "v1") return false
            val vueltas = p[1].toInt()
            val sal = Base64.decode(p[2], Base64.DEFAULT)
            val esperado = Base64.decode(p[3], Base64.DEFAULT)
            val spec = PBEKeySpec(nip.toCharArray(), sal, vueltas, esperado.size * 8)
            val calculado = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
                .generateSecret(spec).encoded
            MessageDigest.isEqual(calculado, esperado)
        } catch (e: Exception) {
            false
        }
    }
}
