from odoo import models, fields, api

class GestionDePagos(models.Model):
    _inherit = 'x_gestion_de_pagos'

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        """
        Sobrescribe read_group para agrupar correctamente la suma por moneda.
        """
        res = super(GestionDePagos, self).read_group(domain, fields, groupby, offset, limit, orderby, lazy)

        if 'x_studio_monto_de_pago' in fields and 'x_studio_moneda' in groupby:
            for line in res:
                if '__domain' in line:
                    total = sum(self.search(line['__domain']).mapped('x_studio_monto_de_pago'))
                    line['x_studio_monto_de_pago'] = total

        return res
