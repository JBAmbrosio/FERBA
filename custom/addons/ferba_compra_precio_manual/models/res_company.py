from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ferba_compra_precio_manual = fields.Boolean(
        string='Precio de compra solo manual',
        help='Las líneas de compra no traen precio automático (ni costo del producto ni lista del proveedor); '
             'el precio lo captura el comprador.',
    )
