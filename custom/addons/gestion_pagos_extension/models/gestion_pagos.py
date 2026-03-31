import logging
from odoo import models, api

_logger = logging.getLogger(__name__)

class GestionDePagos(models.Model):
    _inherit = 'x_gestion_de_pagos'

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        _logger.info(">>> Ejecutando read_group en x_gestion_de_pagos")
        res = super().read_group(domain, fields, groupby, offset, limit, orderby, lazy)

        if 'x_studio_monto_de_pago' in fields and 'x_studio_moneda' in groupby:
            for line in res:
                if '__domain' in line:
                    total = sum(self.search(line['__domain']).mapped('x_studio_monto_de_pago'))
                    line['x_studio_monto_de_pago'] = total

        if 'x_studio_monto_aprobado' in fields and 'x_studio_moneda' in groupby:
            for line in res:
                if '__domain' in line:
                    total = sum(self.search(line['__domain']).mapped('x_studio_monto_aprobado'))
                    line['x_studio_monto_aprobado'] = total

        return res
