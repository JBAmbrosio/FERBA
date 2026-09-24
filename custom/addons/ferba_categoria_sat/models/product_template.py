from odoo import api, fields, models

# Por ahora solo Francisco contabiliza por categoria.
EMPRESA_POR_CATEGORIA = 17
GRUPO_GESTOR = 'ferba_categoria_sat.group_gestor_categorias'


def _ve_contabilidad(env):
    # Donde la contabilidad sale de la categoria, las cuentas y la clave SAT
    # del producto no se capturan a mano: solo las ve quien administra las
    # categorias. En las demas empresas nada cambia.
    return (env.company.id != EMPRESA_POR_CATEGORIA
            or env.user.has_group(GRUPO_GESTOR))


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    ferba_ve_contabilidad = fields.Boolean(compute='_compute_ferba_ve_contabilidad')

    @api.depends_context('company', 'uid')
    def _compute_ferba_ve_contabilidad(self):
        ve = _ve_contabilidad(self.env)
        for producto in self:
            producto.ferba_ve_contabilidad = ve

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


class ProductProduct(models.Model):
    _inherit = 'product.product'

    # Propio y no delegado a la plantilla: depende de la empresa y el usuario
    # de quien mira, no de un dato guardado.
    ferba_ve_contabilidad = fields.Boolean(compute='_compute_ferba_ve_contabilidad')

    @api.depends_context('company', 'uid')
    def _compute_ferba_ve_contabilidad(self):
        ve = _ve_contabilidad(self.env)
        for producto in self:
            producto.ferba_ve_contabilidad = ve
