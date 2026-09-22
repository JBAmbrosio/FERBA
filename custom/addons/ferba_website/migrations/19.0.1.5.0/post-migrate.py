# -*- coding: utf-8 -*-
"""19.0.1.5.0: la página de Ferba pasa a ser la portada ('/'), se elimina la
«Home» de fábrica de Odoo y /inicio-ferba redirige con 301. La lógica vive en
hooks.fijar_portada para compartirla con el post_init_hook."""
from odoo import SUPERUSER_ID, api

from odoo.addons.ferba_website.hooks import fijar_portada


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    fijar_portada(env)
