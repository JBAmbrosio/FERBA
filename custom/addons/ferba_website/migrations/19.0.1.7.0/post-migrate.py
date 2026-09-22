# -*- coding: utf-8 -*-
"""19.0.1.7.0: las 9 páginas de contenido pasan a indexarse, con título y
descripción propios para el buscador; las dos legales siguen fuera mientras
sean el andamio «en preparación». data/pages.xml es noupdate="1" y no toca
registros que ya existen, así que el cambio va por código, una vez."""
from odoo import SUPERUSER_ID, api

from odoo.addons.ferba_website.hooks import aplicar_indexacion


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    aplicar_indexacion(env)
