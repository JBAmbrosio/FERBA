package net.ferba.checador

import android.Manifest
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.animation.ObjectAnimator
import android.view.View
import android.view.animation.PathInterpolator
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.GridLayout
import android.widget.GridView
import android.widget.ImageView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import org.json.JSONObject
import java.text.Normalizer
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.Executors

/** La pantalla del checador: lista con fotos -> NIP (+ foto) -> confirmacion. */
class ChecadorActivity : AppCompatActivity() {

    class Empleado(
        val id: Int, val nombre: String, val puesto: String, val depto: String,
        var dentro: Boolean, val tieneNip: Boolean, val bloqueado: Boolean, val fotoV: Long,
        val nipV: String = "",
    ) {
        val clave: String = sinAcentos(nombre)
    }

    companion object {
        private const val SYNC_MS = 60_000L
        private const val NIP_OCIOSO_MS = 25_000L
        private const val INSTALAR_TRAS_MS = 120_000L
        /** Frena fuerte al final: responde de inmediato y se asienta. */
        private val SALIDA_SUAVE = PathInterpolator(0.23f, 1f, 0.32f, 1f)
        private const val TODOS = "Todos los departamentos"
        private val ES = Locale("es", "MX")

        fun sinAcentos(s: String): String =
            Normalizer.normalize(s.lowercase(ES), Normalizer.Form.NFD)
                .replace(Regex("\\p{InCombiningDiacriticalMarks}+"), "")
    }

    private val red = Executors.newSingleThreadExecutor()
    private val reloj = Handler(Looper.getMainLooper())

    private var todos: List<Empleado> = emptyList()
    private var visibles: List<Empleado> = emptyList()
    private var exigeFoto = true
    private var segundosRegreso = 5
    private var pinMantenimiento = ""
    private var deptoElegido = TODOS

    // Modo sin conexion: reglas que llegaron en la ultima sincronizacion.
    private var offlinePermitido = false
    private var maxFallos = 5
    private var minutosBloqueo = 5
    private var segundosDoble = 120
    @Volatile private var enLinea = false

    private var actual: Empleado? = null
    private var nip = ""
    private var marcando = false
    private var toques = 0
    private var primerToque = 0L

    private lateinit var grid: GridView
    private lateinit var buscar: EditText
    private lateinit var depto: Spinner
    private lateinit var conexion: TextView
    private lateinit var vacio: TextView
    private lateinit var panelNip: View
    private lateinit var panelResultado: View
    private lateinit var espera: View
    private lateinit var nipPuntos: TextView
    private lateinit var nipError: TextView
    private lateinit var camara: Camara
    private val adaptador = Adaptador()

    private val tic = object : Runnable {
        override fun run() {
            val ahora = Date()
            findViewById<TextView>(R.id.reloj).text = SimpleDateFormat("HH:mm", ES).format(ahora)
            findViewById<TextView>(R.id.fecha).text =
                SimpleDateFormat("EEEE d 'de' MMMM", ES).format(ahora)
            reloj.postDelayed(this, 1000L * (60 - ahora.seconds).coerceAtLeast(1))
        }
    }
    private val cicloSync = object : Runnable {
        override fun run() {
            actualizarSiNadieMarca()
            sincronizar()
            reloj.postDelayed(this, SYNC_MS)
        }
    }
    private var ultimoToque = 0L

    override fun dispatchTouchEvent(ev: android.view.MotionEvent): Boolean {
        ultimoToque = android.os.SystemClock.elapsedRealtime()
        return super.dispatchTouchEvent(ev)
    }

    /** Instala la actualizacion pendiente solo si nadie esta usando la tableta. */
    private fun actualizarSiNadieMarca() {
        if (!Kiosko.esDuena(this) || !Actualizador.puedeSola(this)) return
        val quieta = android.os.SystemClock.elapsedRealtime() - ultimoToque > INSTALAR_TRAS_MS
        val libre = !marcando && panelNip.visibility != View.VISIBLE &&
            panelResultado.visibility != View.VISIBLE
        if (quieta && libre) red.execute { Actualizador.instalar(this) }
    }

    private val nipOcioso = Runnable { cerrarNip() }
    private val volver = Runnable { mostrarLista() }

