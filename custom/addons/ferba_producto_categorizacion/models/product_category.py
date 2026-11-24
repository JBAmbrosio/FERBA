from odoo import api, fields, models

from odoo.addons.ferba_categoria_sat.models.product_template import EMPRESA_POR_CATEGORIA


class ProductCategory(models.Model):
    _inherit = 'product.category'

    # ferba_categoria_sat solo muestra la "Clave SAT" de la categoria en Francisco. Donde la
    # categorizacion es obligatoria la clave tambien sale de la categoria, asi que ahi tambien se ve.
    ferba_ve_clave_sat = fields.Boolean(compute='_compute_ferba_ve_clave_sat')

    @api.depends_context('company')
    def _compute_ferba_ve_clave_sat(self):
        ve = (self.env.company.id == EMPRESA_POR_CATEGORIA
              or self.env.company.ferba_categorizacion_obligatoria)
        for categ in self:
            categ.ferba_ve_clave_sat = ve
