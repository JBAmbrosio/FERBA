from odoo import models


class StockRule(models.Model):
    _inherit = 'stock.rule'

    def _update_purchase_order_line(self, product_id, product_qty, product_uom, company_id, values, line):
        # Cuando reabastecimiento suma cantidad a una línea que ya existe, Odoo le vuelve a poner el precio
        # del proveedor. Con la opción activa se conserva el precio que la línea ya tenía.
        res = super()._update_purchase_order_line(product_id, product_qty, product_uom, company_id, values, line)
        if line.company_id.ferba_compra_precio_manual:
            res['price_unit'] = line.price_unit
        return res
