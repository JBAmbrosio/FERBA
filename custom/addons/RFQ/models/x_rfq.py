from odoo import models, fields, api


class RFQ(models.Model):

    _inherit = "x_rfq" 
  
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

        # Extraer los IDs de los productos de forma segura
        product_ids = producciones.mapped('move_raw_ids.product_id').ids


        return {
            'domain': {
                'materiales_guia': [('id', 'in', product_ids)]
            }
        }





    