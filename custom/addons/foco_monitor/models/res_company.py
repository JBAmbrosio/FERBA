# -*- coding: utf-8 -*-
"""Empresa (res.company): contraparte de EE. UU. para el alta de clientes.

FERBA opera varias empresas (Mexico, EE. UU., Peru). Un vendedor pertenece a una
empresa de Mexico (p. ej. Industrias Tecnologicas EMP), pero levanta clientes a
ambos lados de la frontera. Cuando registra desde el telefono un cliente cuya
ubicacion cae en Estados Unidos, el cliente debe quedar en la empresa
estadounidense correspondiente, no en la de Mexico.

El emparejamiento es por EMPRESA, no por vendedor: cada empresa de Mexico apunta
a su contraparte de EE. UU. aqui, y el alta movil (`foco.mobile.device.crear_cliente`)
lo usa. Asi, si manana otro vendedor de la misma empresa registra en EE. UU., el
cliente cae solo en la empresa correcta.
"""
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    foco_us_company_id = fields.Many2one(
        'res.company', string='Empresa de EE. UU. correspondiente',
        help="Cuando un vendedor de esta empresa registra desde el telefono un "
             "cliente ubicado en Estados Unidos, el cliente se crea en esta "
             "empresa (no en la de Mexico). Debe ser una empresa con pais "
             "Estados Unidos, y el vendedor debe tenerla entre sus empresas "
             "permitidas para poder verla y registrar en ella.")
