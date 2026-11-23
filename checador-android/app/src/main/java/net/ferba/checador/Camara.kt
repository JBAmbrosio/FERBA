@file:Suppress("DEPRECATION")

package net.ferba.checador

import android.graphics.Bitmap
import android.graphics.SurfaceTexture
import android.hardware.Camera
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import android.view.Surface
import android.view.TextureView
import android.view.View
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors
import kotlin.math.abs

/** Camara frontal en un recuadro. La foto de la marca es lo que se ve en el
 *  recuadro en el momento de pulsar OK. Si la camara falla, no estorba: la
 *  marca sale sin foto y el servidor la senala. */
class Camara(private val vista: TextureView, private val caja: View) :
    TextureView.SurfaceTextureListener {

    private val hilo = Executors.newSingleThreadExecutor()
    private val pantalla = Handler(Looper.getMainLooper())
    @Volatile private var cam: Camera? = null
    @Volatile private var pedida = false
    @Volatile private var vertical = false

    init {
        vista.surfaceTextureListener = this
    }

    fun abrir() {
        pedida = true
        vista.surfaceTexture?.let { iniciar(it) }
    }

    fun cerrar() {
        pedida = false
        hilo.execute { soltar() }
    }

    fun lista(): Boolean = cam != null

    /** JPEG en base64 de lo que muestra el recuadro, o null. Llamar en el hilo de pantalla. */
    fun foto(): String? {
        if (cam == null || !vista.isAvailable) return null
        return try {
            val bmp = (if (vertical) vista.getBitmap(360, 480) else vista.getBitmap(480, 360))
                ?: return null
            val salida = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, 72, salida)
            Base64.encodeToString(salida.toByteArray(), Base64.NO_WRAP)
        } catch (e: Exception) {
            Log.w("Checador", "no se pudo tomar la foto", e)
            null
        }
    }

    private fun iniciar(textura: SurfaceTexture) {
        val giro = vista.display?.rotation ?: Surface.ROTATION_0
        hilo.execute {
            if (!pedida || cam != null) return@execute
            try {
                val info = Camera.CameraInfo()
                var id = 0
                for (i in 0 until Camera.getNumberOfCameras()) {
                    Camera.getCameraInfo(i, info)
                    if (info.facing == Camera.CameraInfo.CAMERA_FACING_FRONT) { id = i; break }
                }
                Camera.getCameraInfo(id, info)
                val c = Camera.open(id)
                val grados = when (giro) {
                    Surface.ROTATION_90 -> 90
                    Surface.ROTATION_180 -> 180
                    Surface.ROTATION_270 -> 270
                    else -> 0
                }
                val orientacion = if (info.facing == Camera.CameraInfo.CAMERA_FACING_FRONT) {
                    (360 - (info.orientation + grados) % 360) % 360
                } else {
                    (info.orientation - grados + 360) % 360
                }
                c.setDisplayOrientation(orientacion)
                val p = c.parameters
                // El tamano 4:3 mas cercano a 640x480: suficiente como evidencia y ligero.
                p.supportedPreviewSizes
                    ?.filter { abs(it.width * 3 - it.height * 4) < 8 }
                    ?.minByOrNull { abs(it.width - 640) }
                    ?.let { p.setPreviewSize(it.width, it.height) }
                c.parameters = p
                c.setPreviewTexture(textura)
                c.startPreview()
                cam = c
                vertical = orientacion == 90 || orientacion == 270
                pantalla.post { ajustarCaja() }
                if (!pedida) soltar()
            } catch (e: Exception) {
                Log.w("Checador", "camara no disponible", e)
                soltar()
            }
        }
    }

    private fun ajustarCaja() {
        val largo = caja.resources.getDimensionPixelSize(R.dimen.camara_lado)
        val corto = largo * 3 / 4
        val lp = caja.layoutParams
        lp.width = if (vertical) corto else largo
        lp.height = if (vertical) largo else corto
        caja.layoutParams = lp
    }

    private fun soltar() {
        val c = cam
        cam = null
        try {
            c?.stopPreview()
        } catch (e: Exception) {
        }
        try {
            c?.release()
        } catch (e: Exception) {
        }
    }

    override fun onSurfaceTextureAvailable(s: SurfaceTexture, w: Int, h: Int) {
        if (pedida) iniciar(s)
    }

    override fun onSurfaceTextureSizeChanged(s: SurfaceTexture, w: Int, h: Int) {}

    override fun onSurfaceTextureDestroyed(s: SurfaceTexture): Boolean {
        hilo.execute { soltar() }
        return true
    }

    override fun onSurfaceTextureUpdated(s: SurfaceTexture) {}
}
