package net.ferba.checador

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.util.concurrent.Executors

/** Primera vez: IT teclea el codigo de un solo uso que genera Odoo. */
class AltaActivity : AppCompatActivity() {
    private val hilo = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_alta)
        val servidor = findViewById<EditText>(R.id.servidor)
        val codigo = findViewById<EditText>(R.id.codigo)
        val boton = findViewById<Button>(R.id.btnAlta)
        val estado = findViewById<TextView>(R.id.estado)
        findViewById<TextView>(R.id.version).text = "Checador ${BuildConfig.VERSION_NAME}"
        servidor.setText(Config.servidor(this))

        boton.setOnClickListener {
            val url = servidor.text.toString().trim().trimEnd('/')
            val cod = codigo.text.toString().trim()
            if (!url.startsWith("https://") && !url.startsWith("http://")) {
                estado.text = "El servidor debe empezar con https://"
                return@setOnClickListener
            }
            if (cod.length != 8) {
                estado.text = "El código tiene 8 dígitos."
                return@setOnClickListener
            }
            boton.isEnabled = false
            estado.text = "Dando de alta…"
            hilo.execute {
                var mensaje: String
                var listo = false
                try {
                    val cuerpo = JSONObject().put("codigo", cod).put("equipo", equipo())
                    val r = Api.post(this, "/ferba/checador/v1/alta", cuerpo, conToken = false, servidor = url)
                    val token = r.json?.optString("token", "") ?: ""
                    if (r.codigo == 200 && token.isNotEmpty()) {
                        Config.guardarAlta(this, url, token)
                        listo = true
                        mensaje = ""
                    } else if (r.codigo == 403) {
                        mensaje = "Código incorrecto o vencido. Genera uno nuevo en Odoo."
                    } else if (r.codigo == 404) {
                        mensaje = "Ese servidor no tiene instalado el Checador."
                    } else {
                        mensaje = "El servidor respondió ${r.codigo}. Intenta de nuevo."
                    }
                } catch (e: Exception) {
                    mensaje = "No hay conexión con el servidor. Revisa el Wi-Fi."
                }
                runOnUiThread {
                    if (listo) {
                        startActivity(Intent(this, ChecadorActivity::class.java))
                        finish()
                    } else {
                        estado.text = mensaje
                        boton.isEnabled = true
                    }
                }
            }
        }
    }

    private fun equipo() = JSONObject()
        .put("version_app", BuildConfig.VERSION_NAME)
        .put("modelo", "${Build.MANUFACTURER} ${Build.MODEL}")
        .put("android", Build.VERSION.RELEASE)

    override fun onDestroy() {
        hilo.shutdown()
        super.onDestroy()
    }
}
