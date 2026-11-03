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
"""
from odoo import http
from odoo.http import request
from werkzeug.exceptions import NotFound


class CatalogoFerba(http.Controller):

    # ------------------------------------------------------------------
    #  Maquinaria: categoría y ficha
    # ------------------------------------------------------------------
    @http.route(['/maquinaria/<string:categoria>'], type='http', auth='public',
                website=True, sitemap=True)
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
        return request.render('ferba_website.pagina_categoria', {
            'categoria': cat,
            'grupos': grupos,
        })

    @http.route(['/maquinaria/<string:categoria>/<string:maquina>'], type='http',
                auth='public', website=True, sitemap=True)
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
        return request.render('ferba_website.pagina_maquina', {
            'maquina': maq,
            'categoria': maq.categoria_id,
            'splits': bloques.filtered(lambda b: b.tipo == 'split'),
            'beneficios': bloques.filtered(lambda b: b.tipo == 'tarjetas' and b.tema == 'oscuro'),
            'caracteristicas': bloques.filtered(lambda b: b.tipo == 'tarjetas' and b.tema == 'claro'),
        })

    # ------------------------------------------------------------------
    #  Frutos
    # ------------------------------------------------------------------
    @http.route(['/frutos/<string:fruto>'], type='http', auth='public',
                website=True, sitemap=True)
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
        return request.render('ferba_website.pagina_fruto', {
            'fruto': fr,
            'grupos': grupos,
        })