    // ------------------------------------------------------------------
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!Config.dadaDeAlta(this)) {
            irAlAlta()
            return
        }
        setContentView(R.layout.activity_checador)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.raiz)) { v, insets ->
            val b = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            v.setPadding(b.left, b.top, b.right, b.bottom)
            insets
        }

        grid = findViewById(R.id.grid)
        buscar = findViewById(R.id.buscar)
        depto = findViewById(R.id.depto)
        conexion = findViewById(R.id.conexion)
        vacio = findViewById(R.id.vacio)
        panelNip = findViewById(R.id.panelNip)
        panelResultado = findViewById(R.id.panelResultado)
        espera = findViewById(R.id.espera)
        nipPuntos = findViewById(R.id.nipPuntos)
        nipError = findViewById(R.id.nipError)
        camara = Camara(findViewById(R.id.camara), findViewById(R.id.camaraCaja))
        findViewById<View>(R.id.camaraCaja).clipToOutline = true

        grid.adapter = adaptador
        grid.setOnItemClickListener { _, _, pos, _ -> abrirNip(visibles[pos]) }
        buscar.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) = filtrar()
        })
        depto.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) {
                deptoElegido = p?.getItemAtPosition(pos) as? String ?: TODOS
                filtrar()
            }

            override fun onNothingSelected(p: AdapterView<*>?) {}
        }

        val teclado = findViewById<GridLayout>(R.id.teclado)
        for (i in 0 until teclado.childCount) {
            val tecla = teclado.getChildAt(i) as Button
            tecla.setOnClickListener { pulsar(tecla.tag as String) }
        }
        findViewById<Button>(R.id.nipCancelar).setOnClickListener { cerrarNip() }
        panelResultado.setOnClickListener { reloj.removeCallbacks(volver); mostrarLista() }
        findViewById<TextView>(R.id.titulo).setOnClickListener { toqueDeMantenimiento() }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (panelNip.visibility == View.VISIBLE && !marcando) cerrarNip()
            }
        })

        if (!Kiosko.esDuena(this) && ContextCompat.checkSelfPermission(
                this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.CAMERA), 1)
        }

        Config.ultimaSync(this)?.let {
            try {
                aplicar(JSONObject(it))
            } catch (e: Exception) {
            }
        }
        conexion.text = "Conectando…"
    }

    override fun onResume() {
        super.onResume()
        if (!Config.dadaDeAlta(this)) return
        Kiosko.fijar(this)
        ocultarBarras()
        reloj.removeCallbacks(tic)
        reloj.removeCallbacks(cicloSync)
        tic.run()
        cicloSync.run()
    }

    override fun onPause() {
        reloj.removeCallbacks(tic)
        reloj.removeCallbacks(cicloSync)
        if (::camara.isInitialized) camara.cerrar()
        super.onPause()
    }

    override fun onDestroy() {
        reloj.removeCallbacksAndMessages(null)
        red.shutdown()
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && Config.dadaDeAlta(this)) ocultarBarras()
    }

    private fun ocultarBarras() {
        WindowInsetsControllerCompat(window, window.decorView).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            hide(WindowInsetsCompat.Type.systemBars())
        }
    }

    private fun irAlAlta() {
        startActivity(Intent(this, AltaActivity::class.java))
        finish()
    }

    // ------------------------------------------------------------------
    // Sincronizacion: lista de empleados, reglas y senal de vida
    // ------------------------------------------------------------------
    private fun equipo(): JSONObject {
        val j = JSONObject()
            .put("version_app", BuildConfig.VERSION_NAME)
            .put("modelo", "${Build.MANUFACTURER} ${Build.MODEL}")
            .put("android", Build.VERSION.RELEASE)
            .put("actualizacion", Config.actualizacion(this))
        registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))?.let { b ->
            val nivel = b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val escala = b.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
            if (nivel >= 0 && escala > 0) j.put("bateria", nivel * 100 / escala)
            j.put("cargando", b.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0)
        }
        return j
    }

    private fun sincronizar() {
        val cuerpo = JSONObject().put("equipo", equipo())
        try {
            red.execute {
                try {
                    // Primero lo que se marco sin red, para que la lista ya venga al dia.
                    val envio = if (Cola.cuenta(this) > 0) Cola.enviar(this) else null
                    if (envio?.revocada == true) {
                        runOnUiThread { revocada() }
                        return@execute
                    }
                    val antes = System.currentTimeMillis()
                    val r = Api.post(this, "/ferba/checador/v1/sync", cuerpo)
                    val despues = System.currentTimeMillis()
                    if (r.codigo == 200 && r.json != null) {
                        horaServidor(r.json.optString("servidor_utc"))?.let {
                            Config.guardarDesfase(this, it - (antes + despues) / 2)
                        }
                        Actualizador.revisar(this, r.json.optJSONObject("app"))
                    }
                    val pendientes = Cola.cuenta(this)
                    runOnUiThread {
                        when {
                            r.codigo == 401 -> revocada()
                            r.codigo == 200 && r.json != null -> {
                                enLinea = true
                                Local.limpiar(this)
                                Config.guardarSync(this, r.json.toString())
                                aplicar(r.json)
                                if (pendientes > 0) {
                                    conexion.text = "En línea · ${porEnviar(pendientes)}"
                                    conexion.setTextColor(getColor(R.color.riel_aviso))
                                } else {
                                    conexion.text = "En línea"
                                    conexion.setTextColor(getColor(R.color.riel_ok))
                                }
                            }
                            else -> sinConexion("El servidor respondió ${r.codigo}")
                        }
                    }
                } catch (e: Exception) {
                    runOnUiThread { sinConexion("Sin conexión") }
                }
            }
        } catch (e: Exception) {
            // El ejecutor ya se cerro (la pantalla se esta destruyendo).
        }
    }

    private fun sinConexion(texto: String) {
        enLinea = false
        if (offlinePermitido && todos.any { it.nipV.isNotEmpty() }) {
            val n = Cola.cuenta(this)
            conexion.text = "$texto · las marcas se guardan en la tableta" +
                (if (n > 0) " · ${porEnviar(n)}" else "")
            conexion.setTextColor(getColor(R.color.riel_aviso))
        } else {
            conexion.text = "$texto · no se puede marcar"
            conexion.setTextColor(getColor(R.color.riel_error))
        }
    }

    private fun porEnviar(n: Int) = if (n == 1) "1 marca por enviar" else "$n marcas por enviar"

    private fun horaServidor(utc: String): Long? = try {
        val f = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
        f.timeZone = TimeZone.getTimeZone("UTC")
        // El servidor manda segundos enteros: se toma la mitad del segundo.
        f.parse(utc)!!.time + 500
    } catch (e: Exception) {
        null
    }

    private fun revocada() {
        Config.olvidar(this)
        Toast.makeText(this, "Esta tableta ya no está autorizada.", Toast.LENGTH_LONG).show()
        irAlAlta()
    }

    private fun aplicar(j: JSONObject) {
        val cfg = j.optJSONObject("config") ?: JSONObject()
        exigeFoto = cfg.optBoolean("exige_foto", true)
        segundosRegreso = cfg.optInt("segundos_regreso", 5).coerceIn(2, 30)
        pinMantenimiento = cfg.optString("pin_mantenimiento", "")
        offlinePermitido = cfg.optBoolean("offline", false)
        maxFallos = cfg.optInt("max_fallos", 5).coerceAtLeast(1)
        minutosBloqueo = cfg.optInt("minutos_bloqueo", 5).coerceAtLeast(1)
        segundosDoble = cfg.optInt("segundos_doble_marca", 120).coerceAtLeast(0)
        val nombre = cfg.optString("checador", "Checador")
        val empresa = cfg.optString("empresa", "")
        findViewById<TextView>(R.id.titulo).text =
            if (empresa.isEmpty()) nombre else "$nombre · $empresa"

        val lista = ArrayList<Empleado>()
        val arr = j.optJSONArray("empleados")
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val e = arr.getJSONObject(i)
                lista.add(Empleado(
                    e.getInt("id"), e.optString("nombre"), e.optString("puesto"),
                    e.optString("depto"), e.optBoolean("dentro"), e.optBoolean("tiene_nip"),
                    e.optBoolean("bloqueado"), e.optLong("foto_v"), e.optString("nip_v", ""),
                ))
            }
        }
        // Lo marcado sin red y aun no enviado manda sobre lo que dice el servidor.
        val pendiente = Cola.estadoPendiente(this)
        if (pendiente.isNotEmpty()) lista.forEach { p -> pendiente[p.id]?.let { p.dentro = it } }
        todos = lista

        val deptos = listOf(TODOS) + lista.map { it.depto }.filter { it.isNotEmpty() }.distinct().sorted()
        if (!deptos.contains(deptoElegido)) deptoElegido = TODOS
        val ad = ArrayAdapter(this, R.layout.item_depto, deptos)
        ad.setDropDownViewResource(R.layout.item_depto)
        depto.adapter = ad
        depto.setSelection(deptos.indexOf(deptoElegido), false)
        filtrar()
    }

    private fun filtrar() {
        val texto = sinAcentos(buscar.text.toString().trim())
        visibles = todos.filter {
            (deptoElegido == TODOS || it.depto == deptoElegido) &&
                (texto.isEmpty() || it.clave.contains(texto))
        }
        adaptador.notifyDataSetChanged()
        vacio.visibility = if (visibles.isEmpty()) View.VISIBLE else View.GONE
        vacio.text = if (todos.isEmpty()) "Cargando la lista de empleados…" else "Nadie con ese nombre."
    }

    private inner class Adaptador : BaseAdapter() {
        override fun getCount() = visibles.size
        override fun getItem(position: Int) = visibles[position]
        override fun getItemId(position: Int) = visibles[position].id.toLong()
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val v = convertView ?: layoutInflater.inflate(R.layout.item_empleado, parent, false).also {
                it.findViewById<ImageView>(R.id.foto).clipToOutline = true
            }
            val e = visibles[position]
            v.findViewById<TextView>(R.id.nombre).text = e.nombre
            v.findViewById<TextView>(R.id.puesto).text = e.puesto
            val estado = v.findViewById<TextView>(R.id.estado)
            when {
                e.bloqueado -> { estado.text = "Bloqueado"; estado.setTextColor(getColor(R.color.error)) }
                !e.tieneNip -> { estado.text = "Sin NIP"; estado.setTextColor(getColor(R.color.aviso)) }
                e.dentro -> { estado.text = "Dentro"; estado.setTextColor(getColor(R.color.entrada)) }
                else -> { estado.text = "Fuera"; estado.setTextColor(getColor(R.color.texto_suave)) }
            }
            // El punto verde distingue a quien esta dentro sin depender solo del texto.
            val punto = if (e.dentro && e.tieneNip && !e.bloqueado) R.drawable.punto_dentro else 0
            estado.setCompoundDrawablesRelativeWithIntrinsicBounds(punto, 0, 0, 0)
            Fotos.poner(v.findViewById(R.id.foto), e.id, e.fotoV, e.nombre)
            return v
        }
    }

    // ------------------------------------------------------------------
    // NIP
    // ------------------------------------------------------------------
    private fun abrirNip(e: Empleado) {
        (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
            .hideSoftInputFromWindow(buscar.windowToken, 0)
        buscar.clearFocus()
        // Sin NIP o bloqueada: se dice de una vez, sin pedir un NIP que no va a servir.
        if (!e.tieneNip) {
            resultado(R.color.aviso, "Sin NIP", e.nombre,
                "Todavía no tienes NIP asignado. Pídelo a Recursos Humanos para poder marcar.")
            return
        }
        if (e.bloqueado || Local.bloqueadoHasta(this, e.id) > 0) {
            resultado(R.color.error, "Tarjeta bloqueada", e.nombre,
                "Demasiados NIP incorrectos. Espera unos minutos o avisa a Recursos Humanos.")
            return
        }
        actual = e
        nip = ""
        pintarNip()
        nipError.text = ""
        findViewById<TextView>(R.id.nipNombre).text = e.nombre
        val accion = findViewById<TextView>(R.id.nipAccion)
        accion.text = if (e.dentro) "Registrar salida" else "Registrar entrada"
        accion.setTextColor(getColor(if (e.dentro) R.color.salida else R.color.entrada))
        val foto = findViewById<ImageView>(R.id.nipFoto)
        foto.clipToOutline = true
        Fotos.poner(foto, e.id, e.fotoV, e.nombre)

        val conCamara = exigeFoto && ContextCompat.checkSelfPermission(
            this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        findViewById<View>(R.id.camaraCaja).visibility = if (conCamara) View.VISIBLE else View.GONE
        findViewById<View>(R.id.camaraNota).visibility = if (conCamara) View.VISIBLE else View.GONE
        entrar(panelNip, 220)
        if (conCamara) camara.abrir()
        reloj.removeCallbacks(nipOcioso)
        reloj.postDelayed(nipOcioso, NIP_OCIOSO_MS)
    }

    private fun cerrarNip() {
        if (marcando) return
        reloj.removeCallbacks(nipOcioso)
        camara.cerrar()
        panelNip.animate().cancel()
        panelNip.visibility = View.GONE
        actual = null
        nip = ""
    }

    private fun pintarNip() {
        nipPuntos.text = "●".repeat(nip.length)
    }

    private fun pulsar(tecla: String) {
        if (marcando) return
        reloj.removeCallbacks(nipOcioso)
        reloj.postDelayed(nipOcioso, NIP_OCIOSO_MS)
        nipError.text = ""
        when (tecla) {
            "borrar" -> if (nip.isNotEmpty()) nip = nip.dropLast(1)
            "ok" -> { marcar(); return }
            else -> if (nip.length < 8) nip += tecla
        }
        pintarNip()
    }

    // ------------------------------------------------------------------
    // Marcar
    // ------------------------------------------------------------------
    private fun marcar() {
        val e = actual ?: return
        if (nip.isEmpty()) {
            nipError.text = "Escribe tu NIP."
            return
        }
        marcando = true
        reloj.removeCallbacks(nipOcioso)
        espera.visibility = View.VISIBLE
        val tecleado = nip
        val uuid = UUID.randomUUID().toString()
        val foto = camara.foto()
        val cuerpo = JSONObject()
            .put("empleado", e.id)
            .put("nip", tecleado)
            // El mismo identificador en los reintentos: el servidor nunca duplica.
            .put("uuid", uuid)
        foto?.let { cuerpo.put("foto", it) }

        red.execute {
            var r: Api.Respuesta? = null
            // Si ya se sabe que no hay red, un solo intento corto: nadie espera de mas.
            val intentos = if (enLinea) 2 else 1
            for (intento in 1..intentos) {
                try {
                    r = Api.post(this, "/ferba/checador/v1/marcar", cuerpo,
                        conectarMs = if (enLinea) 6000 else 2500)
                    break
                } catch (ex: Exception) {
                    if (intento < intentos) Thread.sleep(500)
                }
            }
            // Sin respuesta, o el servidor no esta en condiciones (caido, reiniciando, sin
            // base de datos: Odoo contesta 404 o 5xx): se resuelve en la tableta si se puede.
            // Solo cuentan como respuesta una marca resuelta (200) o la revocacion (401).
            val util = r?.let { (it.codigo == 200 && it.json != null) || it.codigo == 401 } ?: false
            if (!util) {
                sinRed(e, tecleado, uuid, foto)?.let { r = Api.Respuesta(200, it) }
            }
            val resp = r
            runOnUiThread {
                marcando = false
                espera.visibility = View.GONE
                resolver(e, resp)
            }
        }
    }

    /** Marca sin red (hilo de fondo). Devuelve lo que habria contestado el
     *  servidor, o null si esta tableta no puede validar a esa persona sin red. */
    private fun sinRed(e: Empleado, tecleado: String, uuid: String, foto: String?): JSONObject? {
        if (!offlinePermitido || e.nipV.isEmpty()) return null
        if (Local.bloqueadoHasta(this, e.id) > 0) return JSONObject().put("resultado", "bloqueado")
        if (!Nip.coincide(e.nipV, tecleado)) {
            return JSONObject().put("resultado", "nip_fallido")
                .put("restantes", Local.fallo(this, e.id, maxFallos, minutosBloqueo))
        }
        if (Local.esDoble(this, e.id, segundosDoble)) return JSONObject().put("resultado", "doble")
        val tipo = if (e.dentro) "salida" else "entrada"
        if (!Cola.guardar(this, e.id, tecleado, uuid, foto, tipo)) return null
        val utc = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
        utc.timeZone = TimeZone.getTimeZone("UTC")
        return JSONObject().put("resultado", tipo).put("guardada", true)
            .put("fecha_utc", utc.format(Date(System.currentTimeMillis() + Config.desfase(this))))
    }

    private fun resolver(e: Empleado, r: Api.Respuesta?) {
        if (r == null) {
            cerrarNip()
            resultado(R.color.error, "Sin conexión", e.nombre,
                "Tu marca NO se registró.\nIntenta de nuevo o avisa a Recursos Humanos.")
            return
        }
        if (r.codigo == 401) {
            cerrarNip()
            revocada()
            return
        }
        val j = r.json
        if (r.codigo != 200 || j == null) {
            cerrarNip()
            resultado(R.color.error, "No se pudo marcar", e.nombre,
                "El servidor respondió ${r.codigo}. Tu marca NO se registró.")
            return
        }
        val res = j.optString("resultado")
        when (res) {
            "entrada", "salida" -> {
                e.dentro = res == "entrada"
                // Si la lista se refresco mientras tecleaba el NIP, la tarjeta visible es otro objeto.
                todos.firstOrNull { it.id == e.id }?.dentro = e.dentro
                cerrarNip()
                val hora = horaLocal(j.optString("fecha_utc"))
                val horas = j.optDouble("horas_hoy", 0.0)
                Local.acierto(this, e.id)
                Config.recordarDentro(this, e.id, e.dentro)
                val guardada = j.optBoolean("guardada")
                val detalle = when {
                    guardada -> "Guardada sin conexión.\nSe envía sola en cuanto vuelva la red."
                    res == "salida" -> "Horas de hoy: ${formatoHoras(horas)}"
                    else -> "Buen turno"
                }
                if (guardada) sinConexion("Sin conexión")
                resultado(if (res == "entrada") R.color.entrada else R.color.salida,
                    (if (res == "entrada") "Entrada  " else "Salida  ") + hora, e.nombre, detalle)
                adaptador.notifyDataSetChanged()
            }
            "nip_fallido" -> {
                nip = ""
                pintarNip()
                val quedan = j.optInt("restantes", -1)
                nipError.text = when {
                    quedan > 1 -> "NIP incorrecto. Te quedan $quedan intentos."
                    quedan == 1 -> "NIP incorrecto. Te queda 1 intento."
                    else -> "NIP incorrecto. Tarjeta bloqueada."
                }
                sacudir(nipError)
                reloj.postDelayed(nipOcioso, NIP_OCIOSO_MS)
                if (quedan == 0) {
                    cerrarNip()
                    resultado(R.color.error, "Tarjeta bloqueada", e.nombre,
                        "Demasiados NIP incorrectos. Espera unos minutos o avisa a Recursos Humanos.")
                    sincronizar()
                }
            }
            "bloqueado" -> {
                cerrarNip()
                resultado(R.color.error, "Tarjeta bloqueada", e.nombre,
                    "Demasiados NIP incorrectos. Espera unos minutos o avisa a Recursos Humanos.")
            }
            "doble" -> {
                cerrarNip()
                resultado(R.color.aviso, "Ya habías marcado", e.nombre,
                    "Tu marca anterior sigue registrada. No hace falta repetirla.")
            }
            else -> {
                cerrarNip()
                resultado(R.color.error, "No se registró", e.nombre,
                    j.optString("mensaje", "Avisa a Recursos Humanos."))
            }
        }
    }

    private fun resultado(color: Int, titulo: String, nombre: String, detalle: String) {
        panelResultado.setBackgroundColor(getColor(color))
        findViewById<TextView>(R.id.resTitulo).text = titulo
        findViewById<TextView>(R.id.resNombre).text = nombre
        findViewById<TextView>(R.id.resDetalle).text = detalle
        val icono = findViewById<ImageView>(R.id.resIcono)
        icono.setImageResource(when (color) {
            R.color.entrada -> R.drawable.ic_entrada
            R.color.salida -> R.drawable.ic_salida
            R.color.aviso -> R.drawable.ic_aviso
            else -> R.drawable.ic_error
        })
        entrar(panelResultado, 180)
        // El icono llega un instante despues: es el unico gesto de la pantalla.
        icono.animate().cancel()
        icono.scaleX = 0.9f
        icono.scaleY = 0.9f
        icono.animate().scaleX(1f).scaleY(1f).setDuration(320).setInterpolator(SALIDA_SUAVE).start()
        reloj.removeCallbacks(volver)
        reloj.postDelayed(volver, segundosRegreso * 1000L)
    }

    /** Entrada de un panel: aparece desde casi su lugar, rapido y sin rebote. */
    private fun entrar(panel: View, ms: Long) {
        panel.animate().cancel()
        panel.alpha = 0f
        panel.translationY = 10f * resources.displayMetrics.density
        panel.visibility = View.VISIBLE
        panel.animate().alpha(1f).translationY(0f).setDuration(ms).setInterpolator(SALIDA_SUAVE).start()
    }

    /** NIP incorrecto: una sacudida corta, como quien dice que no con la cabeza. */
    private fun sacudir(v: View) {
        val d = resources.displayMetrics.density
        ObjectAnimator.ofFloat(v, View.TRANSLATION_X, 0f, -9 * d, 8 * d, -5 * d, 3 * d, 0f).apply {
            duration = 320
            start()
        }
    }

    private fun mostrarLista() {
        panelResultado.animate().cancel()
        panelResultado.visibility = View.GONE
        if (buscar.text.isNotEmpty()) buscar.setText("")
        grid.setSelection(0)
    }

    private fun horaLocal(utc: String): String = try {
        val entrada = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
        entrada.timeZone = TimeZone.getTimeZone("UTC")
        SimpleDateFormat("HH:mm", ES).format(entrada.parse(utc)!!)
    } catch (e: Exception) {
        SimpleDateFormat("HH:mm", ES).format(Date())
    }

    private fun formatoHoras(h: Double): String {
        val minutos = Math.round(h * 60).toInt()
        return "${minutos / 60} h ${"%02d".format(minutos % 60)} min"
    }

    // ------------------------------------------------------------------
    // Mantenimiento (solo IT): 7 toques al titulo + NIP de mantenimiento
    // ------------------------------------------------------------------
    private fun toqueDeMantenimiento() {
        val ahora = System.currentTimeMillis()
        if (ahora - primerToque > 4000) {
            primerToque = ahora
            toques = 0
        }
        toques++
        if (toques < 7) return
        toques = 0
        if (pinMantenimiento.isEmpty()) {
            Toast.makeText(this, "Mantenimiento sin configurar en Odoo.", Toast.LENGTH_LONG).show()
            return
        }
        val campo = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
            hint = "NIP de mantenimiento"
        }
        AlertDialog.Builder(this)
            .setTitle("Mantenimiento")
            .setView(campo)
            .setNegativeButton("Cancelar", null)
            .setPositiveButton("Entrar") { _, _ ->
                if (campo.text.toString() == pinMantenimiento) menuMantenimiento()
                else Toast.makeText(this, "NIP incorrecto.", Toast.LENGTH_SHORT).show()
            }
            .show()
    }

    private fun menuMantenimiento() {
        val pendiente = Actualizador.lista(this)
        val opciones = arrayOf(
            "Abrir los ajustes de la tableta",
            "Desvincular esta tableta",
            if (pendiente > 0) "Instalar la actualización descargada" else "Cerrar",
        )
        val pie = "Checador ${BuildConfig.VERSION_NAME} · " +
            (if (Kiosko.esDuena(this)) "pantalla fija" else "pantalla SIN fijar")
        AlertDialog.Builder(this)
            .setTitle(pie)
            .setItems(opciones) { _, cual ->
                when (cual) {
                    0 -> {
                        Kiosko.soltar(this)
                        startActivity(Intent(Settings.ACTION_SETTINGS))
                    }
                    1 -> {
                        Kiosko.soltar(this)
                        Config.olvidar(this)
                        irAlAlta()
                    }
                    2 -> if (pendiente > 0) {
                        Toast.makeText(this, "Instalando…", Toast.LENGTH_LONG).show()
                        red.execute { Actualizador.instalar(this) }
                    }
                }
            }
            .show()
    }
}
