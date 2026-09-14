import secrets
from datetime import timedelta

from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    foco_token = fields.Char(
        string='Token de justificacion', copy=False, readonly=True,
        help='Token de la liga con la que el empleado justifica sus periodos '
             'sin actividad, sin necesidad de usuario de Odoo.')
    foco_token_expiry = fields.Datetime(
        string='Vence el', copy=False, readonly=True)

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
