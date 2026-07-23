from odoo import models, fields

class RFQ(models.Model):

    _inherit = "x_rfq" 
    _description = "Modelo utilizado para realizar requiciones a inventarios"
  
    x_active = fields.Boolean(string="Activo") 
    x_name = fields.Char(string="Nombre")
    materiales_guia = fields.Many2one(string='Material Guía', comodel_name='product.product')



    