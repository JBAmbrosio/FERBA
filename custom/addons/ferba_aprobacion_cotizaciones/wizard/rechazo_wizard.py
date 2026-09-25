from odoo import fields, models


class FerbaCotizacionRechazo(models.TransientModel):
    _name = 'ferba.cotizacion.rechazo'
    _description = 'Rechazar cotizacion (con motivo)'

    order_id = fields.Many2one('sale.order', string='Cotizacion', required=True, readonly=True)
    motivo = fields.Text(
        string='Motivo', required=True,
        help='Le llega tal cual al vendedor, en el chatter y en su bandeja.')

    def action_confirmar(self):
        self.ensure_one()
        self.order_id._ferba_rechazar(self.motivo)
        return {'type': 'ir.actions.act_window_close'}
