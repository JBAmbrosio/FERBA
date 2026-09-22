# -*- coding: utf-8 -*-
"""19.0.1.6.1: /contactus deja de ser un 404 y redirige (301) al contacto de
Ferba, porque la cabecera del tema de Odoo lo enlaza desde las páginas que no
son de Ferba. La función es la misma de 19.0.1.6.0 y es idempotente: lo ya
retirado se queda como está."""
from odoo import SUPERUSER_ID, api

from odoo.addons.ferba_website.hooks import retirar_paginas_de_fabrica


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    retirar_paginas_de_fabrica(env)
