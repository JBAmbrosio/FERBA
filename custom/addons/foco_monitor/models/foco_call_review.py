import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

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
                         'transcript_cleared': True})
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
