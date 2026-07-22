from odoo import models, fields, api
from odoo.exceptions import UserError

class RFQ(models.Model):

    _name = "x_rfq"
    _description = "Modelo utilizado para realizar requiciones a inventarios"
  
    x_active = fields.Boolean(string="Activo")
    x_almacen = fields.Many2one(string="Almacén", comodel_name='stock.warehouse', help='Almacén')
    x_cliente_1 = fields.Many2one(string="Cliente", comodel_name='res.partner', help='Cliente realcionado a la cotización')
    x_comentarios = fields.Text(string="Comentarios")
    x_cotizacin = fields.Many2one(string="Cotización", comodel_name='sale.order', help='Cotizacion relacionada')
    x_direccin_de_envio = fields.Text(string="Direccion de envió")
    x_name = fields.Char(string="Nombre")

    x_studio_cliente = fields.Char(string="Cliente")
    x_studio_enviar_rfq = fields.Boolean(string="Enviar RFQ")
    x_studio_estatus_rfq = fields.Selection([('0','REQUISICION'),('1','RFQ ENVIADA'),('2','APROBADO'),('3','RECHAZADO')],string="Estatus", default='')
    x_studio_fecha_limite = fields.Date(string="Fecha limite")
    x_studio_many2one_field_7YQia = fields.Many2one(string="Proyecto", comodel_name='project.project')
    x_studio_orden_de_venta = fields.Char(string="Orden de venta")
    x_studio_proyecto = fields.Many2one(string="Centro de costo", comodel_name='account.analytic.account')
    x_studio_proyectos = fields.Many2one(string="Presupuesto" ,comodel_name='budget.analytic')
    x_studio_rfq = fields.One2many(string="RFQ", comodel_name='x_rfq_line_f0aac', inverse_name='x_rfq_id')
    x_studio_secuencia = fields.Char(string="Secuencia")
    x_studio_selection_field_sO1tV = fields.Selection([('0','REQUISICIÓN'),('1','RFQ ENVIADA')],string="name")
    x_studio_sequence = fields.Integer(string="Secuencia")
    x_studio_solicitud = fields.Many2one(string="Solicitud", comodel_name='x_solicitudes')

    materiales_guia = fields.Many2one(string='Material Guía', comodel_name='product.product')

    @api.onchange('x_studio_proyecto')
    def _onchange_x_studio_proyecto(self):
        
        self.materiales_guia = False

        if not self.x_studio_proyecto:
            return {
                'domain': {'materiales_guia': [('id', '=', False)]}
            }

        # Buscar órdenes de producción relacionadas
        producciones = self.env['mrp.production'].search([
            ('x_studio_centro_de_costo', '=', self.x_studio_proyecto.id),
             ('state', 'not in', ['cancel'])
        ])

        # Obtener componentes de las órdenes
        product_ids = producciones.move_raw_ids.mapped('product_id').ids


        return {
            'domain': {
                'materiales_guia': [('id', 'in', product_ids)]
            }
        }
    

    def crear_solicitud_inv(self):
    
        self.ensure_one()
                     
        Lproductos = self.x_studio_rfq # listado donde se anidadn todos los productos.
        FechaDeOrder = self.x_studio_fecha_limite
        idDoc = self.id
        Es_Presupuesto = self.x_studio_proyectos #model crossovered.budget
        CentroDeCosto = self.x_studio_proyecto #model account.analitic.account
        send_address = self.x_direccin_de_envio #direccin de envio
        costumer = self.x_cliente_1 #model res.partner clientes
        quotation = self.x_cotizacin #model sale.order

        banderaError = 0 #si no tiene ningun error
        faltantes = []  # Lista para almacenar los productos con cantidad 0

        #Validar que el modelo line tenga registros 
        if not Lproductos:
            raise UserError("La RFQ no tiene ningun concepto solicitado")
        
        #Validar cantidades en 0  
        faltantes = Lproductos.filtered(lambda l: l.x_studio_cantidad == 0)
          
        if faltantes:

            faltantes.write({'x_studio_error': '1'})

            # Alistar nombres para el error
            nombres_faltantes = ", ".join(faltantes.mapped('x_studio_many2one_field_BBoFT.name'))
            
            # Publicar en el chatter antes de detener el proceso
            self.message_post(body=f"Intento de creación fallido. Productos con cantidad en 0: {nombres_faltantes}")
            
            # Detener la ejecución de forma segura y mostrar alerta en pantalla
            raise UserError(f"Faltan cantidades por llenar para los siguientes productos: {nombres_faltantes}")

        # Si pasa la validación, aseguramos que todas las líneas limpien su estado de error
        Lproductos.write({'x_studio_error': '0'})


        solicitudes = self.env['x_solicitudes'].create({
                                'x_studio_fecha': FechaDeOrder,
                                'x_studio_proyecto' : Es_Presupuesto.id,
                                'x_studio_proyectos' : CentroDeCosto.id,
                                'x_studio_requisicin': idDoc,
                                'x_direccion_de_envio' : send_address,
                                'x_cliente_1' :costumer.id,
                                'x_cotizacion' : quotation.id
                            
                        })
                            
           
 
        lineas_vals = []
        for linea in Lproductos: 
                  
            lineas_vals.append({
                
                                'x_solicitudes_id': solicitudes.id, #Esto es el id del modelox_solicitudes
                                'x_studio_many2one_field_Hc2Sw': linea.x_studio_many2one_field_BBoFT.id,  # ID del producto
                                'x_studio_solicitado': linea.x_studio_cantidad,  # Cantidad del producto
                                'x_name': linea.x_name
                                        
            })
                
        if lineas_vals:

            try:
                self.env['x_solicitudes_line_8cdc9'].create(lineas_vals)
            except Exception as e:
                self.message_post(body="Error 0001: Al crear lineas de conceptos en solicitudes de inv: {str(e)}")
                raise UserError(f"No se crearon las lineas de conceptos para solicitudes de inventario: {str(e)}")
                        
                   
            
        self.message_post(body="Fallo en la creación de solicitud de requisición")
        
        
        self.write({
            'x_studio_estatus_rfq': '1',
            'x_studio_selection_field_sO1tV': '1',
        })
            
        if self.x_studio_estatus_rfq == "1":
            folio = self.x_studio_secuencia
            
            rfq = self.env['ir.sequence'].next_by_code('x_rfq')

            if not rfq:
                raise UserError("No existe la secuencia 'rfq'.")
            
            self.write({
                        'x_studio_secuencia':rfq,
                        'x_name':self.x_studio_secuencia

            
            })
    
            return True
              
   
class RFQLine(models.Model):

    _name="x_rfq_line_f0aac"
    _description="Modelo que guarda los registros de los productos que se solicitan"

    x_name = fields.Char(string="Nombre")
    x_studio_cantidad = fields.Integer(string="Cantidad")
    x_rfq_id = fields.Many2one(comodel_name="x_rfq",  string="Solicitud")
    x_studio_error = fields.Selection([('0','0'),('1','1')],string="name")
    x_studio_imagen = fields.Binary(string="Imagén")
    x_studio_many2one_field_BBoFT = fields.Many2one(comodel_name="product.product",  string="Producto")
    x_studio_sequence = fields.Integer(string="Folio")