import logging
from datetime import datetime, time

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

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
        tz = pytz.timezone(self.env['foco.settings']._tz_for(employee))
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
            expected = settings.expected_seconds(employee, start, stop)
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
        tz = pytz.timezone(self.env['foco.settings']._tz_for(self.employee_id))
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
            tz = pytz.timezone(tz_model._tz_for(rec.employee_id))
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
        computers = self.env['foco.computer'].sudo().search(
            [('employee_id', '!=', False)])
        out = {}
        for employee in computers.mapped('employee_id'):
            tz = pytz.timezone(Settings._tz_for(employee))
            ini = tz.localize(datetime.combine(d_ini, time(0, 0))) \
                    .astimezone(pytz.UTC).replace(tzinfo=None)
            fin = tz.localize(datetime.combine(d_fin, time(23, 59, 59))) \
                    .astimezone(pytz.UTC).replace(tzinfo=None)
            recs = self.sudo().search([('employee_id', '=', employee.id),
                                       ('start', '>=', ini), ('start', '<=', fin)])
            out[str(employee.id)] = {
                'expected': settings.expected_seconds(employee, ini, fin) / 3600.0,
                'justified': sum(recs.filtered(
                    lambda a: a.state == 'justificada').mapped('duration')),
                'pending': len(recs.filtered(lambda a: a.state == 'pendiente')),
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
        if self.state == 'justificada':
            return {'ok': False, 'error': 'ya_justificada',
                    'reason': self.reason,
                    'reason_label': dict(REASONS).get(self.reason, '')}
        self.sudo().write({
            'reason': reason,
            'note': (note or '')[:200] or False,
            'state': 'justificada',
            'answered_at': fields.Datetime.now(),
            'answered_via': via,
        })
        return {'ok': True, 'reason_label': dict(REASONS).get(reason, '')}
