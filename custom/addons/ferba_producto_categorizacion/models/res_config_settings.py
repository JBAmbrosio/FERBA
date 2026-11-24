from odoo import api, fields, models

from odoo.addons.ferba_categoria_sat.models.product_template import GRUPO_GESTOR

from ..hooks import PARAM_RAIZ


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ferba_categorizacion_obligatoria = fields.Boolean(
        related='company_id.ferba_categorizacion_obligatoria', readonly=False)
    ferba_categ_raiz_id = fields.Many2one(
        'product.category', string='Categorias contables', config_parameter=PARAM_RAIZ,
        help='Categoria raiz: solo sus hijas cuentan como categoria contable (hoy "CATEGORIAS CONTABLES"). '
             'Es la misma para todas las empresas porque las categorias son globales; lo que cambia por '
             'empresa son sus cuentas y su clave SAT.')

    # Igual que los aprobadores de cotizaciones: es una vista del grupo, no un dato aparte, para que
    # no haya dos verdades sobre quien categoriza.
    ferba_categorizadores_ids = fields.Many2many(
        'res.users', string='Categorizadores',
        compute='_compute_ferba_categorizadores', inverse='_inverse_ferba_categorizadores',
        help='Asignan la categoria a los productos nuevos, reciben el aviso de cada alta y pueden crear y '
             'editar categorias. Pon a mas de una persona para cubrir ausencias.')

    def _ferba_grupo_categorizador(self):
        return self.env.ref(GRUPO_GESTOR, raise_if_not_found=False)

    @api.depends('company_id')
    def _compute_ferba_categorizadores(self):
        grupo = self._ferba_grupo_categorizador()
        for rec in self:
            rec.ferba_categorizadores_ids = grupo.sudo().user_ids.filtered(lambda u: not u.share) if grupo else False

    def _inverse_ferba_categorizadores(self):
        grupo = self._ferba_grupo_categorizador()
        if not grupo:
            return
        for rec in self:
            grupo.sudo().write({'user_ids': [(6, 0, rec.ferba_categorizadores_ids.ids)]})
