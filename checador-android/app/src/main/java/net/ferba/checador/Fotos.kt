package net.ferba.checador

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Handler
import android.os.Looper
import android.util.LruCache
import android.widget.ImageView
import java.io.File
import java.util.concurrent.Executors

/** Fotos de los empleados: memoria -> disco -> servidor. Si el empleado no tiene
 *  foto (Odoo manda un dibujo que Android no decodifica), se pintan sus iniciales. */
object Fotos {
    private val memoria = LruCache<String, Bitmap>(120)
    private val hilos = Executors.newFixedThreadPool(4)
    private val pantalla = Handler(Looper.getMainLooper())
    private val COLORES = intArrayOf(
        0xFF2F6F5E.toInt(), 0xFF3D5A80.toInt(), 0xFF75588A.toInt(),
        0xFF96583A.toInt(), 0xFF57752C.toInt(), 0xFF8A4858.toInt(),
    )

    private fun carpeta(c: Context) = File(c.filesDir, "fotos").apply { mkdirs() }

    fun poner(vista: ImageView, id: Int, version: Long, nombre: String) {
        val llave = "$id-$version"
        vista.tag = llave
        memoria.get(llave)?.let { vista.setImageBitmap(it); return }
        vista.setImageBitmap(iniciales(vista.context, nombre, id))
        val c = vista.context.applicationContext
        hilos.execute {
            val archivo = File(carpeta(c), "$llave.img")
            var bmp: Bitmap? = if (archivo.exists()) BitmapFactory.decodeFile(archivo.path) else null
            if (bmp == null && !File(carpeta(c), "$llave.no").exists()) {
                val bytes = Api.foto(c, id)
                if (bytes != null) {
                    bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    if (bmp != null) {
                        archivo.writeBytes(bytes)
                        limpiarViejas(c, id, llave)
                    } else {
                        // Vino algo que no es foto (el dibujo de iniciales de Odoo): no reintentar.
                        File(carpeta(c), "$llave.no").writeText("")
                    }
                }
            }
            val listo = bmp ?: return@execute
            memoria.put(llave, listo)
            pantalla.post { if (vista.tag == llave) vista.setImageBitmap(listo) }
        }
    }

    private fun limpiarViejas(c: Context, id: Int, actual: String) {
        carpeta(c).listFiles()?.forEach {
            if (it.name.startsWith("$id-") && !it.name.startsWith(actual)) it.delete()
        }
    }

    fun iniciales(c: Context, nombre: String, id: Int): Bitmap {
        val lado = 192
        val bmp = Bitmap.createBitmap(lado, lado, Bitmap.Config.ARGB_8888)
        val lienzo = Canvas(bmp)
        lienzo.drawColor(COLORES[Math.floorMod(id, COLORES.size)])
        val letras = nombre.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
            .take(2).joinToString("") { it.substring(0, 1).uppercase() }
        val pincel = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textSize = 72f
            textAlign = Paint.Align.CENTER
            typeface = androidx.core.content.res.ResourcesCompat.getFont(c, R.font.barlow_semibold)
        }
        lienzo.drawText(letras, lado / 2f, lado / 2f - (pincel.descent() + pincel.ascent()) / 2f, pincel)
        return bmp
    }
}
