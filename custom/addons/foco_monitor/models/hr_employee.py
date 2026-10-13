import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # La ventana de inactividad se decide POR EQUIPO (foco.computer.
    # foco_ventana_inactividad), no por persona: solo los equipos tienen Foco.
    # Ver foco.settings.ventana_computer_ids y foco.policy.conducta_para.

    foco_token = fields.Char(
        string='Token de justificacion', copy=False, readonly=True,
        help='Token de la liga con la que el empleado justifica sus periodos '
             'sin actividad, sin necesidad de usuario de Odoo.')
    foco_token_expiry = fields.Datetime(
        string='Vence el', copy=False, readonly=True)

    # Tolerancia de entrada POR PERSONA (10-oct-2026). 0 = la global de Foco
    # (Configuracion > Asistencia y reporte diario). Sirve para la excepcion
    # acordada con alguien, no para ablandar la regla a todos.
    foco_gracia_min = fields.Integer(
        string='Tolerancia de entrada (min)', default=0,
        help='Minutos de tolerancia para marcar "tarde" a esta persona. 0 usa '
             'la tolerancia global de Foco. Entre 0 y 120.')

    @api.constrains('foco_gracia_min')
    def _check_foco_gracia(self):
        for r in self:
            if r.foco_gracia_min < 0 or r.foco_gracia_min > 120:
                raise ValidationError('La tolerancia de entrada va de 0 a 120 minutos.')

    def foco_justify_url(self, days=7):
        """Liga personal para justificar. Se renueva si caduco."""
        self.ensure_one()
        now = fields.Datetime.now()
        if (not self.foco_token or not self.foco_token_expiry
                or self.foco_token_expiry < now):
            self.sudo().write({
                'foco_token': secrets.token_urlsafe(24),
                'foco_token_expiry': now + timedelta(days=days),
            })
        return '%s/foco/justificar/%s' % (self.get_base_url(), self.foco_token)

    def foco_pending_absences(self):
        """Periodos que este empleado tiene por completar. Lo usa el correo."""
        self.ensure_one()
        return self.env['foco.absence'].sudo().pending_for_employee(self).payload()

    def foco_dias_sin_salida(self):
        """Dias en que no cerro salida y aun no se le ha avisado. Lo usa el
        correo de recordatorio. Devuelve ['lunes 6 de octubre', ...]."""
        self.ensure_one()
        dias_es = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
        meses_es = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
                    'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
        wds = self.env['foco.workday'].sudo().search(
            [('employee_id', '=', self.id), ('check_out_missing', '=', True),
             ('check_out_notified', '=', False)], order='date')
        return ['%s %d de %s' % (dias_es[d.weekday()], d.day, meses_es[d.month - 1])
                for d in wds.mapped('date')]

    @api.model
    def _foco_by_token(self, token):
        """Resuelve el token. Devuelve vacio si no existe o si ya caduco."""
        if not token:
            return self.browse()
        employee = self.sudo().search([('foco_token', '=', token)], limit=1)
        if not employee or not employee.foco_token_expiry:
            return self.browse()
        if employee.foco_token_expiry < fields.Datetime.now():
            return self.browse()
        return employee
