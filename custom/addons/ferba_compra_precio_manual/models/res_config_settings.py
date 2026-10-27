from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ferba_compra_precio_manual = fields.Boolean(related='company_id.ferba_compra_precio_manual', readonly=False)
