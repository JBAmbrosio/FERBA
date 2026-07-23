from odoo import models, fields, api
from odoo.exceptions import UserError

class RFQ(models.Model):

    _inherit = "x_rfq"

    x_active = fields.Boolean(string="Activo")
    x_almacen = fields.Many2one(string="Almacén", comodel_name='stock.warehouse', help='Almacén')
    x_cliente_1 = fields.Many2one(string="Cliente", comodel_name='res.partner', help='Cliente realcionado a la cotización')
    x_comentarios = fields.Text(string="Comentarios")
    x_cotizacin = fields.Many2one(string="Cotización", comodel_name='sale.order', help='Cotizacion relacionada')
    x_direccin_de_envio = fields.Text(string="Direccion de envió")
    x_name = fields.Char(string="Nombre")

    x_studio_cliente = fields.Char(string="Cliente")
    x_studio_enviar_rfq = fields.Boolean(string="Enviar RFQ")
    x_studio_estatus_rfq = fields.Selection([('0','REQUISICION'),('1','RFQ ENVIADA'),('2','APROBADO'),('3','RECHAZADO')],string="Estatus", default='1')
    x_studio_fecha_limite = fields.Date(string="Fecha limite")
    x_studio_many2one_field_7YQia = fields.Many2one(string="Proyecto", comodel_name='project.project')
    x_studio_orden_de_venta = fields.Char(string="Orden de venta")
    x_studio_proyecto = fields.Many2one(string="Centro de costo", comodel_name='account.analytic.account')
    x_studio_proyectos = fields.Many2one(string="Presupuesto" , comodel_name='budget.analytic')
    x_studio_rfq = fields.One2many(string="RFQ", comodel_name='x_rfq_line_f0aac', inverse_name='x_rfq_id')
    x_studio_secuencia = fields.Char(string="Secuencia")
    x_studio_selection_field_sO1tV = fields.Selection([('0','REQUISICIÓN'),('1','RFQ ENVIADA')],string="name")
    x_studio_sequence = fields.Integer(string="Secuencia")
    x_studio_solicitud = fields.Many2one(string="Solicitud", comodel_name='x_solicitudes')
    materiales_guia = fields.Many2one(string='Material Guía', comodel_name='product.product')
