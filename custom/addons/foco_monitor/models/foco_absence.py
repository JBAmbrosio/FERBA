import json
import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# --------------------------------------------------- revision por IA
# La IA lee la justificacion que escribio el empleado y decide si EXPLICA la
# ausencia o si es vaga y hay que revisarla con la persona. Automatica (cron),
# sin boton, con la llave de OpenAI que vive en Odoo. Nace de que la gente
# justifica con "Otro" y una nota de "." o basura para saltarse la regla de
# "Otro exige texto" (que solo mira que NO este vacio).
SISTEMA_JUST = (
    "Eres quien revisa las justificaciones de ausencia de los empleados de {contexto} "
    "Un empleado explica por que su computadora estuvo un rato sin actividad (se fue a "
    "comer, a una junta, al bano, una cita medica, un tramite). Te doy el motivo que "
    "eligio, el texto que escribio, cuanto duro y a que hora. Decide si la justificacion "
    "EXPLICA la ausencia o si es vaga y hay que revisarla con la persona. Reglas: "
    "(1) 'adecuada' si da una razon entendible, aunque sea breve ('bano', 'junta', 'fui al "
    "banco'); el motivo por si solo (Comida, Cita medica, Escuela, Permiso, Tramite) ya "
    "explica salvo que el texto lo contradiga. (2) 'vaga' si el texto no explica nada: "
    "vacio, un punto, 'x', 'asdf', 'otro', letras sueltas, o 'Otro' sin texto util. "
    "(3) 'sin_relacion' si el texto no parece una razon de ausencia de trabajo. "
    "(4) 'indeterminada' si no puedes decidir. 'requiere_revision' es true para 'vaga' y "
    "'sin_relacion'. 'motivo' de maximo 12 palabras, sin nombres de personas. Se tolerante: "
    "la meta es cazar las que no dicen nada, no castigar un texto corto pero real."
)
VEREDICTOS_JUST = [
    ('adecuada', 'Justificacion adecuada'),
    ('vaga', 'Vaga o sin explicacion'),
    ('sin_relacion', 'No parece razon de trabajo'),
    ('indeterminada', 'Indeterminada'),
]
ESQUEMA_JUST = {
    'name': 'revision_justificacion', 'strict': True,
    'schema': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'veredicto': {'type': 'string', 'enum': [v for v, _ in VEREDICTOS_JUST]},
            'requiere_revision': {'type': 'boolean'},
            'motivo': {'type': 'string'},
            'confianza': {'type': 'number'},
        },
        'required': ['veredicto', 'requiere_revision', 'motivo', 'confianza'],
    },
}
AI_MAX_INTENTOS = 4
AI_DIAS_ATRAS = 45

REASONS = [
    ('comida', 'Comida'),
    ('medico', 'Cita medica'),
    ('escuela', 'Escuela'),
    ('permiso', 'Permiso'),
    ('tramite', 'Tramite'),
    ('personal', 'Asunto personal'),
    ('otro', 'Otro'),
]

KINDS = [
    ('idle', 'Sin actividad'),
    ('locked', 'Equipo bloqueado'),
    ('offline', 'Equipo apagado'),
]

# Se le muestran al empleado como DATO NEUTRO que le ayuda a recordar,
# nunca como reclamo.
KIND_FRASE = {
    'idle': 'No hubo actividad en el equipo',
    'locked': 'El equipo estuvo bloqueado',
    'offline': 'El equipo estuvo apagado',
}

DIAS = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
         'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']

# Si dentro del hueco se esperaba menos que esto, el calendario ya lo explica.
MIN_EXPECTED_SECS = 300


