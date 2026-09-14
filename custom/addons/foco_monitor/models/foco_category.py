from odoo import api, fields, models


class FocoCategory(models.Model):
    _name = 'foco.category'
    _description = 'Categoria de aplicacion (parametrizacion de productividad)'
    _order = 'weight desc, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(help='Identificador estable, p.ej. productiva, distraccion.')
    weight = fields.Float(
        default=0.0,
        help='Peso para el indice de productividad. 1.0 = 100% productivo, '
             '0.0 = no aporta. Ej: productiva 1.0, navegador 0.5, distraccion 0.0.')
    is_system = fields.Boolean(
        string='Ruido del sistema',
        help='Si esta marcado, las apps de esta categoria se EXCLUYEN del tiempo '
             'activo (explorer.exe, host del shell, buscar, etc.).')
    color = fields.Integer()
    app_ids = fields.One2many('foco.app', 'category_id')
    app_count = fields.Integer(string='Apps', compute='_compute_app_count')

    def _compute_app_count(self):
        groups = self.env['foco.app']._read_group(
            [('category_id', 'in', self.ids)], ['category_id'], ['__count'])
        counts = {cat.id: n for cat, n in groups}
        for rec in self:
            rec.app_count = counts.get(rec.id, 0)
