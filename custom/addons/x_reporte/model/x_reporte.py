from odoo import models, fields


class xReports(models.Model):
    _name= 'x_reporte'
    _description = 'Parametriza cualquier reporte qweb de odoo'

    x_name = fields.Char(string="Name")
    x_studio_tel_2 = fields.Char(string="Tel 2")
    x_studio_tel_1 = fields.Char(string="Tel 1")
    x_studio_sitio_web = fields.Char(string="Sitio Web")
    x_studio_rfc = fields.Char(string="RFC")
    x_studio_notificacin = fields.Text(string="Notificación")
    x_studio_leyenda = fields.Text(string="Leyenda")

    x_studio_imagen_pie_de_pagina_1 = fields.Binary(string="Imagen Pie de Pagina")
    x_studio_imagen_pie_de_pagina = fields.Binary(string="Imagen Pie de Pagina")

    x_studio_header = fields.Binary(string="header")

    x_studio_estado = fields.Char(string="Estado")
    x_studio_direccin = fields.Char(string="Dirección")
    x_studio_cuentas_deposito = fields.Char(string="Cuentas Deposito")

    x_studio_correo = fields.Char(string="Correo")
    x_studio_compaia = fields.Char(string="Compañia")
