from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Apagado de fabrica: encenderlo cambia como trabaja quien da de alta productos en esa empresa
    # (Industrias da de alta ~110 al mes, sobre todo desde ventas), asi que se decide empresa por empresa.
    ferba_categorizacion_obligatoria = fields.Boolean(
        string='Productos nuevos requieren categorizacion',
        help='Un producto dado de alta por alguien que no es categorizador queda oculto (archivado) y sin '
             'categoria hasta que un categorizador le asigna su categoria contable.')
