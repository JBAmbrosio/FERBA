from odoo import fields, models


class FocoCommand(models.Model):
    _name = 'foco.command'
    _description = 'Orden de control remoto'
    _order = 'create_date desc'

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', store=True, string='Empleado')
    command_type = fields.Selection([
        ('message', 'Mostrar mensaje'),
        ('lock', 'Bloquear equipo'),
        ('kill_app', 'Cerrar aplicacion'),
        ('logoff', 'Cerrar sesion'),
    ], string='Orden', required=True, default='message')
    payload = fields.Char(
        string='Parametro',
        help='Mensaje a mostrar, o ejecutable a cerrar (p.ej. spotify.exe).')
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('sent', 'Enviada'),
        ('done', 'Ejecutada'),
        ('error', 'Error'),
    ], string='Estado', default='pending', required=True, index=True)
    result = fields.Text(readonly=True)
    sent_at = fields.Datetime(string='Enviada', readonly=True)
    done_at = fields.Datetime(string='Ejecutada', readonly=True)

    def action_resend(self):
        self.write({'state': 'pending', 'result': False,
                    'sent_at': False, 'done_at': False})
