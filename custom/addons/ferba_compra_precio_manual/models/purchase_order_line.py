from odoo import api, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _ferba_precio_manual(self):
        self.ensure_one()
        company = self.company_id or self.order_id.company_id or self.env.company
        return company.ferba_compra_precio_manual

    def _reset_price_unit(self, price_unit):
        # Odoo pasa por aquí cada vez que pone un precio automático: el costo del producto o el precio
        # de la lista del proveedor (_compute_price_unit_and_date_planned_and_name y el catálogo).
        # Con precio 0 y technical_price_unit 0 la línea sigue sin "precio tecleado": cuando el comprador
        # capture uno, Odoo 19 lo respeta en los cambios de proveedor y cantidad.
        if self._ferba_precio_manual():
            price_unit = 0.0
        return super()._reset_price_unit(price_unit)

    def _compute_price_unit_and_date_planned_and_name(self):
        super()._compute_price_unit_and_date_planned_and_name()
        # El descuento de la lista del proveedor también es automático; sin precio no aplica.
        for line in self:
            if line.product_id and line.discount and not line.price_unit and not line.technical_price_unit \
                    and line._ferba_precio_manual():
                line.discount = 0.0

    @api.model
    def _prepare_purchase_order_line(self, product_id, product_qty, product_uom, company_id, partner_id, po):
        # Líneas que crea reabastecimiento / abastecimiento desde ventas o fabricación.
        values = super()._prepare_purchase_order_line(product_id, product_qty, product_uom, company_id, partner_id, po)
        if company_id.ferba_compra_precio_manual:
            values.update(price_unit=0.0, discount=0.0)
        return values
