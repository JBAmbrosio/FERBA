from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    # Por empresa, como las cuentas de la categoria: la clave es parte de como
    # contabiliza y factura CADA empresa. Guardarla global haria que la primera
    # empresa en configurarla le cambiara la clave a los productos de las demas.
    ferba_unspsc_code_id = fields.Many2one(
        'product.unspsc.code', string='Clave SAT',
        company_dependent=True,
        domain=[('applies_to', '=', 'product')],
        help='Clave de producto/servicio del SAT que toman los productos de '
             'esta categoria en la empresa activa.')
