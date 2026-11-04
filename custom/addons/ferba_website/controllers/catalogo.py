# -*- coding: utf-8 -*-
"""Rutas dinámicas del catálogo de Ferba.

    /maquinaria/<categoria>              página de categoría (etapa)
    /maquinaria/<categoria>/<maquina>    ficha de máquina (URL canónica)
    /frutos/<fruto>                      página de fruto

Las landings /maquinaria y /frutos siguen siendo las website.page estáticas del
módulo; aquí solo cuelgan las interiores. La ficha de máquina tiene UNA sola URL
—bajo su categoría— aunque la liste también un fruto: el fruto enlaza a esa misma
URL, así no hay contenido duplicado.

Todo lo que se publica está `active=True`; un slug que no existe cae en el 404 de
Ferba (http_routing.404, heredado en ferba_paginas.xml).

SEO: cada página pasa `title` y `website_meta_description` al contexto (los lee
`website.layout` para el `<title>` y el `<meta name="description">`), tomados del
`meta_titulo`/`meta_descripcion` del registro. El `meta_titulo` ya trae el sufijo
" | FERBA", así que se usa tal cual sin añadir el nombre del sitio.

Sitemap: las rutas llevan parámetros, así que Odoo no puede enumerarlas solo;
cada una declara una función `sitemap_*` que genera sus URLs reales.
"""
from odoo import http
from odoo.http import request
from werkzeug.exceptions import NotFound


# ---------------------------------------------------------------------------
#  Generadores de sitemap (una entrada por registro publicado)
# ---------------------------------------------------------------------------
def sitemap_categoria(env, rule, qs):
    for cat in env['ferba.categoria'].sudo().search([('active', '=', True)]):
        loc = '/maquinaria/%s' % cat.slug
        if not qs or qs.lower() in loc.lower():
            yield {'loc': loc}


def sitemap_maquina(env, rule, qs):
    maquinas = env['ferba.maquina'].sudo().search([('active', '=', True)])
    for maq in maquinas:
        if not maq.categoria_id or not maq.categoria_id.active:
            continue
        loc = '/maquinaria/%s/%s' % (maq.categoria_id.slug, maq.slug)
        if not qs or qs.lower() in loc.lower():
            yield {'loc': loc}


def sitemap_fruto(env, rule, qs):
    for fr in env['ferba.fruto'].sudo().search([('active', '=', True)]):
        loc = '/frutos/%s' % fr.slug
        if not qs or qs.lower() in loc.lower():
            yield {'loc': loc}


def _seo(record):
    """Contexto SEO para `website.layout`: `title` (tal cual, ya trae '| FERBA')
    y `website_meta_description`. Si faltan, se devuelven vacíos y la plantilla
    cae en su título por defecto."""
    return {
        'title': record.meta_titulo or '',
        'website_meta_description': record.meta_descripcion or '',
    }


class CatalogoFerba(http.Controller):

    # ------------------------------------------------------------------
    #  Maquinaria: categoría y ficha
    # ------------------------------------------------------------------
    @http.route(['/maquinaria/<string:categoria>'], type='http', auth='public',
                website=True, sitemap=sitemap_categoria)
    def categoria(self, categoria, **kw):
        cat = request.env['ferba.categoria'].sudo().search(
            [('slug', '=', categoria), ('active', '=', True)], limit=1)
        if not cat:
            raise NotFound()
        # Máquinas agrupadas por subgrupo (Empaque y Transportadores agrupan; las
        # demás son planas y caen todas en el grupo sin subgrupo). El agrupado va
        # aquí porque QWeb no permite filtrar con lambda en la plantilla.
        maquinas = cat.maquina_ids.filtered(lambda m: m.active)
        grupos = []
        if cat.subgrupo_ids:
            for sg in cat.subgrupo_ids.sorted(lambda s: (s.secuencia, s.id)):
                items = maquinas.filtered(lambda m: m.subgrupo_id == sg) \
                                .sorted(lambda m: (m.secuencia, m.id))
                if items:
                    grupos.append((sg, items))
            sueltas = maquinas.filtered(lambda m: not m.subgrupo_id)
            if sueltas:
                grupos.append((request.env['ferba.categoria.subgrupo'], sueltas))
        else:
            grupos = [(request.env['ferba.categoria.subgrupo'], maquinas)]
        vals = {'categoria': cat, 'grupos': grupos}
        vals.update(_seo(cat))
        return request.render('ferba_website.pagina_categoria', vals)

    @http.route(['/maquinaria/<string:categoria>/<string:maquina>'], type='http',
                auth='public', website=True, sitemap=sitemap_maquina)
    def maquina(self, categoria, maquina, **kw):
        maq = request.env['ferba.maquina'].sudo().search(
            [('slug', '=', maquina), ('active', '=', True)], limit=1)
        # La categoría de la URL tiene que ser la real de la máquina: evita que
        # la misma ficha responda bajo cualquier categoría (contenido duplicado).
        if not maq or maq.categoria_id.slug != categoria:
            raise NotFound()
        # Los bloques se pre-filtran aquí (QWeb no filtra con lambda). El orden de
        # render es fijo y coincide con los PDFs: splits -> beneficios (tarjetas
        # oscuras) -> pasos -> specs -> características (tarjetas claras) -> galería.
        bloques = maq.bloque_ids
        # Versión del módulo como cache-buster de las imágenes de la ficha: cada
        # republicación cambia la URL (?v=...) y el navegador no sirve la vieja
        # (los static llevan max-age de 7 días).
        modulo = request.env['ir.module.module'].sudo().search(
            [('name', '=', 'ferba_website')], limit=1)
        vals = {
            'maquina': maq,
            'categoria': maq.categoria_id,
            'ver': (modulo.installed_version or '0').replace('.', ''),
            'splits': bloques.filtered(lambda b: b.tipo == 'split'),
            'beneficios': bloques.filtered(lambda b: b.tipo == 'tarjetas' and b.tema == 'oscuro'),
            'caracteristicas': bloques.filtered(lambda b: b.tipo == 'tarjetas' and b.tema == 'claro'),
        }
        vals.update(_seo(maq))
        return request.render('ferba_website.pagina_maquina', vals)

    # ------------------------------------------------------------------
    #  Frutos
    # ------------------------------------------------------------------
    @http.route(['/frutos/<string:fruto>'], type='http', auth='public',
                website=True, sitemap=sitemap_fruto)
    def fruto(self, fruto, **kw):
        fr = request.env['ferba.fruto'].sudo().search(
            [('slug', '=', fruto), ('active', '=', True)], limit=1)
        if not fr:
            raise NotFound()
        # Las máquinas del fruto, agrupadas por categoría (la "etapa"), en el
        # orden de las categorías y de las máquinas.
        maquinas = fr.maquina_ids.filtered(lambda m: m.active)
        grupos = []
        for cat in maquinas.mapped('categoria_id').sorted(lambda c: (c.secuencia, c.id)):
            delcat = maquinas.filtered(lambda m: m.categoria_id == cat) \
                             .sorted(lambda m: (m.secuencia, m.id))
            grupos.append((cat, delcat))
        vals = {'fruto': fr, 'grupos': grupos}
        vals.update(_seo(fr))
        return request.render('ferba_website.pagina_fruto', vals)
