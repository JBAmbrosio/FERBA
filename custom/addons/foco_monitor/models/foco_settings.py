import base64
import hashlib
import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Cuanto borra como maximo una corrida. La primera purga de un sistema con
# anos encima podria ser de cientos de miles de renglones; partirla evita
# que el proceso diario se quede colgado, y como corre todos los dias, se
# drena solo. Lo que falta se reporta, no se esconde.
PURGA_MAX_POR_CORRIDA = 20000


class FocoSettings(models.Model):
    _name = 'foco.settings'
    _description = 'Configuracion de Foco'

    name = fields.Char(default="Configuracion", readonly=True)

    # El horario global (sched_*, day_*) se retiro el 28-sep-2026: desde que
    # RRHH asigna calendarios laborales no gobernaba a nadie, y la jornada la
    # dice el CHECADOR para quien lo usa (ver `jornada_de`). Las columnas
    # viejas quedan en la base sin uso; Odoo no las borra y nada las lee.

    # ---- bloqueo de navegacion ------------------------------------------
    # APAGADO de fabrica. Una funcion que puede dejar a alguien sin poder abrir
    # una pagina no se enciende sola: se enciende cuando alguien lo decide.
    block_enabled = fields.Boolean(
        string='Aplicar bloqueo de sitios', default=False,
        help='Interruptor general. Apagado, los equipos LIMPIAN la lista que '
             'tuvieran puesta; no se quedan con la ultima. Que apagarlo suelte '
             'de verdad los equipos es lo que lo hace un interruptor y no un '
             'adorno.')
    default_policy_id = fields.Many2one(
        'foco.policy', string='Perfil por omision',
        help='El que toma un equipo que no tenga uno propio. Vacio = ese equipo '
             'no bloquea nada.')

    # ---- ventana de inactividad (justificacion bloqueante) --------------
    # El umbral (estos minutos) es GLOBAL para todos; a QUE EQUIPO le sale la
    # ventana es POR EQUIPO (foco.computer.foco_ventana_inactividad), APAGADO
    # para todos de fabrica. Se administra en Foco > Configuracion > Ventana de
    # inactividad. Se elige por EQUIPO -no por empleado- porque la ventana la
    # muestra el agente de esa maquina y solo los equipos tienen Foco: un
    # empleado sin equipo no la podria recibir.
    gap_min_minutes = fields.Integer(
        string='Minutos de inactividad para pedir justificacion', default=15,
        help='Un hueco sin actividad mas largo que estos minutos abre un periodo '
             'por justificar; al equipo que tenga la ventana encendida, ademas se '
             'la muestra. 15 min de fabrica. Entre 1 y 60. Viaja a los equipos en '
             'su siguiente envio (cinco minutos), sin reinstalar nada.')
    ventana_computer_ids = fields.Many2many(
        'foco.computer', string='Equipos con la ventana de inactividad',
        compute='_compute_ventana_equipos', inverse='_inverse_ventana_equipos',
        help='En estos equipos aparece la ventana bloqueante para justificar '
             'los periodos largos sin actividad. Vacio = en ninguno (de fabrica). '
             'Son equipos con Foco instalado; el umbral de minutos de arriba es '
             'el mismo para todos.')

    @api.constrains('gap_min_minutes')
    def _check_gap_min_minutes(self):
        for r in self:
            if r.gap_min_minutes and not (1 <= r.gap_min_minutes <= 60):
                raise ValidationError('Los minutos de inactividad van de 1 a 60.')

    @api.depends_context('uid')
    def _compute_ventana_equipos(self):
        eqs = self.env['foco.computer'].sudo().search([('foco_ventana_inactividad', '=', True)])
        for rec in self:
            rec.ventana_computer_ids = eqs

    def _inverse_ventana_equipos(self):
        Comp = self.env['foco.computer'].sudo()
        for rec in self:
            actuales = Comp.search([('foco_ventana_inactividad', '=', True)])
            (actuales - rec.ventana_computer_ids).write({'foco_ventana_inactividad': False})
            rec.ventana_computer_ids.sudo().write({'foco_ventana_inactividad': True})

    @api.model
    def action_ventana(self):
        """Abre la pantalla de la ventana de inactividad sobre el registro unico."""
        ajustes = self.get_settings()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Ventana de inactividad',
            'res_model': 'foco.settings',
            'view_mode': 'form',
            'res_id': ajustes.id,
            'target': 'current',
            'views': [(self.env.ref('foco_monitor.foco_settings_view_form_ventana').id, 'form')],
        }

    # ---- el archivo abierto ---------------------------------------------
    # APAGADO de fabrica, y es la compuerta de TODO lo demas: mientras este
    # apagado, las marcas por aplicacion quedan inertes. Sin esto, actualizar el
    # modulo bastaria para que una app recien descubierta empezara a reportar
    # nombres de archivo sin que ninguna persona lo hubiera decidido, y eso es
    # justo lo que no puede pasar en un sistema que mide a personas.
    document_enabled = fields.Boolean(
        string='Reportar el archivo abierto', default=False,
        help='Interruptor general. Apagado, NINGUN equipo reporta el nombre de '
             'ningun archivo, sin importar que aplicaciones esten marcadas. '
             'Se puede dejar todo configurado y encenderlo el dia que el aviso '
             'de privacidad este firmado.')

    # ---- capturas de pantalla -------------------------------------------
    #
    # APAGADO de fabrica y con un motivo que no es tecnico: el aviso de
    # privacidad vigente promete EN NEGRITAS que no se toman capturas. Mientras
    # ese sea el texto firmado, encender esto rompe una promesa escrita. La
    # funcion se construye para que este lista el dia que exista un aviso nuevo
    # firmado, no para encenderla antes.
    screenshot_enabled = fields.Boolean(
        string='Capturas de pantalla', default=False,
        help='Interruptor general. Apagado, NINGUN equipo toma ni guarda una '
             'sola captura, sin importar que ordenes se manden. No lo '
             'enciendas mientras el aviso de privacidad firmado diga que no se '
             'toman capturas.')
    screenshot_unclassified_minutes = fields.Integer(
        string='Minutos en una app sin clasificar', default=30,
        help='Si alguien pasa mas de estos minutos seguidos en una aplicacion '
             'que el catalogo no conoce, el equipo toma UNA captura para poder '
             'identificarla. Esa aplicacion no vuelve a disparar nunca. '
             '0 = apagar este disparador y dejar solo las que se piden a mano.')
    screenshot_retention_days = fields.Integer(
        string='Conservar las capturas (dias)', default=30,
        help='Plazo PROPIO, aparte del resto del dato y mas corto a proposito: '
             'una imagen de la pantalla de alguien envejece mal. 0 = no borrar, '
             'que con capturas es una decision que hay que tomar a conciencia.')

    # ---- monitoreo movil (Android) --------------------------------------
    #
    # APAGADO de fabrica, misma razon que capturas: es la pieza que trae
    # UBICACION y LLAMADAS, lo mas sensible del sistema. Mientras este apagado,
    # la ingesta movil NO guarda nada aunque un telefono la mande, y el aviso de
    # privacidad tiene que cubrir ubicacion y llamadas antes de encenderlo. Son
    # telefonos PROPIEDAD DE LA EMPRESA, con consentimiento firmado.
    mobile_enabled = fields.Boolean(
        string='Monitoreo movil (Android)', default=False,
        help='Interruptor general del lado movil. Apagado, NINGUN telefono '
             'guarda ubicacion, uso de apps ni llamadas, sin importar que '
             'reporte. Enciendelo solo con el aviso de privacidad que cubra '
             'ubicacion y llamadas ya firmado.')
    mobile_location_minutes = fields.Integer(
        string='Ubicacion cada (min)', default=5,
        help='Cada cuantos minutos el telefono toma y guarda su posicion. '
             'Mas seguido = mas precision de recorrido y mas bateria. '
             '0 = no registrar ubicacion (solo apps y llamadas).')
    mobile_collect_calls = fields.Boolean(
        string='Registrar llamadas', default=True,
        help='Numero, direccion y duracion de las llamadas del telefono.')
    mobile_collect_apps = fields.Boolean(
        string='Registrar uso de apps', default=True,
        help='Segundos en primer plano por aplicacion y dia.')
    # --- Grabacion de la visita (coaching con IA) ---
    # APAGADO de fabrica. Es lo mas sensible del lado movil: graba la
    # conversacion de la visita (micro) para que la IA la analice como gerente
    # senior. Enciendelo solo con la responsabilidad legal cubierta.
    visita_grabar = fields.Boolean(
        string='Grabar la visita para analisis de IA', default=False,
        help='Interruptor general. Apagado, NINGUN telefono graba la visita, y '
             'el audio que llegara se rechaza. La grabacion arranca con el '
             'check-in y se corta sola al alejarse del cliente.')
    visita_grabar_radio_m = fields.Integer(
        string='Radio de la visita (m)', default=200,
        help='Al alejarse mas de este radio del punto de llegada, la grabacion '
             'se detiene sola (se dio por terminada la visita).')
    visita_grabar_max_min = fields.Integer(
        string='Duracion maxima de grabacion (min)', default=90,
        help='Tope de seguridad: la grabacion se corta a los N minutos aunque '
             'el telefono siga en el sitio.')
    # PIN de proteccion (anti-desinstalacion) para equipos que NO se pueden
    # aprovisionar como Device Owner (telefonos ya en uso). El telefono lo pide
    # antes de dejar desinstalar Foco, forzar su detencion o desactivar su
    # administrador. Es POR EMPRESA (una sola configuracion). En un equipo que si
    # es Device Owner no hace falta: alli el bloqueo es del sistema.
    mobile_uninstall_pin = fields.Char(
        string='PIN de proteccion (desinstalar/pausar)', size=8,
        help='PIN que el telefono pide antes de dejar desinstalar Foco, forzar '
             'su detencion o desactivar su administrador, en equipos que NO son '
             'Device Owner. 4 a 8 digitos. Vacio = sin PIN.')
    # Capturas de pantalla del movil. Interruptor PROPIO (aparte del de la
    # laptop) porque un pantallazo del telefono es lo mas invasivo del sistema:
    # solo se enciende con el aviso de privacidad firmado que lo contemple, y
    # solo funciona en equipos con el servicio de accesibilidad activo (Android
    # 11+). Reusa los minutos y la retencion de las capturas de la laptop.
    mobile_screenshot_enabled = fields.Boolean(
        string='Capturas de pantalla (movil)', default=False,
        help='Apagado, ningun telefono toma pantallazos. Enciendelo solo con el '
             'aviso de privacidad firmado que contemple capturas. Requiere el '
             'servicio de accesibilidad de Foco activo (Android 11+).')
    # Apps que se fotografian PERIODICAMENTE mientras esten en primer plano (no
    # una sola vez), p.ej. WhatsApp. La lista vive en el servidor -no en el
    # telefono- para poder cambiarla sin reinstalar la app. Sembrada con los dos
    # paquetes de WhatsApp; el admin agrega o quita los que quiera, uno por linea.
    mobile_screenshot_monitor_packages = fields.Text(
        string='Apps a fotografiar cada N min (una por linea)',
        default='com.whatsapp\ncom.whatsapp.w4b',
        help='Paquetes Android que se capturan CADA CIERTO TIEMPO mientras esten '
             'en primer plano, no una sola vez, p.ej. WhatsApp '
             '(com.whatsapp) y WhatsApp Business (com.whatsapp.w4b). Uno por '
             'linea. El resto de las apps solo se capturan a mano o al '
             'descubrirlas por primera vez.')
    mobile_screenshot_monitor_minutes = fields.Integer(
        string='Cada cuantos minutos (apps monitoreadas)', default=15,
        help='Cada cuantos minutos se toma una captura mientras una app de la '
             'lista de arriba esta en primer plano. 0 = usar los mismos minutos '
             'de las apps sin clasificar.')
    mobile_screenshot_capture_unregistered = fields.Boolean(
        string='Fotografiar apps NO registradas cada N min', default=True,
        help='Encendido, cualquier app que NO este marcada como registrada en '
             'el catalogo movil (una app personal o desconocida) se fotografia '
             'cada N min mientras este al frente, igual que las monitoreadas. '
             'Las apps de trabajo registradas y las de la lista "nunca capturar" '
             'quedan fuera. Apagado, solo se toma UNA captura de descubrimiento.')

    def mobile_screenshot_monitor_list(self):
        """Los paquetes a fotografiar en forma periodica, ya limpios. Acepta
        separados por linea o por coma; ignora vacios y espacios."""
        self.ensure_one()
        txt = (self.mobile_screenshot_monitor_packages or '').replace(',', '\n')
        vistos = []
        for p in txt.splitlines():
            p = p.strip()
            if p and p not in vistos:
                vistos.append(p)
        return vistos

    @api.constrains('mobile_uninstall_pin')
    def _check_uninstall_pin(self):
        for r in self:
            p = (r.mobile_uninstall_pin or '').strip()
            if p and (not p.isdigit() or not (4 <= len(p) <= 8)):
                raise ValidationError(
                    'El PIN de proteccion debe ser de 4 a 8 digitos (o vacio).')

    def mobile_config(self):
        """Lo que el telefono necesita saber para comportarse. Viaja en el
        enrolamiento y en cada envio, para que apagarlo surta efecto rapido."""
        self.ensure_one()
        return {
            'enabled': self.mobile_enabled,
            'location_minutes': self.mobile_location_minutes or 0,
            'collect_calls': self.mobile_collect_calls,
            'collect_apps': self.mobile_collect_apps,
            'uninstall_pin': (self.mobile_uninstall_pin or '').strip(),
            'visita_grabar': self.visita_grabar,
            'visita_radio_m': self.visita_grabar_radio_m or 200,
            'visita_max_min': self.visita_grabar_max_min or 90,
        }

    # ---- analisis de llamadas de WhatsApp (laptop) -----------------------
    #
    # APAGADO de fabrica y con DOS candados: este interruptor general y la
    # marca por equipo (foco.computer.call_review), que solo se pone con el
    # consentimiento firmado de esa persona. Es lo mas invasivo del sistema:
    # el agente graba microfono y bocinas mientras WhatsApp tenga el
    # microfono. Lo que se guarda es el VEREDICTO (trabajo / personal), no la
    # llamada: el audio se transcribe al llegar y se descarta, y la
    # transcripcion se borra en cuanto el modelo decide.
    call_review_enabled = fields.Boolean(
        string='Analizar llamadas de WhatsApp (laptop)', default=False,
        help='Interruptor general. Apagado, NINGUN equipo graba aunque tenga '
             'la marca puesta, y los trozos que llegaran se rechazan. '
             'Enciendelo solo con el aviso de privacidad firmado que contemple '
             'el analisis de llamadas.')
    call_review_apps = fields.Text(
        string='Apps cuyas llamadas se analizan (una por linea)',
        default='5319275A.WhatsAppDesktop\nWhatsApp.Root.exe\nWhatsApp.exe',
        help='Nombre con el que Windows registra a la app que toma el microfono '
             '(el que aparece en los eventos «Entro a una llamada»). WhatsApp '
             'de escritorio es 5319275A.WhatsAppDesktop. Una llamada de '
             'WhatsApp Web dentro de Chrome NO se distingue de otra llamada del '
             'navegador y no se analiza.')
    call_review_notice = fields.Text(
        string='Aviso en pantalla al empezar',
        default='Foco esta analizando esta llamada de WhatsApp para clasificarla como '
                'trabajo o personal. Nadie la escucha y el audio se borra al terminar.',
        help='Se muestra 8 segundos al detectar la llamada. Vacio = sin aviso '
             '(no recomendable).')
    call_review_context = fields.Text(
        string='Contexto del negocio para el clasificador',
        default='FERBA (Industrias Tecnologicas EMP, Culiacan): fabricacion y venta de '
                'maquinaria agricola e industrial, refacciones, servicio y proyectos.',
        help='«Trabajo» se juzga respecto a lo que hace ESTA empresa. Una linea.')
    call_review_keep_reason = fields.Boolean(
        string='Guardar el motivo (maximo 12 palabras)', default=True,
        help='Apagado, solo queda el veredicto. En las llamadas personales el '
             'motivo es siempre «asunto personal», sin detalle.')
    call_review_keep_transcript = fields.Boolean(
        string='Guardar la transcripcion de la llamada', default=False,
        help='Apagado (de fabrica), la transcripcion se BORRA en cuanto el modelo '
             'da su veredicto y solo queda la etiqueta. Encendido, la transcripcion '
             'se conserva para poder validar si la IA clasifica bien. Es lo que se '
             'dijo en la llamada: enciendelo SOLO con el aviso de privacidad firmado '
             'que contemple guardar la transcripcion. La ven unicamente quienes '
             'administran Foco.')
    call_review_max_minutes = fields.Integer(
        string='Maximo de minutos por llamada', default=120,
        help='Pasado este tiempo el agente deja de grabar esa llamada.')
    call_review_chunk_minutes = fields.Integer(
        string='Subir el audio en trozos de (min)', default=3,
        help='Cada trozo se transcribe al llegar y se descarta. Trozos cortos '
             'suben mas seguido; largos cargan mas cada peticion. 1 a 10.')
    # ---- rebote de justificaciones vagas al empleado (enforcement) ----------
    justify_rebote_activo = fields.Boolean(
        string='Devolver justificaciones vagas al empleado', default=False,
        help='Encendido: cuando la IA marca una justificacion como VAGA (no da '
             'ninguna razon: un punto, vacio, basura), el periodo se REABRE y la '
             'ventana del agente vuelve a pedirla con un aviso, hasta que el '
             'empleado de un motivo coherente (o, tras varios intentos, queda solo '
             'para revision del admin). Apagado: solo se marca en «Periodos a '
             'revisar» para que el admin lo trate con la persona.')
    justify_rebote_msg = fields.Char(
        string='Aviso al devolver la justificacion',
        default='Tu justificacion anterior no explica que paso. Escribe el motivo '
                'real (aunque sea breve: «cita medica», «junta con proveedor», «fui '
                'al almacen») para poder seguir usando el equipo.',
        help='Lo que ve el empleado en la ventana del agente cuando se le devuelve '
             'una justificacion vaga. Se puede ajustar aqui sin recompilar el agente.')
    anthropic_api_key = fields.Char(
        string='API key de Anthropic (Claude)', compute='_compute_anthropic_api_key',
        inverse='_inverse_anthropic_api_key',
        help='Vive en un parametro del sistema (foco.anthropic_api_key). Nunca '
             'viaja a los equipos: la usa Odoo para el veredicto por vision, el '
             'chat de Tomy y la clasificacion. Es la IA principal.')
    openai_api_key = fields.Char(
        string='API key de OpenAI (solo transcripcion)', compute='_compute_openai_api_key',
        inverse='_inverse_openai_api_key',
        help='Vive en un parametro del sistema (foco.openai_api_key). Nunca '
             'viaja a los equipos. Solo se usa para TRANSCRIBIR audio de llamadas '
             '(Whisper): Claude no transcribe. Si no usas el analisis de llamadas, '
             'puedes dejarla vacia.')

    def _compute_anthropic_api_key(self):
        key = self.env['ir.config_parameter'].sudo().get_param('foco.anthropic_api_key') or ''
        for rec in self:
            rec.anthropic_api_key = key

    def _inverse_anthropic_api_key(self):
        for rec in self:
            self.env['ir.config_parameter'].sudo().set_param(
                'foco.anthropic_api_key', (rec.anthropic_api_key or '').strip())

    def _compute_openai_api_key(self):
        key = self.env['ir.config_parameter'].sudo().get_param('foco.openai_api_key') or ''
        for rec in self:
            rec.openai_api_key = key

    def _inverse_openai_api_key(self):
        for rec in self:
            self.env['ir.config_parameter'].sudo().set_param(
                'foco.openai_api_key', (rec.openai_api_key or '').strip())

    @api.constrains('call_review_chunk_minutes', 'call_review_max_minutes')
    def _check_call_review(self):
        for r in self:
            if not (1 <= (r.call_review_chunk_minutes or 0) <= 10):
                raise ValidationError('Los trozos de audio van de 1 a 10 minutos.')
            if (r.call_review_max_minutes or 0) < 1:
                raise ValidationError('El maximo por llamada tiene que ser al menos 1 minuto.')

    def call_review_apps_list(self):
        self.ensure_one()
        vistos = []
        for linea in (self.call_review_apps or '').replace(',', '\n').splitlines():
            linea = linea.strip()
            if linea and linea not in vistos:
                vistos.append(linea)
        return vistos

    def call_review_config(self, computer):
        """Lo que el agente de ESE equipo necesita saber. Viaja en cada envio
        para que apagarlo -en general o por equipo- surta efecto en el
        siguiente ciclo, sin reinstalar nada."""
        self.ensure_one()
        activo = bool(self.call_review_enabled and computer and computer.call_review
                      and self.env['foco.openai'].configurado())
        return {
            'enabled': activo,
            'apps': self.call_review_apps_list(),
            'aviso': (self.call_review_notice or '').strip(),
            'max_min': self.call_review_max_minutes or 120,
            'chunk_min': self.call_review_chunk_minutes or 3,
        }

    def call_review_allowed(self, computer):
        return bool(self.call_review_enabled and computer and computer.call_review)

    # ---- veredicto por IA ante actividad sospechosa (vision) --------------
    # La IA vive en Odoo. Cuando el agente mide un patron de actividad simulada
    # (pantalla sin cambio con input, solo mouse, clics ritmicos, input
    # inyectado) durante T minutos, toma unas pocas capturas y las sube; Odoo
    # pide al modelo de vision que diga que se ve. Ordenado por la direccion y
    # escrito en la politica de uso de equipos (7-oct-2026).
    vision_enabled = fields.Boolean(
        string='Veredicto por IA ante actividad sospechosa', default=False,
        help='Interruptor general. Apagado, ningun equipo captura por este motivo y '
             'lo que llegara se rechaza. Requiere la API key de OpenAI. Las '
             'capturas y el veredicto los ven solo quienes administran Foco.')
    vision_umbral_minutos = fields.Integer(
        string='Minutos de patron sospechoso antes de capturar', default=15,
        help='T. El agente abre un episodio cuando un patron (pantalla sin cambio '
             'con input, solo mouse, clics ritmicos, input inyectado) lleva estos '
             'minutos seguidos. 3 a 240.')
    vision_max_capturas = fields.Integer(
        string='Capturas por episodio (maximo)', default=4,
        help='Cuantas capturas como mucho se toman en un episodio. Mas no mejora el '
             'veredicto. 1 a 12.')
    vision_capturas_cada_min = fields.Integer(
        string='Minutos entre capturas del episodio', default=5,
        help='Separacion entre capturas mientras el patron siga. Ver DOS pantallas '
             'identicas con minutos de por medio es lo que distingue una simulacion '
             'de una pausa. 1 a 60.')
    vision_detalle = fields.Selection(
        [('low', 'Baja: rapida y barata (basta para saber que hay en pantalla)'),
         ('high', 'Alta: lee texto pequeno, cuesta varias veces mas')],
        string='Resolucion con la que mira el modelo', default='low')
    vision_notice = fields.Text(
        string='Aviso en pantalla al capturar',
        default='Foco detecto actividad sin cambios en pantalla durante varios minutos y '
                'tomo una captura como evidencia, conforme a la politica de uso de equipos.',
        help='Se muestra unos segundos al tomar la primera captura del episodio. '
             'Vacio = sin aviso (no recomendable).')
    vision_retention_days = fields.Integer(
        string='Conservar las capturas (dias)', default=0,
        help='0 = se conservan como evidencia, no se borran nunca (decision del '
             '7-oct-2026). Con un numero, el proceso de cada 5 min borra las mas '
             'antiguas; el veredicto se queda.')
    jornada_tope_semana = fields.Float(
        string='Tope legal de horas por semana', default=40.0,
        help='Solo para AVISAR en la pantalla de horarios cuando un empleado lo '
             'rebasa. No recorta ni bloquea nada: la cifra la fija el area legal '
             '(hoy el limite va camino a 40/38 h).')

    # ---- adherencia al turno y reporte diario (10-oct-2026) ---------------
    adherencia_gracia_min = fields.Integer(
        string='Tolerancia de entrada (min)', default=10,
        help='Minutos despues de su hora de entrada a partir de los cuales una '
             'llegada se marca "tarde". Medido con 30 dias de checadas de FERBA: '
             'con 10 min, el 39 por ciento de las entradas salen tarde, casi todas de 4 '
             'personas cuyo horario real no es el del calendario estandar. '
             'Corregir SU horario en Horarios laborales es lo correcto; subir '
             'esta tolerancia a todos, no. Se puede dar una tolerancia distinta '
             'a una persona en su ficha de empleado.')
    reporte_diario_activo = fields.Boolean(
        string='Enviar el reporte diario', default=False,
        help='Cada dia laborable, a la hora indicada, se arma el reporte del dia '
             'con los numeros reales del tablero y se manda a los destinatarios.')
    reporte_diario_hora = fields.Float(
        string='Hora de envio', default=17.75,
        help='Hora local de la empresa (la del calendario laboral). 17:45 de '
             'fabrica: despues de la salida de las 17:30 y antes de la revision '
             'de las 18:00.')
    reporte_diario_user_ids = fields.Many2many(
        'res.users', 'foco_reporte_diario_user_rel', 'settings_id', 'user_id',
        string='Destinatarios',
        help='Usuarios de Odoo que lo reciben, por su correo.')
    reporte_diario_emails = fields.Char(
        string='Otros correos',
        help='Correos adicionales separados por coma, para quien no tiene usuario.')
    reporte_diario_ultimo = fields.Date(
        string='Ultimo enviado', readonly=True,
        help='El dia del ultimo reporte enviado por el proceso automatico. Se '
             'envia una vez por dia.')

    @api.constrains('adherencia_gracia_min', 'reporte_diario_hora')
    def _check_adherencia(self):
        for r in self:
            if r.adherencia_gracia_min < 0 or r.adherencia_gracia_min > 120:
                raise ValidationError('La tolerancia de entrada va de 0 a 120 minutos.')
            if r.reporte_diario_hora < 0 or r.reporte_diario_hora >= 24:
                raise ValidationError('La hora de envio debe estar entre 00:00 y 23:59.')

    def action_asistencia(self):
        """Abre la configuracion de asistencia y reporte diario."""
        rec = self.get_settings()
        return {
            'type': 'ir.actions.act_window', 'name': 'Asistencia y reporte diario',
            'res_model': 'foco.settings', 'res_id': rec.id,
            'view_mode': 'form', 'target': 'current',
            'view_id': self.env.ref('foco_monitor.foco_settings_view_form_asistencia').id,
        }

    def action_enviar_reporte_ahora(self):
        """Genera el reporte de HOY y lo manda a los destinatarios configurados.
        Es el boton de prueba: no espera a la hora programada."""
        self.ensure_one()
        rep = self.env['foco.daily.report'].sudo().generar(fields.Date.context_today(self))
        enviados = rep.enviar()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success' if enviados else 'warning', 'sticky': False,
                       'title': 'Reporte diario',
                       'message': ('Enviado a %s' % enviados) if enviados
                                  else 'Generado, pero no hay destinatarios con correo.'},
        }

    def action_ver_reporte_hoy(self):
        """Arma (o rearma) el reporte de hoy y lo abre, sin enviarlo."""
        self.ensure_one()
        rep = self.env['foco.daily.report'].sudo().generar(fields.Date.context_today(self))
        return {
            'type': 'ir.actions.act_window', 'name': rep.display_name,
            'res_model': 'foco.daily.report', 'res_id': rep.id,
            'view_mode': 'form', 'target': 'current',
        }

    @api.constrains('vision_umbral_minutos', 'vision_max_capturas', 'vision_capturas_cada_min')
    def _check_vision(self):
        for r in self:
            if not (3 <= (r.vision_umbral_minutos or 0) <= 240):
                raise ValidationError('El umbral de actividad sospechosa va de 3 a 240 minutos.')
            if not (1 <= (r.vision_max_capturas or 0) <= 12):
                raise ValidationError('Las capturas por episodio van de 1 a 12.')
            if not (1 <= (r.vision_capturas_cada_min or 0) <= 60):
                raise ValidationError('La separacion entre capturas va de 1 a 60 minutos.')

    def vision_allowed(self, computer):
        return bool(self.vision_enabled and computer and self.env['foco.openai'].configurado())

    def vision_config(self, computer):
        """Lo que el agente de ESE equipo necesita para abrir episodios. Viaja
        en cada envio: apagarlo surte efecto en el siguiente ciclo."""
        self.ensure_one()
        return {
            'enabled': self.vision_allowed(computer),
            'umbral_min': self.vision_umbral_minutos or 15,
            'max_capturas': self.vision_max_capturas or 4,
            'cada_min': self.vision_capturas_cada_min or 5,
            'aviso': (self.vision_notice or '').strip(),
            'patrones': ['pantalla_sin_cambio', 'solo_mouse', 'clics_ritmicos', 'input_inyectado'],
        }

    # ---- cuanto tiempo se guarda el dato --------------------------------
    # APAGADA de fabrica (0). Una purga encendida por omision borraria datos
    # que nadie decidio borrar, y ese borrado no se puede deshacer.
    retention_months = fields.Integer(
        string='Conservar el detalle (meses)', default=0,
        help='0 = no se borra nada, nunca. Con un numero, un proceso diario '
             'elimina el detalle de uso y los periodos sin actividad mas '
             'antiguos que esos meses. En un sistema que mide a personas, poder '
             'decir cuanto tiempo se guarda el dato es parte del sistema.')
    retention_last_run = fields.Datetime(
        string='Ultima purga', readonly=True,
        help='Cuando corrio por ultima vez. Si esta vacio con la retencion '
             'encendida, el proceso no ha corrido.')
    retention_last_deleted = fields.Integer(
        string='Borrados en la ultima purga', readonly=True)
    retention_pending = fields.Integer(
        string='Pendientes de borrar', readonly=True,
        help='Renglones que ya superaron la retencion y que la ultima corrida '
             'no alcanzo a borrar. Se van en las siguientes corridas diarias.')

    installer = fields.Binary(string="Instalador (.exe)",
                              help="El FERBA-Foco-Setup.exe que descargan los empleados desde el correo "
                                   "y el que bajan los equipos para actualizarse solos.")
    installer_name = fields.Char(string="Nombre del instalador", default="FERBA-Foco-Setup.exe")

    # ---- version del agente de escritorio y actualizacion sola -----------
    # Mismo patron que el APK del movil: se sube el instalador y se declara su
    # version. El servicio de cada equipo pregunta a /foco/agent/latest,
    # compara el codigo con el suyo y, si el de Odoo es mayor, baja el .exe,
    # comprueba tamano y huella SHA-256 y lo corre en silencio. La huella se
    # calcula AQUI al subir el archivo, no la escribe nadie: sin huella no se
    # instala nada, porque una descarga a medias dejaria el equipo sin agente.
    agent_version_name = fields.Char(
        string='Version del agente', help='Etiqueta visible, p.ej. 2026.09.25.')
    agent_version_code = fields.Integer(
        string='Codigo de version del agente',
        help='Entero que sube en cada version (foco_version.VERSION_CODE, lo '
             'imprime el empaquetado). Un equipo se actualiza cuando el suyo '
             'es menor que este.')
    agent_autoupdate = fields.Boolean(
        string='Los equipos se actualizan solos', default=True,
        help='Apagado, el instalador solo sirve para la descarga manual: se '
             'puede subir una version sin que los equipos la reciban todavia.')
    installer_sha256 = fields.Char(string='Huella SHA-256 del instalador', readonly=True)
    installer_size = fields.Integer(string='Tamano del instalador (bytes)', readonly=True)
    installer_uploaded_at = fields.Datetime(string='Instalador subido', readonly=True)

    @api.model
    def _huella_instalador(self, b64):
        """(sha256 en hex, bytes) del binario tal como se subio."""
        if not b64:
            return '', 0
        raw = base64.b64decode(b64)
        return hashlib.sha256(raw).hexdigest(), len(raw)

    def _vals_con_huella(self, vals):
        if 'installer' in vals:
            sha, tam = self._huella_instalador(vals.get('installer'))
            vals = dict(vals, installer_sha256=sha or False, installer_size=tam,
                        installer_uploaded_at=fields.Datetime.now() if sha else False)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._vals_con_huella(v) for v in vals_list])

    def write(self, vals):
        return super().write(self._vals_con_huella(vals))

    def agent_latest(self):
        """Lo que contesta /foco/agent/latest al servicio de un equipo.

        `version_code` 0 = no hay nada que instalar: sin instalador subido,
        sin codigo de version, o con la actualizacion sola apagada. Solo se
        publica una version que tenga huella calculada.
        """
        self.ensure_one()
        if not (self.agent_autoupdate and self.installer
                and self.installer_sha256 and self.agent_version_code):
            return {'ok': True, 'version_code': 0}
        return {
            'ok': True,
            'version_code': self.agent_version_code,
            'version_name': self.agent_version_name or '',
            'sha256': self.installer_sha256,
            'size': self.installer_size,
            'name': self.installer_name or 'FERBA-Foco-Setup.exe',
        }

    # ---- app movil (APK) para repartir por QR ---------------------------
    # La Play Store no admite apps de monitoreo, asi que el APK se reparte por
    # sideload: se sube aqui y se descarga desde /foco/instalar (QR + token
    # efimero). El version_code sirve para el auto-update de la app.
    mobile_apk = fields.Binary(
        string='App movil (APK)',
        help='El .apk de Foco que se instala en los telefonos de la empresa. '
             'Se descarga escaneando el QR de /foco/instalar.')
    mobile_apk_name = fields.Char(string='Nombre del APK', default='foco.apk')
    mobile_apk_version_name = fields.Char(
        string='Version de la app', help='Etiqueta visible, p.ej. 1.9.0.')
    mobile_apk_version_code = fields.Integer(
        string='Codigo de version', help='Numero entero que sube en cada '
             'version (versionCode). Lo usa el auto-update para saber si hay '
             'una mas nueva.')
    mobile_apk_token_minutes = fields.Integer(
        string='Minutos que vive el enlace del QR', default=10,
        help='Cada QR de instalacion acuña un enlace de un solo uso que caduca '
             'a estos minutos. Corto a proposito: es la seguridad real, no el '
             'codigo de teclas.')

    def mobile_apk_ready(self):
        """True si hay un APK publicado para repartir."""
        self.ensure_one()
        return bool(self.mobile_apk)

    # ---- APK "stub" de ALTA (pasa Play Protect en el QR) ----------------
    # Google Play Protect BLOQUEA el Foco completo al instalarlo por QR (micro,
    # registro de llamadas, accesibilidad, uso de apps = firma de "app de
    # vigilancia"). El stub es una app MINIMA, con la MISMA firma y el mismo
    # paquete, que si pasa Play Protect: queda de dueño del equipo, apaga Play
    # Protect y baja/instala el Foco completo en silencio (que ya no se bloquea).
    # El QR apunta su descarga al stub; el Foco completo lo baja el stub de
    # `/foco/app/full`.
    mobile_stub_apk = fields.Binary(
        string='APK de alta (stub)',
        help='El .apk MINIMO de alta que el QR instala primero para esquivar '
             'Play Protect. Debe ir firmado con la MISMA llave que el Foco '
             'completo y con un versionCode MENOR.')
    mobile_stub_apk_name = fields.Char(string='Nombre del APK de alta', default='foco-alta.apk')
    mobile_stub_apk_version_code = fields.Integer(
        string='Codigo de version del stub')

    def mobile_stub_ready(self):
        """True si hay stub de alta publicado (y el Foco completo que baja)."""
        self.ensure_one()
        return bool(self.mobile_stub_apk) and bool(self.mobile_apk)

    # ---- quien puede ver Foco -------------------------------------------
    # Se administra desde aqui y no desde Ajustes > Usuarios para que el
    # responsable de Foco no necesite permisos generales de administracion de
    # Odoo. Los campos no se almacenan: son una vista de los grupos, de modo que
    # no puede haber dos verdades sobre quien tiene acceso.
    access_user_ids = fields.Many2many(
        'res.users', string='Pueden ver Foco',
        compute='_compute_accesos', inverse='_inverse_access_user',
        help='Ven el tablero y el uso. Sin estar aqui, la aplicacion no les '
             'aparece siquiera en el menu.')
    access_manager_ids = fields.Many2many(
        'res.users', string='Administran Foco',
        compute='_compute_accesos', inverse='_inverse_access_manager',
        help='Ademas de ver, clasifican apps y sitios, envian ordenes remotas y '
             'gestionan estos accesos. Incluye de forma automatica el permiso '
             'de consulta.')

    def _grupo(self, xmlid):
        return self.env.ref('foco_monitor.' + xmlid, raise_if_not_found=False)

    @api.depends_context('uid')
    def _compute_accesos(self):
        g_user = self._grupo('group_foco_user')
        g_admin = self._grupo('group_foco_manager')
        for rec in self:
            rec.access_user_ids = g_user.user_ids if g_user else False
            rec.access_manager_ids = g_admin.user_ids if g_admin else False

    def _inverse_access_user(self):
        g_user = self._grupo('group_foco_user')
        if not g_user:
            return
        for rec in self:
            g_user.sudo().user_ids = [(6, 0, rec.access_user_ids.ids)]

    def _inverse_access_manager(self):
        g_admin = self._grupo('group_foco_manager')
        if not g_admin:
            return
        for rec in self:
            g_admin.sudo().user_ids = [(6, 0, rec.access_manager_ids.ids)]

    @api.model
    def action_accesos(self):
        """Abre la pantalla de accesos sobre el registro unico de configuracion."""
        ajustes = self.get_settings()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Quien puede ver Foco',
            'res_model': 'foco.settings',
            'view_mode': 'form',
            'res_id': ajustes.id,
            'target': 'current',
            'views': [(self.env.ref('foco_monitor.foco_settings_view_form_accesos').id, 'form')],
        }

    @api.model
    def action_movil(self):
        """Abre los ajustes del monitoreo movil sobre el registro unico."""
        ajustes = self.get_settings()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Monitoreo movil',
            'res_model': 'foco.settings',
            'view_mode': 'form',
            'res_id': ajustes.id,
            'target': 'current',
            'views': [(self.env.ref('foco_monitor.foco_settings_view_form_movil').id, 'form')],
        }

    @api.model
    def action_alcance(self):
        """Abre la lista de quien tiene acceso, para repartir departamentos.

        Se limita a las personas con acceso a Foco: repartir alcance a alguien
        que ni siquiera ve la aplicacion no significa nada, y la lista completa
        de usuarios de Odoo solo haria ruido.
        """
        g_ver = self._grupo('group_foco_user')
        g_admin = self._grupo('group_foco_manager')
        usuarios = (g_ver.user_ids if g_ver else self.env['res.users'])
        usuarios |= (g_admin.user_ids if g_admin else self.env['res.users'])
        return {
            'type': 'ir.actions.act_window',
            'name': 'Que ve cada quien',
            'res_model': 'res.users',
            'view_mode': 'list',
            'views': [(self.env.ref('foco_monitor.foco_res_users_view_list_alcance').id, 'list')],
            'domain': [('id', 'in', usuarios.ids)],
            'target': 'current',
            'help': '<p class="o_view_nocontent_smiling_face">Todavia nadie '
                    'tiene acceso a Foco</p><p>Concede el acceso primero en '
                    '"Quien puede ver Foco".</p>',
        }

    @api.model
    def get_settings(self):
        return self.search([], limit=1) or self.create({})

    # ---- la jornada: checador primero, calendario despues -----------------
    #
    # Cuando una persona "usa el checador": tiene un registro de asistencia
    # ese dia o en los 30 anteriores. Es una definicion declarada, no un
    # umbral afinado: la misma que aplica el agente al etiquetar en vivo
    # (`asistencia_para`) y la que aplica la jornada al recalcularse
    # (`jornada_de`), para que los dos digan lo mismo de un mismo dia se mire
    # cuando se mire. Quien no lo usa se rige por su calendario laboral; quien
    # no tiene ninguno, no tiene jornada contra que contrastar.
    CHECADOR_VENTANA_DIAS = 30

    @api.model
    def _a_utc(self, dt_local):
        return dt_local.astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def _usa_checador(self, employee, dia, zona):
        if not employee or 'hr.attendance' not in self.env:
            return False
        desde = zona.localize(datetime.combine(
            dia - timedelta(days=self.CHECADOR_VENTANA_DIAS), time.min))
        hasta = zona.localize(datetime.combine(dia, time.max))
        try:
            return bool(self.env['hr.attendance'].sudo().search_count(
                [('employee_id', '=', employee.id),
                 ('check_in', '>=', self._a_utc(desde)),
                 ('check_in', '<=', self._a_utc(hasta))], limit=1))
        except Exception:
            return False

    @api.model
    def _tramos_checados(self, employee, dia, zona):
        """[(h_ini, h_fin, abierta)] de lo checado ese dia, en horas locales.

        Una asistencia sin salida se cierra en "ahora" si es hoy (sigue
        checada) y en el fin del dia si es un dia pasado (se le olvido checar
        salida): lo checado sin actividad se vera, que es lo correcto.
        """
        if not employee or 'hr.attendance' not in self.env:
            return []
        base = zona.localize(datetime.combine(dia, time.min))
        fin_dia = zona.localize(datetime.combine(dia, time.max))
        ahora = datetime.now(zona)
        tope = min(fin_dia, ahora) if dia == ahora.date() else fin_dia
        if dia > ahora.date() or tope <= base:
            return []
        try:
            regs = self.env['hr.attendance'].sudo().search_read(
                [('employee_id', '=', employee.id),
                 ('check_in', '<=', self._a_utc(tope)),
                 '|', ('check_out', '=', False), ('check_out', '>=', self._a_utc(base))],
                ['check_in', 'check_out'], order='check_in')
        except Exception:
            return []

        def hora(d):
            return d.hour + d.minute / 60.0 + d.second / 3600.0

        out = []
        for r in regs:
            ci = pytz.UTC.localize(fields.Datetime.to_datetime(r['check_in'])).astimezone(zona)
            abierta = not r['check_out']
            co = tope if abierta else pytz.UTC.localize(
                fields.Datetime.to_datetime(r['check_out'])).astimezone(zona)
            a, b = max(ci, base), min(co, tope)
            if b <= a:
                continue
            h_a = 0.0 if a <= base else hora(a)
            h_b = 24.0 if b >= fin_dia else hora(b)
            out.append((round(h_a, 4), round(h_b, 4), abierta))
        return sorted(out)

    @api.model
    def jornada_de(self, employee, dia, zona):
        """La jornada de esa persona ese dia: fuente, tramos y duracion."""
        if employee and self._usa_checador(employee, dia, zona):
            tramos = [(a, b) for a, b, _ in self._tramos_checados(employee, dia, zona)]
            return {'fuente': 'checador', 'tramos': tramos,
                    'horas': round(sum(b - a for a, b in tramos), 3)}
        intervals = self.calendar_intervals(employee.resource_calendar_id if employee else None)
        if intervals:
            tramos = list(intervals.get(dia.isoweekday(), []))
            return {'fuente': 'calendario', 'tramos': tramos,
                    'horas': round(sum(b - a for a, b in tramos), 3)}
        return {'fuente': 'ninguno', 'tramos': [], 'horas': 0.0}

    @api.model
    def asistencia_para(self, employee, computer=None):
        """Lo que el agente necesita para etiquetar EN VIVO "en jornada":
        si esta persona usa el checador y si ahora mismo esta checada."""
        salida = {'usa': False, 'checado': False, 'desde': None, 'fuente': 'ninguno'}
        if not employee:
            return salida
        zona = self._tzinfo_for(employee, computer)
        ahora = datetime.now(zona)
        if self._usa_checador(employee, ahora.date(), zona):
            salida['usa'] = True
            salida['fuente'] = 'checador'
            abierta = self.env['hr.attendance'].sudo().search(
                [('employee_id', '=', employee.id), ('check_out', '=', False)],
                order='check_in desc', limit=1)
            if abierta:
                salida['checado'] = True
                salida['desde'] = pytz.UTC.localize(abierta.check_in).astimezone(zona).strftime('%H:%M')
        elif self.calendar_intervals(employee.resource_calendar_id):
            salida['fuente'] = 'calendario'
        return salida

    @api.model
    def jornada_fuentes(self):
        """De donde sale la jornada de cada persona monitoreada, HOY.

        Es lo que ensena Configuracion > Jornada, y no se edita ahi: el
        checador es de Asistencias y el calendario de RRHH. Duplicarlos en
        Foco seria tener dos verdades sobre la jornada de una persona.
        """
        salida = []
        empleados = self.env['foco.computer'].search(
            [('employee_id', '!=', False)]).mapped('employee_id')
        for emp in empleados:
            zona = self._tzinfo_for(emp)
            hoy = datetime.now(zona).date()
            usa = self._usa_checador(emp, hoy, zona)
            cal = emp.resource_calendar_id
            con_cal = bool(self.calendar_intervals(cal))
            ultima = ''
            if 'hr.attendance' in self.env:
                reg = self.env['hr.attendance'].sudo().search(
                    [('employee_id', '=', emp.id)], order='check_in desc', limit=1)
                if reg:
                    ultima = fields.Date.to_string(
                        pytz.UTC.localize(reg.check_in).astimezone(zona).date())
            salida.append({
                'id': emp.id, 'nombre': emp.name,
                'fuente': 'checador' if usa else ('calendario' if con_cal else 'ninguno'),
                'calendario': cal.name or '', 'calendario_id': cal.id or 0,
                'calendario_valido': con_cal, 'ultima_checada': ultima,
            })
        orden = {'checador': 0, 'calendario': 1, 'ninguno': 2}
        salida.sort(key=lambda p: (orden[p['fuente']], p['nombre']))
        conteo = {'checador': 0, 'calendario': 0, 'ninguno': 0}
        for p in salida:
            conteo[p['fuente']] += 1
        return {'personas': salida, 'conteo': conteo,
                'ventana_dias': self.CHECADOR_VENTANA_DIAS}

    @api.model
    def calendar_intervals(self, calendar):
        """Intervalos de trabajo por dia ISO (1=lunes), EXCLUYENDO la comida.

        Ojo: en resource.calendar la comida NO es un hueco, es una LINEA con
        day_period='lunch' (por ejemplo 12.0-13.0). Si no se excluye, se
        contaria como tiempo de trabajo.

        dayofweek de Odoo es '0'=lunes; el agente usa ISO 1=lunes.
        """
        out = {}
        if not calendar or calendar.two_weeks_calendar:
            return out          # calendario quincenal alternante: no soportado
        for att in calendar.attendance_ids:
            if att.day_period == 'lunch':
                continue
            try:
                iso = int(att.dayofweek) + 1
            except (TypeError, ValueError):
                continue
            if att.hour_to > att.hour_from:
                out.setdefault(iso, []).append((att.hour_from, att.hour_to))
        for iso in out:
            out[iso].sort()
        return out

    @api.model
    def lunch_intervals(self, calendar):
        """Franjas de comida por dia ISO. Sirven para PRESUGERIR el motivo."""
        out = {}
        if not calendar or calendar.two_weeks_calendar:
            return out
        for att in calendar.attendance_ids:
            if att.day_period != 'lunch':
                continue
            try:
                iso = int(att.dayofweek) + 1
            except (TypeError, ValueError):
                continue
            out.setdefault(iso, []).append((att.hour_from, att.hour_to))
        return out

    @api.model
    def _tzinfo_for(self, employee, computer=None):
        """La zona con la que se fecha la jornada de esa persona.

        El orden va de lo mas declarado a lo mas deducido: lo que dice RRHH
        primero, y el desfase que reporta la maquina solo cuando no hay nada
        configurado. Asumir UTC en silencio es lo que no se puede hacer: mueve
        un dia entero de actividad y no deja rastro de por que.
        """
        # Manda lo que REPORTA la maquina, y no es un capricho: el agente ya
        # aplica el horario con su propio reloj (in_schedule usa la hora local
        # del equipo), asi que ese reloj ya era la autoridad de facto para
        # decidir que es "dentro de jornada". Alinear el servidor a el reduce el
        # numero de verdades en vez de aumentarlo. Ademas trae el horario de
        # verano ya aplicado por Windows, y sigue a la persona si trabaja desde
        # otro pais.
        #
        # La zona configurada en Odoo queda de respaldo porque en una base nueva
        # NADIE la decide: Odoo rellena 'UTC' solo, copiandola del usuario que
        # creo al empleado. Tratar ese relleno como una decision es lo que movia
        # un dia entero de actividad todas las noches, en silencio.
        if computer is None and employee:
            computer = self.env['foco.computer'].sudo().search(
                [('employee_id', '=', employee.id),
                 ('utc_offset_min', '!=', 0)], limit=1)
        if computer and computer.utc_offset_min:
            return pytz.FixedOffset(computer.utc_offset_min)
        nombre = None
        if employee:
            nombre = employee.tz or employee.resource_calendar_id.tz
        nombre = nombre or self.env.company.resource_calendar_id.tz
        if nombre:
            try:
                return pytz.timezone(nombre)
            except Exception:
                pass
        return pytz.UTC

    @api.model
    def _tz_for(self, employee):
        if employee:
            return (employee.tz
                    or employee.resource_calendar_id.tz
                    or self.env.company.resource_calendar_id.tz
                    or 'UTC')
        return self.env.company.resource_calendar_id.tz or 'UTC'

    def schedule_for(self, employee=None):
        """El calendario laboral que se le manda al agente de ese equipo.

        Es el respaldo del agente: etiqueta con el checador si la persona lo
        usa (bloque `asistencia`) y con esto si no. Sin calendario no hay
        jornada: `enabled` False y el agente etiqueta todo "en jornada", que
        es lo conservador (no acusa a nadie de trabajar fuera de nada).
        """
        self.ensure_one()
        calendar = employee.resource_calendar_id if employee else None
        intervals = self.calendar_intervals(calendar)
        if intervals:
            return {"enabled": True,
                    "tz": calendar.tz or self._tz_for(employee),
                    "source": "employee_calendar",
                    "intervals": {str(k): v for k, v in intervals.items()}}
        return {"enabled": False, "intervals": {}, "source": "sin_calendario",
                "tz": self._tz_for(employee)}

    # ---- editor de horarios por empleado (Config > Horarios laborales) -----
    #
    # Edita el horario laboral NATIVO (resource.calendar) de cada empleado, que
    # es de donde ya salen TODAS las metricas (expected_seconds / jornada_de /
    # schedule_for). No se inventa una segunda verdad. Al personalizar a alguien
    # que comparte un calendario (el estandar), se le da el suyo propio para no
    # mover a los demas. El checador sigue mandando lo REAL en los dias que marca.
    DIAS_SEMANA = [(1, 'Lunes'), (2, 'Martes'), (3, 'Miércoles'), (4, 'Jueves'),
                   (5, 'Viernes'), (6, 'Sábado'), (7, 'Domingo')]

    @api.model
    def _hora_valida(self, v):
        """Un numero de hora (0..24) o None. Acepta float o cadena."""
        if v is None or v is False or v == '':
            return None
        try:
            return max(0.0, min(24.0, round(float(v), 4)))
        except (TypeError, ValueError):
            return None

    @api.model
    def _horario_dias(self, calendar):
        """El horario semanal EDITABLE de un calendario: por dia ISO, si trabaja,
        entrada, salida y comida. Reconstruye entrada=min y salida=max de las
        lineas de trabajo (la comida sale de la linea 'lunch')."""
        trabajo = self.calendar_intervals(calendar)   # {iso: [(a,b)...]} sin comida
        comidas = self.lunch_intervals(calendar)       # {iso: [(a,b)...]} lineas 'lunch'
        dias = []
        for iso, etiqueta in self.DIAS_SEMANA:
            spans = sorted(trabajo.get(iso) or [])
            com = comidas.get(iso) or []
            if spans:
                entrada = spans[0][0]
                salida = spans[-1][1]
                horas = round(sum(b - a for a, b in spans), 2)
                if com:
                    ci, cf = com[0][0], com[0][1]
                elif len(spans) >= 2 and spans[1][0] > spans[0][1]:
                    # El 'Standard 40h' de Odoo NO tiene linea de comida: la comida
                    # es el HUECO entre el bloque de la mañana y el de la tarde.
                    ci, cf = spans[0][1], spans[1][0]
                else:
                    ci = cf = None
            else:
                entrada = salida = ci = cf = None
                horas = 0.0
            dias.append({'iso': iso, 'etiqueta': etiqueta, 'trabaja': bool(spans),
                         'entrada': entrada, 'salida': salida,
                         'comida_inicio': ci, 'comida_fin': cf, 'horas': horas})
        return dias

    @api.model
    def _horario_plantillas(self):
        """Plantillas rapidas para no capturar dia por dia. 8:30 h = 9:00–18:30
        menos 1 h de comida."""
        def d(e, s, ci=None, cf=None):
            return {'entrada': e, 'salida': s, 'comida_inicio': ci, 'comida_fin': cf}
        lv = {str(i): d(9.0, 18.5, 13.0, 14.0) for i in range(1, 6)}
        lv8 = {str(i): d(9.0, 18.0, 13.0, 14.0) for i in range(1, 6)}
        return [
            {'clave': 'lv85', 'etiqueta': 'Lun–Vie 8:30 h', 'dias': lv},
            {'clave': 'lv85s5', 'etiqueta': 'Lun–Vie 8:30 h + Sáb 5 h',
             'dias': {**lv, '6': d(9.0, 14.0)}},
            {'clave': 'lv8', 'etiqueta': 'Lun–Vie 8 h', 'dias': lv8},
        ]

    @api.model
    def _fuente_hoy(self, emp):
        """De donde manda la jornada de esa persona HOY: checador o calendario."""
        zona = self._tzinfo_for(emp)
        hoy = datetime.now(zona).date()
        if self._usa_checador(emp, hoy, zona):
            return 'checador'
        return 'calendario' if self.calendar_intervals(emp.resource_calendar_id) else 'ninguno'

    @api.model
    def horarios_tablero(self):
        """Lista de empleados monitoreados con su horario resumido. Lo lee la
        pantalla OWL de Horarios laborales (sin sudo: respeta el alcance)."""
        ajustes = self.get_settings()
        empleados = self.env['foco.computer'].search(
            [('employee_id', '!=', False)]).mapped('employee_id')
        out = []
        for emp in empleados:
            cal = emp.resource_calendar_id
            dias = ajustes._horario_dias(cal)
            comparten = (self.env['hr.employee'].sudo().search_count(
                [('resource_calendar_id', '=', cal.id)]) if cal else 0)
            out.append({
                'id': emp.id, 'name': emp.name,
                'departamento': emp.department_id.name or '',
                'dias': [d['iso'] for d in dias if d['trabaja']],
                'horas_semana': round(sum(d['horas'] for d in dias), 2),
                'calendario': cal.name or '', 'compartido': comparten > 1,
                'fuente_hoy': self._fuente_hoy(emp),
            })
        out.sort(key=lambda p: p['name'])
        return {'empleados': out,
                'tope_semana': ajustes.jornada_tope_semana or 40.0,
                'plantillas': self._horario_plantillas()}

    @api.model
    def horario_empleado(self, employee_id):
        """El horario completo y editable de un empleado."""
        emp = self.env['hr.employee'].browse(int(employee_id or 0)).exists()
        if not emp:
            return {}
        ajustes = self.get_settings()
        cal = emp.resource_calendar_id
        comparten = (self.env['hr.employee'].sudo().search_count(
            [('resource_calendar_id', '=', cal.id)]) if cal else 0)
        return {
            'id': emp.id, 'name': emp.name,
            'departamento': emp.department_id.name or '',
            'calendario': cal.name or '', 'compartido': comparten > 1,
            'comparten': comparten,
            'tz': (cal.tz if cal else None) or ajustes._tz_for(emp),
            'tope_semana': ajustes.jornada_tope_semana or 40.0,
            'fuente_hoy': self._fuente_hoy(emp),
            'dias': ajustes._horario_dias(cal),
        }

    @api.model
    def _calendario_propio(self, emp):
        """El calendario que usa SOLO este empleado, para editarlo sin tocar a
        nadie mas. Si el actual lo comparte, es el de la empresa, o no tiene, se
        le clona/crea uno propio y se le asigna."""
        cal = emp.resource_calendar_id
        company_def = self.env.company.resource_calendar_id
        comparten = (self.env['hr.employee'].sudo().search_count(
            [('resource_calendar_id', '=', cal.id)]) if cal else 0)
        propio = bool(cal) and comparten <= 1 and cal.id != (company_def.id if company_def else 0)
        if propio:
            return cal
        nombre = 'Horario — %s' % emp.name
        if cal:
            nuevo = cal.sudo().copy()
            # resource.calendar.copy ignora el name y pone "(copy)": se fija aparte.
            nuevo.sudo().write({'name': nombre})
        else:
            nuevo = self.env['resource.calendar'].sudo().create({
                'name': nombre,
                'tz': emp.tz or (company_def.tz if company_def else 'America/Mexico_City')})
        emp.sudo().write({'resource_calendar_id': nuevo.id})
        return nuevo

    @api.model
    def horario_guardar(self, employee_id, dias):
        """Guarda el horario del empleado en SU calendario (clona si lo comparte).

        `dias` = [{iso, trabaja, entrada, salida, comida_inicio, comida_fin}].
        Un dia con comida se escribe como manana + comida + tarde (como el
        estandar de Odoo), para que `calendar_intervals` cuente el trabajo sin la
        comida y `lunch_intervals` reconozca la comida. Levanta UserError con un
        mensaje claro si un dia no cuadra: nada se guarda a medias."""
        emp = self.env['hr.employee'].browse(int(employee_id or 0)).exists()
        if not emp:
            raise UserError('No se encontró al empleado.')
        etq = dict(self.DIAS_SEMANA)
        lineas = []
        for d in (dias or []):
            if not d.get('trabaja'):
                continue
            try:
                iso = int(d.get('iso'))
            except (TypeError, ValueError):
                continue
            nombre_dia = etq.get(iso, '')
            e = self._hora_valida(d.get('entrada'))
            s = self._hora_valida(d.get('salida'))
            ci = self._hora_valida(d.get('comida_inicio'))
            cf = self._hora_valida(d.get('comida_fin'))
            if e is None or s is None or s <= e:
                raise UserError('En %s la salida debe ser mayor que la entrada.' % nombre_dia)
            dow = str(iso - 1)
            hay_comida = ci is not None and cf is not None
            if hay_comida:
                if not (e <= ci < cf <= s):
                    raise UserError('En %s la comida debe caer dentro del turno '
                                    '(entrada ≤ comida ≤ salida).' % nombre_dia)
                lineas += [
                    (0, 0, {'name': '%s mañana' % nombre_dia, 'dayofweek': dow,
                            'hour_from': e, 'hour_to': ci, 'day_period': 'morning'}),
                    (0, 0, {'name': 'Comida', 'dayofweek': dow,
                            'hour_from': ci, 'hour_to': cf, 'day_period': 'lunch'}),
                    (0, 0, {'name': '%s tarde' % nombre_dia, 'dayofweek': dow,
                            'hour_from': cf, 'hour_to': s, 'day_period': 'afternoon'}),
                ]
            else:
                periodo = 'afternoon' if e >= 13.0 else 'morning'
                lineas.append((0, 0, {
                    'name': nombre_dia, 'dayofweek': dow,
                    'hour_from': e, 'hour_to': s, 'day_period': periodo}))
        cal = self._calendario_propio(emp)
        cal.sudo().write({'two_weeks_calendar': False,
                          'attendance_ids': [(5, 0, 0)] + lineas})
        return self.horario_empleado(emp.id)

    # ---- purga ----------------------------------------------------------
    def _purgar_modelo(self, modelo, dominio, cupo):
        """Borra hasta `cupo` renglones y dice cuantos quedaron.

        Devuelve (borrados, pendientes). El pendiente es un conteo real, no una
        estimacion: es la diferencia entre lo que cumple el criterio y lo que
        se alcanzo a borrar.
        """
        Modelo = self.env[modelo].sudo()
        total = Modelo.search_count(dominio)
        if not total:
            return 0, 0
        registros = Modelo.search(dominio, limit=cupo)
        borrados = len(registros)
        # De mil en mil: un unlink de veinte mil de golpe arma una sentencia
        # que no le hace ningun favor a la base.
        for i in range(0, borrados, 1000):
            registros[i:i + 1000].unlink()
        return borrados, total - borrados

    @api.model
    def _cron_purgar(self):
        """Borra el detalle mas viejo que la retencion configurada.

        Con retention_months = 0 no borra nada y lo deja anotado en el registro:
        un proceso de borrado que corre en silencio, y del que no se sabe si
        hizo algo, es peor que no tenerlo.
        """
        ajustes = self.get_settings()

        # Las capturas se purgan SIEMPRE que tengan plazo, aunque la retencion
        # general este apagada. Son dos decisiones distintas: alguien puede
        # querer conservar el detalle de uso indefinidamente y aun asi no
        # querer guardar fotografias de la pantalla de su gente un ano.
        ahora = fields.Datetime.now()

        # 1) Monitoreo periodico: cada imagen trae SU fecha de caducidad
        #    (foco.watch.retention_days, por empleado y app), fijada al guardar.
        #    Con plazos distintos por regla, una sola consulta por `expires_at`
        #    las purga todas -no se puede con un corte global unico-.
        n, quedan = self._purgar_modelo(
            'foco.capture',
            [('trigger', '=', 'monitoreo'),
             ('expires_at', '!=', False), ('expires_at', '<', ahora)],
            PURGA_MAX_POR_CORRIDA)
        if n or quedan:
            _logger.info('Foco: capturas de monitoreo caducadas -> %d borradas, '
                         'quedan %d', n, quedan)

        # 2) Manual y "sin clasificar": por el plazo global de capturas.
        dias_img = ajustes.screenshot_retention_days or 0
        if dias_img > 0:
            corte_img = ahora - timedelta(days=dias_img)
            n, quedan = self._purgar_modelo(
                'foco.capture',
                [('trigger', 'in', ['manual', 'sin_clasificar']),
                 ('at', '<', corte_img)],
                PURGA_MAX_POR_CORRIDA)
            if n or quedan:
                _logger.info('Foco: capturas anteriores a %s -> %d borradas, '
                             'quedan %d', corte_img, n, quedan)

        meses = ajustes.retention_months or 0
        if meses <= 0:
            _logger.info('Foco: retencion apagada, no se purga nada')
            return 0

        corte = fields.Date.subtract(fields.Date.context_today(self), months=meses)
        corte_dt = datetime.combine(corte, time.min)

        # Todo lo que fecha a una persona entra a la purga, no solo el uso:
        # dejar fuera los eventos del equipo o la jornada convertiria la
        # promesa de "se conservan N meses" en una verdad a medias.
        objetivos = [
            ('foco.usage', [('date', '<', corte)]),
            ('foco.absence', [('start', '<', corte_dt)]),
            ('foco.event', [('at', '<', corte_dt)]),
            ('foco.workday', [('date', '<', corte)]),
            # Lado movil: la ubicacion es lo que mas crece, y fecha a una persona
            # igual que el resto, asi que entra a la misma retencion.
            ('foco.location', [('at', '<', corte_dt)]),
            ('foco.mobile.usage', [('date', '<', corte)]),
            ('foco.call', [('at', '<', corte_dt)]),
        ]
        cupo = PURGA_MAX_POR_CORRIDA
        borrados = pendientes = 0
        detalle = []
        for modelo, dominio in objetivos:
            if cupo > 0:
                n, quedan = self._purgar_modelo(modelo, dominio, cupo)
                cupo -= n
            else:
                n = 0
                quedan = self.env[modelo].sudo().search_count(dominio)
            borrados += n
            pendientes += quedan
            detalle.append('%s=%d' % (modelo, n))

        ajustes.sudo().write({
            'retention_last_run': fields.Datetime.now(),
            'retention_last_deleted': borrados,
            'retention_pending': pendientes,
        })
        _logger.info('Foco: purga anterior a %s -> %s, quedan %d',
                     corte, ', '.join(detalle), pendientes)
        return borrados

    @api.model
    def _presencia_por_dia(self, employee, ini, fin, tz):
        """Lo que dice el CHECADOR (hr.attendance): por dia local, los tramos
        en que la persona estuvo checada dentro de [ini, fin].

        Devuelve {fecha: [(desde, hasta), ...]} en la zona `tz`. Una asistencia
        sin salida (se le olvido checar) se toma como abierta hasta el fin del
        rango: ahi no hay con que afirmar que ya se fue. Un dia SIN ninguna
        asistencia no aparece en el dict: para ese dia manda el calendario,
        porque "no checo" no es lo mismo que "no estaba".
        """
        if not employee or 'hr.attendance' not in self.env:
            return {}
        try:
            regs = self.env['hr.attendance'].sudo().search_read(
                [('employee_id', '=', employee.id),
                 ('check_in', '<=', fin.astimezone(pytz.UTC).replace(tzinfo=None)),
                 '|', ('check_out', '=', False),
                 ('check_out', '>=', ini.astimezone(pytz.UTC).replace(tzinfo=None))],
                ['check_in', 'check_out'], order='check_in')
        except Exception:
            return {}
        por_dia = {}
        for r in regs:
            ci = pytz.UTC.localize(fields.Datetime.to_datetime(r['check_in'])).astimezone(tz)
            co = pytz.UTC.localize(fields.Datetime.to_datetime(r['check_out'])).astimezone(tz) \
                if r['check_out'] else fin
            por_dia.setdefault(ci.date(), []).append((ci, co))
        return por_dia

    @api.model
    def expected_seconds(self, employee, start_utc, stop_utc, con_asistencia=False):
        """Segundos de JORNADA ESPERADA dentro de [start_utc, stop_utc].

        Es lo que permite no molestar al empleado por huecos que caen fuera de
        su jornada (o en su comida): ahi el esperado es 0.

        `con_asistencia=True` (lo usa la ingesta de huecos, no el tablero):
        ademas del calendario, cuenta el checador. Un dia en que la persona
        checo, solo es esperado lo que cae ENTRE su entrada y su salida; asi,
        lo que pasa despues de checar salida (la computadora que se quedo
        prendida toda la noche) o antes de checar entrada no se pide justificar.
        Acordado con Francesco el 25-sep-2026: "cuando chequen salida, ya no
        hay que justificar". Un dia sin asistencia se sigue rigiendo por el
        calendario: no checar no prueba que no estuviera.
        """
        if stop_utc <= start_utc:
            return 0.0
        calendar = employee.resource_calendar_id if employee else None
        intervals = self.calendar_intervals(calendar)
        if not intervals:
            # Sin calendario utilizable no se puede afirmar que estuviera libre:
            # se asume esperado para que alguien lo revise.
            return (stop_utc - start_utc).total_seconds()
        tz = self._tzinfo_for(employee)
        ini = pytz.UTC.localize(start_utc).astimezone(tz)
        fin = pytz.UTC.localize(stop_utc).astimezone(tz)
        presencia = self._presencia_por_dia(employee, ini, fin, tz) if con_asistencia else {}
        total = 0.0
        day = ini.date()
        while day <= fin.date():
            base = tz.localize(datetime.combine(day, time(0, 0)))
            tramos = presencia.get(day)
            for h_from, h_to in intervals.get(day.isoweekday(), []):
                span_a = base + timedelta(hours=h_from)
                span_b = base + timedelta(hours=h_to)
                lo = max(ini, span_a)
                hi = min(fin, span_b)
                if hi <= lo:
                    continue
                if not tramos:
                    total += (hi - lo).total_seconds()
                    continue
                for ci, co in tramos:
                    a = max(lo, ci)
                    b = min(hi, co)
                    if b > a:
                        total += (b - a).total_seconds()
            day += timedelta(days=1)
        return total

    # ---- la columna Jornada del tablero: el checador manda, el horario guia --
    @api.model
    def _comida_por_iso(self, calendar):
        """La comida por dia ISO: la linea 'lunch' si existe, o el HUECO entre el
        bloque de la mañana y el de la tarde (asi la trae el Standard 40h)."""
        trabajo = self.calendar_intervals(calendar)
        comidas = self.lunch_intervals(calendar)
        out = {}
        for iso, spans in trabajo.items():
            spans = sorted(spans)
            if comidas.get(iso):
                out[iso] = (comidas[iso][0][0], comidas[iso][0][1])
            elif len(spans) >= 2 and spans[1][0] > spans[0][1]:
                out[iso] = (spans[0][1], spans[1][0])
        return out

    @api.model
    def _checador_jornada(self, empleados, d_ini, d_fin):
        """Jornada REAL del checador y EXTRA/faltante vs el horario (la guia), por
        empleado, en [d_ini, d_fin]. Decision del usuario (8-oct-2026): el horario
        es una guia; el CHECADOR tiene el veredicto final de la columna Jornada, y
        lo configurado dice cuanto fue EXTRA. Se compara en NETO (se descuenta la
        comida configurada, para no contar como extra la hora de comer de quien no
        la checa). Un dia PASADO sin cerrar salida NO cuenta: no hay con que medir
        (cero falsos positivos). El indice de productividad NO usa esto: sigue
        contra la guia."""
        base = {e.id: {'usa': False, 'checado': 0.0, 'extra': 0.0, 'faltante': 0.0}
                for e in empleados}
        if 'hr.attendance' not in self.env or not empleados:
            return base
        guia, comida, zonas, hoy = {}, {}, {}, {}
        for e in empleados:
            cal = e.resource_calendar_id
            tr = self.calendar_intervals(cal)
            guia[e.id] = {iso: round(sum(b - a for a, b in sp), 4) for iso, sp in tr.items()}
            comida[e.id] = self._comida_por_iso(cal)
            z = self._tzinfo_for(e)
            zonas[e.id] = z
            hoy[e.id] = datetime.now(z).date()
        lo = datetime.combine(d_ini - timedelta(days=1), time.min)
        hi = datetime.combine(d_fin + timedelta(days=1), time.max)
        try:
            regs = self.env['hr.attendance'].sudo().search_read(
                [('employee_id', 'in', [e.id for e in empleados]),
                 ('check_in', '>=', lo), ('check_in', '<=', hi)],
                ['employee_id', 'check_in', 'check_out'], order='check_in')
        except Exception:
            return base
        por = {}
        for r in regs:
            eid = r['employee_id'][0]
            z = zonas.get(eid)
            if not z:
                continue
            ci = pytz.UTC.localize(fields.Datetime.to_datetime(r['check_in'])).astimezone(z)
            dia = ci.date()
            if dia < d_ini or dia > d_fin:
                continue
            por.setdefault((eid, dia), []).append((ci, r['check_out']))
        for (eid, dia), pares in por.items():
            base[eid]['usa'] = True
            z = zonas[eid]
            iso = dia.isoweekday()
            tramos, abierto_pasado = [], False
            for ci, co_raw in pares:
                if not co_raw:
                    if dia < hoy[eid]:
                        abierto_pasado = True       # paso el dia y no cerro salida
                        continue
                    co = datetime.now(z)            # hoy: sigue checado
                else:
                    co = pytz.UTC.localize(
                        fields.Datetime.to_datetime(co_raw)).astimezone(z)
                a = ci.hour + ci.minute / 60.0 + ci.second / 3600.0
                b = 24.0 if co.date() > dia else co.hour + co.minute / 60.0 + co.second / 3600.0
                if b > a:
                    tramos.append((a, b))
            if abierto_pasado or not tramos:
                continue
            cw = comida[eid].get(iso)
            neto = 0.0
            for a, b in tramos:
                dur = b - a
                if cw:                               # descontar la comida configurada
                    dur -= max(0.0, min(b, cw[1]) - max(a, cw[0]))
                neto += max(0.0, dur)
            g = guia[eid].get(iso, 0.0)
            base[eid]['checado'] += neto
            base[eid]['extra'] += max(0.0, neto - g)
            base[eid]['faltante'] += max(0.0, g - neto)
        return base
