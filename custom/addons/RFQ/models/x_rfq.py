from odoo import models, fields, api
from odoo.exceptions import UserError

class RFQ(models.Model):

    _inherit = "x_rfq"

    x_active = fields.Boolean(string="Activo")
    x_name = fields.Char(string="Nombre")
    materiales_guia = fields.Many2one(string='Material Guía', comodel_name='product.product')
