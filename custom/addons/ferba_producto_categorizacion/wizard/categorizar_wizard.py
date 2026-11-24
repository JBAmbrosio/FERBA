from odoo import api, fields, models
from odoo.exceptions import UserError

from ..models.product_template import raiz_contable


class FerbaProductoCategorizar(models.TransientModel):
    _name = 'ferba.producto.categorizar'
    _description = 'Categorizar o rechazar el alta de productos'

    product_tmpl_ids = fields.Many2many(
        'product.template', string='Productos', context={'active_test': False})
    modo = fields.Selection(
        [('categorizar', 'Categorizar'), ('rechazar', 'Rechazar alta')],
        string='Accion', default='categorizar', required=True)
    categ_id = fields.Many2one('product.category', string='Categoria')
    ferba_categ_raiz_id = fields.Many2one('product.category', compute='_compute_ferba_categ_raiz_id')
    motivo = fields.Text(string='Motivo del rechazo')

    def _compute_ferba_categ_raiz_id(self):
        raiz = raiz_contable(self.env)
        for rec in self:
            rec.ferba_categ_raiz_id = raiz

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        if (not res.get('product_tmpl_ids') and ctx.get('active_model') == 'product.template'
                and ctx.get('active_ids')):
            res['product_tmpl_ids'] = [(6, 0, ctx['active_ids'])]
        ids = []
        for comando in res.get('product_tmpl_ids') or []:
            if isinstance(comando, (list, tuple)) and comando and comando[0] == 6:
                ids = list(comando[2])
            elif isinstance(comando, int):
                ids.append(comando)
        if 'categ_id' in fields_list and not res.get('categ_id') and ids:
            productos = self.env['product.template'].with_context(active_test=False).browse(ids)
            sugeridas = productos.mapped('ferba_categ_sugerida_id')
            if len(sugeridas) == 1 and all(productos.mapped('ferba_categ_sugerida_id')):
                res['categ_id'] = sugeridas.id
            elif len(productos) == 1 and productos.categ_id:
                res['categ_id'] = productos.categ_id.id
        return res

    def action_aplicar(self):
        self.ensure_one()
        productos = self.product_tmpl_ids.with_context(active_test=False)
        if not productos:
            raise UserError('No hay productos seleccionados.')
        if self.modo == 'categorizar':
            if not self.categ_id:
                raise UserError('Elige la categoria.')
            productos._ferba_categorizar(self.categ_id)
        else:
            productos._ferba_rechazar(self.motivo)
        return {'type': 'ir.actions.act_window_close'}
