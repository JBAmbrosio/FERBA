# -*- coding: utf-8 -*-
"""19.0.1.9.0: el aviso de privacidad y los términos y condiciones dejan de ser
el andamio «en preparación» y pasan a llevar el texto que entregó Ferba.

Aquí solo hace falta volver a pasar la indexación: las dos páginas estaban
marcadas website_indexed = False a propósito y ahora entran en el buscador con
su propio título y descripción (hooks.SEO). El contenido en sí son plantillas,
que se actualizan solas al actualizar el módulo.

aplicar_indexacion() solo escribe los campos que estén vacíos, así que si
alguien ya les había puesto título o descripción a mano desde Sitio web >
Optimizar SEO, no se los pisa. Purga además el sitemap, que se sirve de un
adjunto con 12 h de vida y todavía no anunciaría las dos páginas.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.ferba_website.hooks import aplicar_indexacion


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    aplicar_indexacion(env)
