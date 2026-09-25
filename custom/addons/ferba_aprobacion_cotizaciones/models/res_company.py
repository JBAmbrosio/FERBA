from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Interruptor por empresa. Encendido de fabrica porque el modulo existe para
    # eso; apagarlo deja las cotizaciones como estaban (enviar y confirmar libres).
    ferba_aprobacion_cotizaciones = fields.Boolean(
        string='Requerir aprobacion de cotizaciones', default=True)
