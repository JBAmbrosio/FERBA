import json
import logging
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Intercala las DOS pistas (empleado / interlocutor) en el orden natural de la
# charla para leerla como conversacion. Aqui NO se infiere quien hablo (ya viene
# separado por pista): solo se ORDENA en turnos.
SISTEMA_LLAMADA_DIALOGO = (
    "Recibes la transcripcion de una LLAMADA en DOS pistas ya separadas: lo que "
    "dijo el EMPLEADO (su microfono) y lo que dijo el INTERLOCUTOR (la otra "
    "persona). Reconstruye la CONVERSACION intercalando los turnos en el orden "
    "natural en que ocurrieron (una pregunta y su respuesta van juntas). NO "
    "inventes ni cambies palabras: usa SOLO el texto de cada pista, repartido en "
    "turnos y atribuido a quien lo dijo. Si una pista viene vacia, usa solo la otra."
)

ESQUEMA_LLAMADA_DIALOGO = {
    'name': 'dialogo_llamada', 'strict': True,
    'schema': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'turnos': {
                'type': 'array',
                'items': {
                    'type': 'object', 'additionalProperties': False,
                    'properties': {
                        'rol': {'type': 'string', 'enum': ['empleado', 'otro']},
                        'texto': {'type': 'string'},
                    },
                    'required': ['rol', 'texto'],
                },
            },
        },
        'required': ['turnos'],
    },
}

CLASIFICACIONES = [
    ('trabajo', 'Trabajo'),
    ('personal', 'Personal'),
    ('mixta', 'Mixta'),
    ('indeterminada', 'Indeterminada'),
]
CON_QUIEN = [
    ('cliente', 'Cliente'),
    ('proveedor', 'Proveedor'),
    ('colega', 'Colega'),
    ('familiar_o_amigo', 'Familiar o amistad'),
    ('otro', 'Otro'),
    ('desconocido', 'Desconocido'),
]
ESTADOS = [
    ('grabando', 'En llamada'),
    ('transcribiendo', 'Transcribiendo'),
    ('por_clasificar', 'Por clasificar'),
    ('clasificada', 'Clasificada'),
    ('sin_audio', 'Sin audio'),
    ('error', 'Error'),
]
# Una llamada que no se cerro en este plazo se da por perdida (el agente murio
# o el equipo se apago a media llamada): se clasifica con lo que haya.
HORAS_PARA_DARLA_POR_PERDIDA = 6
# Si la clasificacion falla, se reintenta cada 10 min hasta este plazo; despues
# queda «indeterminada» y la transcripcion se borra igual.
HORAS_MAX_REINTENTO = 24


