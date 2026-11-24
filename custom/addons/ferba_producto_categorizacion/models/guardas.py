"""Guardas de servidor: un producto que no esta categorizado no entra en documentos.

El producto por categorizar esta archivado, asi que en pantalla no se puede elegir. Estas guardas
cubren lo que la pantalla no ve: el alta rapida desde una linea («Crear ...»), importaciones,
acciones de Studio que copian lineas y llamadas por RPC.
"""
from odoo import api, models
from odoo.exceptions import UserError

from .product_template import AYUDA_COLA, SIN_REGLAS


def validar_productos(env, vals_list):
    if env.context.get(SIN_REGLAS):
        return
    ids = {vals['product_id'] for vals in vals_list if vals.get('product_id')}
    if not ids:
        return
    productos = env['product.product'].sudo().with_context(active_test=False).browse(list(ids))
    ocultos = productos.filtered(lambda p: p.product_tmpl_id.ferba_categorizacion != 'categorizado')
    if ocultos:
        raise UserError(
            '%s todavia no tiene categoria contable, asi que no se puede usar. Lo libera un categorizador '
            'al asignarle su categoria (%s).' % (', '.join(ocultos.mapped('display_name')), AYUDA_COLA))


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        validar_productos(self.env, vals_list)
        return super().create(vals_list)

    def write(self, vals):
        validar_productos(self.env, [vals])
        return super().write(vals)


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        validar_productos(self.env, vals_list)
        return super().create(vals_list)

    def write(self, vals):
        validar_productos(self.env, [vals])
        return super().write(vals)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        validar_productos(self.env, vals_list)
        return super().create(vals_list)

    def write(self, vals):
        validar_productos(self.env, [vals])
        return super().write(vals)


class StockMove(models.Model):
    _inherit = 'stock.move'

    @api.model_create_multi
    def create(self, vals_list):
        validar_productos(self.env, vals_list)
        return super().create(vals_list)

    def write(self, vals):
        validar_productos(self.env, [vals])
        return super().write(vals)
