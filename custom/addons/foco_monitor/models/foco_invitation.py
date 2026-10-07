import base64
import secrets
from datetime import timedelta
from urllib.parse import quote

from odoo import api, fields, models
from odoo.exceptions import UserError

_ALPH = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _code():
    raw = "".join(secrets.choice(_ALPH) for _ in range(12))
    return "%s-%s-%s" % (raw[0:4], raw[4:8], raw[8:12])


class FocoInvitation(models.Model):
    _name = 'foco.invitation'
    _description = 'Invitacion de enrolamiento Foco'
    _order = 'create_date desc'
    _rec_name = 'employee_id'

    _token_uniq = models.Constraint('unique(token)', 'El token debe ser unico.')

    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True, ondelete='cascade')
    email = fields.Char(related='employee_id.work_email', string='Correo', store=True)
    token = fields.Char(string='Codigo', required=True, index=True, copy=False,
                        default=lambda self: _code(), readonly=True)
    state = fields.Selection([
        ('draft', 'Borrador'), ('sent', 'Enviada'),
        ('enrolled', 'Enrolada'), ('expired', 'Caducada')],
        default='draft', index=True, string='Estado')
    expiry = fields.Datetime(
        string='Caduca', default=lambda self: fields.Datetime.now() + timedelta(days=14))
    computer_id = fields.Many2one('foco.computer', string='Equipo', readonly=True)
    # El MISMO codigo sirve para la laptop Y el celular de la persona: se
    # enrola cada uno por su propio endpoint y aqui quedan los dos, sin pisarse.
    mobile_id = fields.Many2one('foco.mobile.device', string='Movil', readonly=True)
    sent_at = fields.Datetime(readonly=True)
    enrolled_at = fields.Datetime(readonly=True)

    # QR para enrolar el CELULAR: una vez que Foco esta instalado (por cable) y es
    # dueno del equipo, la persona abre Foco, toca "Escanear codigo" y apunta a
    # este QR; la app se conecta sola a este empleado. El QR codifica el enlace
    # foco://enroll?u=<odoo>&c=<codigo>. No se guarda: se dibuja al abrir la ficha.
    qr_image = fields.Binary(string='QR para la app', compute='_compute_qr_image')

    def _enroll_uri(self):
        self.ensure_one()
        base = (self.env['ir.config_parameter'].sudo()
                .get_param('web.base.url') or '').rstrip('/')
        return 'foco://enroll?u=%s&c=%s' % (quote(base, safe=''), self.token or '')

    @api.depends('token')
    def _compute_qr_image(self):
        from reportlab.graphics.barcode import createBarcodeDrawing
        for inv in self:
            if not inv.token:
                inv.qr_image = False
                continue
            try:
                dibujo = createBarcodeDrawing(
                    'QR', value=inv._enroll_uri(), format='png', width=320, height=320)
                inv.qr_image = base64.b64encode(dibujo.asString('png'))
            except Exception:
                inv.qr_image = False

    def action_send(self):
        template = self.env.ref('foco_monitor.mail_tpl_invite', raise_if_not_found=False)
        for inv in self:
            if not inv.email:
                raise UserError(
                    "El empleado %s no tiene 'Correo de trabajo'; agregalo primero." % inv.employee_id.name)
            if template:
                template.send_mail(inv.id, force_send=False)   # se encola y lo manda el cron de correo
            inv.write({'state': 'sent', 'sent_at': fields.Datetime.now()})

    def action_regenerate(self):
        for inv in self:
            inv.write({'token': _code(), 'state': 'draft',
                       'computer_id': False, 'mobile_id': False,
                       'enrolled_at': False})

    @api.model
    def invite_employees(self, employee_ids):
        invs = self.browse()
        for emp in self.env['hr.employee'].browse(employee_ids):
            inv = self.search([('employee_id', '=', emp.id),
                               ('state', 'in', ('draft', 'sent'))], limit=1)
            if not inv:
                inv = self.create({'employee_id': emp.id})
            invs |= inv
        invs.action_send()
        return invs

    @api.model
    def _resolve(self, token):
        inv = self.sudo().search([('token', '=', (token or '').strip())], limit=1)
        if not inv:
            return self.browse()
        if inv.expiry and inv.expiry < fields.Datetime.now():
            inv.state = 'expired'
            return self.browse()
        return inv
