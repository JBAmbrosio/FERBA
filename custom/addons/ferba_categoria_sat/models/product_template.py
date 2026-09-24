from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.onchange('categ_id')
    def _onchange_categ_id_clave_sat(self):
        # Al elegir o cambiar la categoria manda la clave de la categoria. Una
        # clave puesta a mano se respeta mientras no se cambie la categoria.
        for producto in self:
            clave = producto.categ_id.ferba_unspsc_code_id
            if clave:
                producto.unspsc_code_id = clave

    @api.model_create_multi
    def create(self, vals_list):
        # Altas por importacion o por codigo no pasan por el onchange.
        Categoria = self.env['product.category']
        for vals in vals_list:
            if vals.get('categ_id') and not vals.get('unspsc_code_id'):
                clave = Categoria.browse(vals['categ_id']).ferba_unspsc_code_id
                if clave:
                    vals['unspsc_code_id'] = clave.id
        return super().create(vals_list)