class FocoAbsence(models.Model):
    _name = 'foco.absence'
    _description = 'Periodo sin actividad'
    _order = 'start desc'

    _periodo_uniq = models.Constraint(
        'unique(computer_id, start)',
        'Ese periodo ya estaba registrado para el equipo.')

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True,
        ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', string='Empleado',
        store=True, index=True)
    start = fields.Datetime(string='Inicio', required=True, index=True)
    stop = fields.Datetime(string='Fin', required=True)
    duration = fields.Float(
        string='Duracion (h)', compute='_compute_duration', store=True)
    kind = fields.Selection(KINDS, string='Tipo', default='idle')
    expected_seconds = fields.Float(
        string='Jornada esperada dentro del periodo (s)', readonly=True,
        help='Cuanto de este periodo caia dentro de la jornada del empleado. '
             'Si es casi cero, el calendario ya lo explica.')
    reason = fields.Selection(REASONS, string='Motivo')
    note = fields.Char(string='Nota')
    state = fields.Selection(
        [('auto', 'Explicado por calendario'),
         ('pendiente', 'Por justificar'),
         ('justificada', 'Justificada')],
        string='Estado', default='pendiente', index=True, required=True)
    notified = fields.Boolean(
        string='Avisado por correo', default=False, readonly=True,
        help='Se avisa UNA vez. Si el empleado no contesta, el periodo queda '
             'visible para RR.HH., pero no se le insiste todos los dias.')
    answered_at = fields.Datetime(string='Contestado', readonly=True)
    answered_via = fields.Selection(
        [('agente', 'Ventana del agente'),
         ('web', 'Liga por correo'),
         ('backend', 'Odoo')], string='Contestado desde', readonly=True)

    # ---- revision por IA de la justificacion (automatica, sin boton) --------
    ai_estado = fields.Selection(
        [('sin_analizar', 'Sin analizar'), ('analizado', 'Analizado'), ('error', 'Error')],
        string='Analisis IA', index=True,
        help='La IA lee la justificacion y decide si explica la ausencia o si es '
             'vaga. Corre sola en un proceso periodico, no hay boton.')
    ai_veredicto = fields.Selection(VEREDICTOS_JUST, string='Veredicto IA')
    ai_requiere_revision = fields.Boolean(
        string='A revisar', index=True,
        help='La IA la marco como vaga o sin relacion con el trabajo: alguien '
             'deberia revisarla con la persona.')
    ai_motivo = fields.Char(string='Que vio la IA')
    ai_confianza = fields.Float(string='Confianza IA', digits=(3, 2))
    ai_modelo = fields.Char(string='Modelo IA')
    ai_tokens = fields.Integer(string='Tokens IA')
    ai_at = fields.Datetime(string='Analizada el', readonly=True)
    ai_intentos = fields.Integer(string='Intentos IA', default=0)
    ai_error = fields.Char(string='Ultimo error IA')

    # ---- revision humana de lo que la IA marco ------------------------------
    review_visto = fields.Boolean(
        string='Revisado', index=True,
        help='Un administrador ya reviso esta justificacion marcada por la IA. '
             'Sale de la lista de "Periodos a revisar".')
    review_por = fields.Many2one('res.users', string='Revisado por', readonly=True)
    review_at = fields.Datetime(string='Revisado el', readonly=True)
    review_nota = fields.Char(string='Nota de la revision')

    @api.depends('start', 'stop')
    def _compute_duration(self):
        for rec in self:
            if rec.start and rec.stop and rec.stop > rec.start:
                rec.duration = (rec.stop - rec.start).total_seconds() / 3600.0
            else:
                rec.duration = 0.0

    # ------------------------------------------------------------- ingesta
    @api.model
    def _to_utc(self, value, employee):
        """El agente manda hora LOCAL sin zona; Odoo guarda UTC."""
        if not value:
            return False
        try:
            naive = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return False
        if naive.tzinfo is not None:
            return naive.astimezone(pytz.UTC).replace(tzinfo=None)
        tz = self.env['foco.settings']._tzinfo_for(employee)
        return tz.localize(naive).astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def _covered_by_leave(self, employee, start, stop):
        """Permisos aprobados. OJO: hr_holidays puede NO estar instalado."""
        if not employee or 'hr.leave' not in self.env:
            return False
        try:
            return bool(self.env['hr.leave'].sudo().search_count([
                ('employee_id', '=', employee.id),
                ('state', '=', 'validate'),
                ('date_from', '<=', stop),
                ('date_to', '>=', start),
            ]))
        except Exception:
            return False

    @api.model
    def record_gaps(self, computer, gaps):
        """Guarda los huecos que manda el agente. Idempotente por (equipo, inicio).

        Solo se marca 'pendiente' lo que el sistema NO puede explicar solo: si
        el hueco cae fuera de la jornada o en la comida, entra como 'auto' y
        nadie tiene que contestarlo. Sin esto, preguntariamos por la hora de
        comida todos los dias.
        """
        if not computer or not gaps:
            return 0
        settings = self.env['foco.settings'].sudo().get_settings()
        employee = computer.employee_id
        kinds = dict(KINDS)
        stored = 0
        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            start = self._to_utc(gap.get('start'), employee)
            stop = self._to_utc(gap.get('end'), employee)
            if not start or not stop or stop <= start:
                continue
            if self.sudo().search_count([('computer_id', '=', computer.id),
                                         ('start', '=', start)]):
                stored += 1                 # reenviado: ya estaba, no se duplica
                continue
            # Con el checador: lo que pasa despues de checar salida (o antes de
            # checar entrada) no cuenta como jornada esperada y no se pregunta.
            expected = settings.expected_seconds(employee, start, stop, con_asistencia=True)
            explicado = (expected < MIN_EXPECTED_SECS
                         or self._covered_by_leave(employee, start, stop))
            self.sudo().create({
                'computer_id': computer.id,
                'start': start,
                'stop': stop,
                'kind': gap.get('kind') if gap.get('kind') in kinds else 'idle',
                'expected_seconds': expected,
                'state': 'auto' if explicado else 'pendiente',
            })
            stored += 1
        return stored

    # ------------------------------------------------------------- consulta
    @api.model
    def pending_for_employee(self, employee, limit=20):
        if not employee:
            return self.browse()
        return self.sudo().search(
            [('employee_id', '=', employee.id), ('state', '=', 'pendiente')],
            order='start desc', limit=limit)

    def _suggested_reason(self):
        """Si el periodo traslapa la comida del calendario, se presugiere."""
        self.ensure_one()
        calendar = self.employee_id.resource_calendar_id
        lunch = self.env['foco.settings'].lunch_intervals(calendar)
        if not lunch or not self.start or not self.stop:
            return False
        tz = self.env['foco.settings']._tzinfo_for(self.employee_id)
        ini = pytz.UTC.localize(self.start).astimezone(tz)
        fin = pytz.UTC.localize(self.stop).astimezone(tz)
        for spans in (lunch.get(ini.isoweekday()) or []), (lunch.get(fin.isoweekday()) or []):
            for h_from, h_to in spans:
                a = ini.hour + ini.minute / 60.0
                b = fin.hour + fin.minute / 60.0 if fin.date() == ini.date() else 24.0
                if min(b, h_to) > max(a, h_from):
                    return 'comida'
        return False

    def payload(self):
        """Forma que consumen la ventana del agente y la pagina publica.

        Las etiquetas vienen ya armadas para que ni la ventana ni la pagina
        tengan que saber de formatos ni de zonas horarias.
        """
        tz_model = self.env['foco.settings']
        out = []
        for rec in self:
            tz = tz_model._tzinfo_for(rec.employee_id)
            ini = pytz.UTC.localize(rec.start).astimezone(tz)
            fin = pytz.UTC.localize(rec.stop).astimezone(tz)
            mins = int(round(rec.duration * 60))
            horas, resto = divmod(mins, 60)
            if horas and resto:
                dur = '%d h %d min' % (horas, resto)
            elif horas:
                dur = '%d h' % horas
            else:
                dur = '%d min' % resto
            out.append({
                'id': rec.id,
                'date': ini.strftime('%Y-%m-%d'),
                'start': ini.strftime('%H:%M'),
                'end': fin.strftime('%H:%M'),
                'minutes': mins,
                'dur_label': dur,
                'when_label': '%s %d de %s' % (
                    DIAS[ini.weekday()], ini.day, MESES[ini.month - 1]),
                'kind': rec.kind,
                'kind_label': KIND_FRASE.get(rec.kind, ''),
                'suggested': rec._suggested_reason() or '',
            })
        return out

    # ------------------------------------------------------------- tablero
    @api.model
    def dashboard_summary(self, date_from, date_to):
        """Por empleado: jornada esperada, justificado y pendientes del rango.

        Es lo que permite al tablero mostrar la descomposicion honesta
        (esperado - medido - justificado = sin explicar) en vez de un numero
        suelto que castiga a quien tuvo una cita medica.
        """
        Settings = self.env['foco.settings'].sudo()
        settings = Settings.get_settings()
        d_ini = fields.Date.to_date(date_from)
        d_fin = fields.Date.to_date(date_to)
        if not d_ini or not d_fin:
            return {}
        # Sin sudo: la lista de personas que devuelve este resumen tiene que
        # quedar acotada al alcance de quien pregunta.
        computers = self.env['foco.computer'].search(
            [('employee_id', '!=', False)])
        empleados = computers.mapped('employee_id')
        # Rezago checador -> PC del periodo, por persona: promedio de los dias
        # en que checo entrada y hubo senal en la computadora. Una sola lectura
        # para todos, agrupada en Python.
        lags = {}
        for w in self.env['foco.workday'].sudo().search([
                ('employee_id', 'in', empleados.ids),
                ('date', '>=', d_ini), ('date', '<=', d_fin),
                ('shift_source', '=', 'checador'),
                ('first_signal', '!=', False)]):
            lags.setdefault(w.employee_id.id, []).append(w.check_in_lag_minutes)
        out = {}
        for employee in empleados:
            tz = Settings._tzinfo_for(employee)
            ini = tz.localize(datetime.combine(d_ini, time(0, 0))) \
                    .astimezone(pytz.UTC).replace(tzinfo=None)
            fin = tz.localize(datetime.combine(d_fin, time(23, 59, 59))) \
                    .astimezone(pytz.UTC).replace(tzinfo=None)
            recs = self.search([('employee_id', '=', employee.id),
                                ('start', '>=', ini), ('start', '<=', fin)])
            v = lags.get(employee.id) or []
            # Lo JUSTIFICADO y lo SIN EXPLICAR cuentan solo la porcion que cae
            # DENTRO de la jornada (expected_seconds), no la duracion cruda.
            # Medido el 8-oct: una ausencia "bloqueado" de 8.45 h justificada de
            # madrugada (00:22-08:49) tenia solo 0.48 h dentro de la jornada;
            # sumar su duracion completa inflaba lo cubierto a 8h53 con 31 min
            # activos y daba 100%. Una comida justificada (dentro de jornada) si
            # cuenta entera; una noche bloqueada justificada, casi nada.
            justi = recs.filtered(lambda a: a.state == 'justificada')
            pend = recs.filtered(lambda a: a.state == 'pendiente')
            out[str(employee.id)] = {
                'expected': settings.expected_seconds(employee, ini, fin) / 3600.0,
                'justified': sum(justi.mapped('expected_seconds')) / 3600.0,
                'pending': len(pend),
                # "Sin explicar" en horas: porcion de la jornada que cubren los
                # huecos SIN justificar. Antes el tablero lo derivaba restando
                # (esperado - activo - justificado), que a media jornada daba un
                # numero enorme porque el esperado es el dia completo.
                'unexplained_h': sum(pend.mapped('expected_seconds')) / 3600.0,
                # Minutos del checador a la PC: promedio de los dias checados.
                # None cuando no hay ningun dia con checada: el tablero no lo
                # dibuja, en vez de pintar un 0 que afirmaria "llego al instante".
                'lag_min': int(round(sum(v) / len(v))) if v else None,
            }
        return out

    # ------------------------------------------------------------- correo
    @api.model
    def _cron_digest(self):
        """Un correo por empleado con lo que SIGUE pendiente.

        Es la RED DE SEGURIDAD del doble canal: lo que la ventana del agente ya
        recogio no llega aqui, porque solo se buscan las que siguen en
        'pendiente'. Y se avisa UNA sola vez por periodo, para no insistir.
        """
        pendientes = self.sudo().search([('state', '=', 'pendiente'),
                                         ('notified', '=', False)])
        if not pendientes:
            return
        tpl = self.env.ref('foco_monitor.mail_tpl_absences',
                           raise_if_not_found=False)
        if not tpl:
            return
        enviados = 0
        for employee in pendientes.mapped('employee_id'):
            if not employee or not employee.work_email:
                continue
            try:
                tpl.sudo().send_mail(employee.id, force_send=False)
                pendientes.filtered(
                    lambda a, e=employee: a.employee_id == e).notified = True
                enviados += 1
            except Exception:
                _logger.exception(
                    "no se pudo encolar el aviso de ausencias de %s", employee.name)
        if enviados:
            _logger.info("Foco: avisos de periodos sin actividad encolados: %s",
                         enviados)

    # ------------------------------------------------------------- respuesta
    def answer(self, reason, note=None, via='web'):
        """Registra el motivo. GANA EL PRIMERO: si ya fue contestada no se pisa.

        Es lo que permite tener dos canales (ventana del agente y liga por
        correo) sin que se estorben.
        """
        self.ensure_one()
        if reason not in dict(REASONS):
            return {'ok': False, 'error': 'motivo_invalido'}
        note = (note or '').strip()[:200]
        # «Otro» sin texto no explica nada: es un periodo que sigue sin
        # justificar con otro nombre. La regla vive AQUI y no solo en la
        # ventana y en la pagina: los dos canales la muestran, pero es el
        # servidor el que no la deja pasar (decision del cliente, 25-sep).
        if reason == 'otro' and not note:
            return {'ok': False, 'error': 'nota_requerida'}
        if self.state == 'justificada':
            return {'ok': False, 'error': 'ya_justificada',
                    'reason': self.reason,
                    'reason_label': dict(REASONS).get(self.reason, '')}
        self.sudo().write({
            'reason': reason,
            'note': note or False,
            'state': 'justificada',
            'answered_at': fields.Datetime.now(),
            'answered_via': via,
            # Recien justificada: la IA la analiza en su proximo ciclo. Nota de
            # "." o basura con motivo "Otro" es justo lo que viene a cazar.
            'ai_estado': 'sin_analizar', 'ai_intentos': 0,
        })
        return {'ok': True, 'reason_label': dict(REASONS).get(reason, '')}

    def write(self, vals):
        # Si cambia el texto o el motivo de una justificacion ya analizada,
        # la IA la vuelve a mirar: un administrador pudo corregir la nota. Se
        # evita la recursion saltando cuando el propio write es de campos IA.
        reanaliza = (('note' in vals or 'reason' in vals)
                     and 'ai_estado' not in vals and 'ai_veredicto' not in vals)
        res = super().write(vals)
        if reanaliza:
            for rec in self:
                if rec.state == 'justificada' and rec.ai_estado == 'analizado':
                    super(FocoAbsence, rec).write({'ai_estado': 'sin_analizar', 'ai_intentos': 0})
        return res

    # ------------------------------------------------- revision por IA (cron)
    @api.model
    def _cron_revisar_ia(self, limit=40):
        """Analiza las justificaciones que faltan: nuevas y las que fallaron.

        Automatico, sin boton. Solo justificadas y recientes (ultimos dias):
        las viejas no mueven ninguna decision. Cada una va en su savepoint para
        que un fallo no tumbe al resto."""
        OpenAI = self.env['foco.openai']
        if not OpenAI.configurado():
            return 0
        settings = self.env['foco.settings'].sudo().get_settings()
        desde = fields.Datetime.now() - timedelta(days=AI_DIAS_ATRAS)
        recs = self.sudo().search([
            ('state', '=', 'justificada'),
            ('ai_estado', '!=', 'analizado'),
            ('ai_intentos', '<', AI_MAX_INTENTOS),
            ('start', '>=', desde),
        ], order='start desc', limit=limit)
        contexto = (settings.call_review_context or '').strip()
        n = 0
        for rec in recs:
            try:
                with self.env.cr.savepoint():
                    rec._clasificar_ia(contexto)
                    n += 1
            except Exception as e:
                rec.sudo().write({'ai_estado': 'error', 'ai_intentos': rec.ai_intentos + 1,
                                  'ai_error': str(e)[:200]})
                _logger.warning('foco.absence %s: no se pudo analizar (%s)', rec.id, e)
        return n

    def _clasificar_ia(self, contexto):
        """Una justificacion: le pide a la IA el veredicto y lo guarda."""
        self.ensure_one()
        tz = self.env['foco.settings'].sudo()._tzinfo_for(self.employee_id)
        ini = pytz.UTC.localize(self.start).astimezone(tz) if self.start else None
        mins = int(round((self.duration or 0.0) * 60))
        dur = ('%d h %d min' % (mins // 60, mins % 60)) if mins >= 60 else ('%d min' % mins)
        ctx = (contexto or '').strip()
        if ctx and not ctx.endswith('.'):
            ctx += '.'
        sistema = SISTEMA_JUST.format(contexto=ctx or 'la empresa.')
        usuario = (
            'MOTIVO ELEGIDO: %s\nTEXTO QUE ESCRIBIO: %s\nDURACION: %s\nHORA LOCAL: %s\nTIPO: %s'
            % (dict(REASONS).get(self.reason, self.reason or '(ninguno)'),
               (self.note or '').strip() or '(vacio)', dur,
               ini.strftime('%H:%M') if ini else '?',
               dict(KINDS).get(self.kind, self.kind or '')))
        resp = self.env['foco.openai'].chat(
            [{'role': 'system', 'content': sistema},
             {'role': 'user', 'content': usuario}],
            response_format={'type': 'json_schema', 'json_schema': ESQUEMA_JUST},
            max_tokens=200)
        try:
            j = json.loads(resp['message'].get('content') or '{}')
        except (ValueError, TypeError):
            j = {}
        ver = j.get('veredicto') if j.get('veredicto') in dict(VEREDICTOS_JUST) else 'indeterminada'
        # El flag se fija por el veredicto, no por el booleano suelto del modelo:
        # 'vaga' y 'sin_relacion' SIEMPRE son a revisar, pase lo que pase.
        requiere = ver in ('vaga', 'sin_relacion')
        try:
            conf = min(max(float(j.get('confianza') or 0.0), 0.0), 1.0)
        except (TypeError, ValueError):
            conf = 0.0
        self.sudo().write({
            'ai_estado': 'analizado', 'ai_veredicto': ver,
            'ai_requiere_revision': requiere,
            'ai_motivo': (j.get('motivo') or '').strip()[:150] or False,
            'ai_confianza': conf, 'ai_modelo': resp.get('modelo') or False,
            'ai_tokens': int((resp.get('usage') or {}).get('total_tokens') or 0),
            'ai_at': fields.Datetime.now(), 'ai_error': False,
        })

    # ------------------------------------------------- revision humana
    def action_marcar_revisado(self):
        self.write({'review_visto': True, 'review_por': self.env.user.id,
                    'review_at': fields.Datetime.now()})
        return True

    def action_reabrir_revision(self):
        self.write({'review_visto': False, 'review_por': False, 'review_at': False})
        return True

    @api.model
    def revisar_pendientes(self):
        """Cuantas justificaciones marco la IA que siguen sin revisar. Para el
        KPI del tablero; respeta el alcance de quien pregunta (sin sudo)."""
        return self.search_count([('ai_requiere_revision', '=', True),
                                  ('review_visto', '=', False)])