class FocoCallReview(models.Model):
    """Una llamada de WhatsApp hecha desde la laptop, ya analizada.

    QUE SE GUARDA: cuando fue, cuanto duro, con que app, y el veredicto del
    modelo (trabajo / personal / mixta / indeterminada), su confianza, el ROL
    del interlocutor y un motivo de hasta 12 palabras. Para las personales el
    motivo es siempre «asunto personal».

    QUE NO SE GUARDA: el audio nunca toca la base -se transcribe en la misma
    peticion en que llega y se descarta-. La transcripcion, de fabrica, vive en
    este registro solo mientras la llamada sigue y hasta que el modelo decide, y
    en ese momento se borra (`transcript_cleared`); el administrador ve etiqueta,
    duracion y motivo, nunca lo que se dijo.

    RETENCION OPCIONAL: si Ajustes enciende `call_review_keep_transcript` -algo
    que depende del aviso de privacidad firmado-, la transcripcion se CONSERVA
    para poder validar que la IA clasifica bien. Solo la ven quienes administran
    Foco (los campos `transcript_*` estan restringidos a `group_foco_manager`).
    """
    _name = 'foco.call.review'
    _description = 'Llamada de WhatsApp analizada (laptop)'
    _order = 'started_at desc, id desc'

    _uniq = models.Constraint('unique(computer_id, ref)', 'Esa llamada ya estaba registrada.')

    computer_id = fields.Many2one('foco.computer', string='Equipo', required=True,
                                  ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='computer_id.employee_id', store=True,
                                  string='Empleado', index=True)
    department_id = fields.Many2one(related='employee_id.department_id', store=True,
                                    string='Departamento')
    ref = fields.Char(string='Referencia', required=True, index=True,
                      help='La que le puso el agente al empezar a grabar. Es la llave '
                           'con la que el uso de ese rato se liga a la llamada.')
    app = fields.Char(string='App', help='Que programa tenia el microfono.')
    started_at = fields.Datetime(string='Inicio', required=True, index=True)
    ended_at = fields.Datetime(string='Fin')
    duration_seconds = fields.Integer(string='Duracion (s)')
    duration_text = fields.Char(string='Duracion', compute='_compute_dur')
    state = fields.Selection(ESTADOS, string='Estado', default='grabando', index=True)

    clasificacion = fields.Selection(CLASIFICACIONES, string='Veredicto', index=True)
    confianza = fields.Float(string='Confianza', digits=(3, 2))
    con_quien = fields.Selection(CON_QUIEN, string='Con quien (rol)')
    motivo = fields.Char(string='Motivo', help='Maximo 12 palabras. En las personales: «asunto personal».')
    decided_at = fields.Datetime(string='Clasificada el')

    chunks_received = fields.Integer(string='Trozos recibidos')
    audio_seconds = fields.Integer(string='Audio transcrito (s)')
    transcript_empleado = fields.Text(string='Transcripcion (empleado)',
                                      groups='foco_monitor.group_foco_manager')
    transcript_otro = fields.Text(string='Transcripcion (interlocutor)',
                                  groups='foco_monitor.group_foco_manager')
    # Conversacion intercalada (JSON [{rol, texto}]) y su render en burbujas, para
    # leer la llamada como un chat. Solo existe si se conserva la transcripcion.
    transcript_dialogo = fields.Text(string='Conversacion (JSON)',
                                     groups='foco_monitor.group_foco_manager')
    dialogo_html = fields.Html(string='Conversacion', compute='_compute_dialogo_html',
                               sanitize=False, groups='foco_monitor.group_foco_manager')
    transcript_chars = fields.Integer(string='Caracteres transcritos')
    transcript_cleared = fields.Boolean(string='Transcripcion borrada', default=False)
    tokens = fields.Integer(string='Tokens del modelo')
    modelo = fields.Char(string='Modelo')
    retries = fields.Integer(string='Reintentos', default=0)
    error_detail = fields.Char(string='Ultimo error')

    @api.depends('duration_seconds')
    def _compute_dur(self):
        for rec in self:
            s = rec.duration_seconds or 0
            rec.duration_text = '%d:%02d' % (s // 60, s % 60)

    def action_rehacer_dialogo(self):
        """Boton de la ficha: (re)arma la conversacion en burbujas desde las dos
        pistas conservadas. Util para llamadas ya guardadas sin dialogo."""
        for rec in self:
            if rec.transcript_empleado or rec.transcript_otro:
                try:
                    rec._generar_dialogo()
                except Exception as e:
                    _logger.warning('foco.call.review %s: no se armo el dialogo (%s)', rec.ref, e)

    def _generar_dialogo(self):
        """Intercala las dos pistas (empleado/interlocutor) en turnos para leer la
        llamada como conversacion. Solo con la transcripcion conservada; su fallo
        no afecta la clasificacion."""
        self.ensure_one()
        emp = (self.transcript_empleado or '').strip()
        otro = (self.transcript_otro or '').strip()
        if not (emp or otro):
            return
        entrada = 'EMPLEADO:\n%s\n\nINTERLOCUTOR:\n%s' % (emp[:9000], otro[:9000])
        resp = self.env['foco.openai'].chat(
            [{'role': 'system', 'content': SISTEMA_LLAMADA_DIALOGO},
             {'role': 'user', 'content': entrada}],
            response_format={'type': 'json_schema', 'json_schema': ESQUEMA_LLAMADA_DIALOGO},
            max_tokens=2500)
        turnos = json.loads(resp['message'].get('content') or '{}').get('turnos') or []
        self.transcript_dialogo = json.dumps(turnos, ensure_ascii=False)

    @api.depends('transcript_dialogo')
    def _compute_dialogo_html(self):
        """Burbujas tipo chat: el EMPLEADO a la derecha (verde), el INTERLOCUTOR a
        la izquierda (blanco)."""
        for rec in self:
            try:
                turnos = json.loads(rec.transcript_dialogo or '[]')
            except Exception:
                turnos = []
            if not turnos:
                rec.dialogo_html = False
                continue
            quien_emp = rec.employee_id.name or 'Empleado'
            filas = []
            for t in turnos:
                texto = (t.get('texto') or '').strip()
                if not texto:
                    continue
                es_emp = (t.get('rol') == 'empleado')
                align = 'flex-end' if es_emp else 'flex-start'
                bg = '#d9fdd3' if es_emp else '#ffffff'
                etq_color = '#1a8a5a' if es_emp else '#8a6d1a'
                quien = quien_emp if es_emp else 'Interlocutor'
                filas.append(
                    '<div style="display:flex;justify-content:%s;margin:3px 0;">'
                    '<div style="max-width:78%%;min-width:0;background:%s;border:1px solid #e4e4e4;'
                    'border-radius:12px;padding:7px 11px;box-shadow:0 1px 1px rgba(0,0,0,.08);">'
                    '<div style="font-size:11px;font-weight:600;color:%s;margin-bottom:2px;">%s</div>'
                    '<div style="white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;'
                    'color:#111;line-height:1.35;">%s</div>'
                    '</div></div>' % (align, bg, etq_color, escape(quien), escape(texto)))
            # Ancho completo (width:100%% + box-sizing) para que las burbujas usen
            # todo el sheet; scroll propio solo cuando la charla es muy larga.
            rec.dialogo_html = Markup(
                '<div style="width:100%%;box-sizing:border-box;background:#efeae2;'
                'padding:12px;border-radius:10px;max-height:560px;overflow-y:auto;">%s</div>'
                % ''.join(filas))

    # ------------------------------------------------------------ pesos
    def weight(self):
        """Peso de productividad del rato de esta llamada, o None si el modelo
        no decidio (entonces manda la app que estaba al frente)."""
        self.ensure_one()
        if self.clasificacion == 'trabajo':
            return 1.0
        if self.clasificacion == 'personal':
            return 0.0
        return None

    # ------------------------------------------------------------ ingesta
    @api.model
    def _buscar(self, computer, ref):
        return self.search([('computer_id', '=', computer.id), ('ref', '=', ref)], limit=1)

    @api.model
    def _start(self, computer, ref, app, started_at):
        rec = self._buscar(computer, ref)
        if rec:
            return rec
        rec = self.create({
            'computer_id': computer.id, 'ref': ref, 'app': (app or '')[:120],
            'started_at': started_at or fields.Datetime.now(), 'state': 'grabando',
        })
        # El uso de ese rato pudo llegar antes que la llamada (los envios van
        # cada 5 min): se liga lo que ya haya con esta referencia.
        self.env['foco.usage'].sudo().search([
            ('computer_id', '=', computer.id), ('call_ref', '=', ref),
            ('call_id', '=', False)]).write({'call_id': rec.id})
        return rec

    @api.model
    def _chunk(self, computer, ref, track, seq, secs, wav_bytes):
        """Transcribe un trozo y lo suma a su pista. El audio no se guarda:
        vive en esta peticion y se va con ella. Levanta si la API falla, para
        que el agente conserve el trozo y lo reintente."""
        rec = self._buscar(computer, ref)
        if not rec:
            rec = self._start(computer, ref, '', fields.Datetime.now())
        texto = self.env['foco.openai'].transcribir(
            wav_bytes, nombre='%s_%s_%03d.wav' % (ref, track, int(seq or 0)))
        campo = 'transcript_otro' if track == 'otro' else 'transcript_empleado'
        previo = rec[campo] or ''
        vals = {
            campo: (previo + '\n' + texto).strip() if texto else previo,
            'chunks_received': rec.chunks_received + 1,
            'audio_seconds': rec.audio_seconds + int(secs or 0),
            'transcript_chars': rec.transcript_chars + len(texto),
        }
        if rec.state == 'grabando':
            vals['state'] = 'transcribiendo'
        rec.write(vals)
        return rec, len(texto)

    @api.model
    def _finish(self, computer, ref, ended_at, duration, chunks_esperados=None):
        rec = self._buscar(computer, ref)
        if not rec:
            rec = self._start(computer, ref, '', ended_at or fields.Datetime.now())
        vals = {'ended_at': ended_at or fields.Datetime.now(),
                'duration_seconds': int(duration or 0)}
        if chunks_esperados is not None and rec.chunks_received < int(chunks_esperados):
            # El agente manda «fin» solo cuando ya subio todos los trozos; si
            # aun asi faltan, se clasifica con lo que hay pero se deja dicho.
            vals['error_detail'] = 'llegaron %d de %d trozos' % (rec.chunks_received, int(chunks_esperados))
        rec.write(vals)
        rec._clasificar()
        return rec

    def _clasificar(self):
        settings = self.env['foco.settings'].sudo().get_settings()
        for rec in self:
            if rec.state == 'clasificada':
                continue
            if not (rec.transcript_empleado or rec.transcript_otro):
                rec._cerrar('indeterminada', 0.0, 'desconocido', '', 'sin_audio')
                continue
            try:
                res = self.env['foco.openai'].clasificar(
                    settings.call_review_context, rec.transcript_empleado,
                    rec.transcript_otro, rec.duration_seconds)
            except Exception as e:
                rec.write({'state': 'error', 'retries': rec.retries + 1,
                           'error_detail': str(e)[:240]})
                _logger.warning('foco.call.review %s: no se pudo clasificar (%s)', rec.ref, e)
                continue
            motivo = (res.get('motivo') or '').strip()
            if res.get('clasificacion') in ('personal', 'mixta'):
                motivo = 'asunto personal'
            elif not settings.call_review_keep_reason:
                motivo = ''
            # Si se conserva la transcripcion, arma la conversacion en burbujas
            # ANTES de cerrar (en el cierre se borraria si no se conserva).
            if settings.call_review_keep_transcript:
                try:
                    rec._generar_dialogo()
                except Exception as e:
                    _logger.warning('foco.call.review %s: no se armo el dialogo (%s)', rec.ref, e)
            rec._cerrar(res.get('clasificacion') or 'indeterminada',
                        float(res.get('confianza') or 0.0),
                        res.get('con_quien') or 'desconocido', motivo[:120], 'clasificada',
                        tokens=res.get('tokens'), modelo=res.get('modelo'))

    def _cerrar(self, clasificacion, confianza, con_quien, motivo, estado, tokens=0, modelo=''):
        """Guarda el veredicto. BORRA la transcripcion en el mismo write, salvo
        que Ajustes pida conservarla (call_review_keep_transcript), decision que
        depende del aviso de privacidad firmado."""
        guardar = self.env['foco.settings'].sudo().get_settings().call_review_keep_transcript
        vals = {
            'clasificacion': clasificacion, 'confianza': confianza,
            'con_quien': con_quien, 'motivo': motivo or False,
            'state': estado, 'decided_at': fields.Datetime.now(),
            'tokens': int(tokens or 0), 'modelo': modelo or False,
        }
        if not guardar:
            vals.update({'transcript_empleado': False, 'transcript_otro': False,
                         'transcript_dialogo': False, 'transcript_cleared': True})
        self.write(vals)

    # ------------------------------------------------------------ cron
    @api.model
    def _cron_pendientes(self):
        """Reintenta lo que fallo y cierra lo que se quedo abierto."""
        ahora = fields.Datetime.now()
        # Fallos de la API: reintento; pasado el plazo, se cierra sin veredicto
        # y la transcripcion se borra igual (no se guarda contenido por un fallo).
        for rec in self.search([('state', 'in', ('error', 'por_clasificar'))]):
            limite = (rec.ended_at or rec.started_at) + timedelta(hours=HORAS_MAX_REINTENTO)
            if ahora > limite:
                rec._cerrar('indeterminada', 0.0, 'desconocido', '', 'clasificada')
            else:
                rec._clasificar()
        # Llamadas que nunca cerraron (agente muerto, equipo apagado a media
        # llamada): se cierran con lo que llego.
        tope = ahora - timedelta(hours=HORAS_PARA_DARLA_POR_PERDIDA)
        for rec in self.search([('state', 'in', ('grabando', 'transcribiendo')),
                                ('started_at', '<', tope)]):
            rec.write({'ended_at': rec.ended_at or ahora,
                       'error_detail': 'la llamada no se cerro; se clasifico con lo recibido'})
            rec._clasificar()
