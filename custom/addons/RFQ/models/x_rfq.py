from odoo import models, fields

class RFQ(models.Model):

    _name = "x_rfq"

    x_active = fields.Boolean(string="Activo")
    x_name = fields.Char(string="Nombre")
   
