# -*- coding: utf-8 -*-
"""19.0.1.6.0: fuera del sitio las páginas y rutas de fábrica de Odoo que no
son de Ferba (/contactus, /contactus-thank-you, /terms, /website/info, /jobs…).
La lógica vive en hooks.retirar_paginas_de_fabrica para compartirla con el
post_init_hook."""
from odoo import SUPERUSER_ID, api

from odoo.addons.ferba_website.hooks import retirar_paginas_de_fabrica


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    retirar_paginas_de_fabrica(env)
