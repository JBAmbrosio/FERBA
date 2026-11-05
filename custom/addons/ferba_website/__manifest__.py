# -*- coding: utf-8 -*-
{
    'name': 'Ferba — Sitio web',
    'version': '19.0.1.21.0',
    'author': 'Simdata Group',
    'category': 'Website/Website',
    'summary': 'Tema y páginas del sitio público de Ferba Postharvest Technology',
    'description': """
Sitio público de Ferba
======================

Implementa el diseño entregado por el cliente (9 páginas) como tema propio
sobre el constructor de sitios web de Odoo 19.

- Paleta y tipografía de marca en `static/src/scss/ferba_variables.scss`
- Secciones reutilizables como snippets editables desde el constructor
- Imágenes extraídas del diseño original, optimizadas a WebP

Sustituye al sitio WordPress anterior (ferba.net), dado de baja por compromiso
de seguridad.
    """,
    'depends': [
        'website',
        'http_routing',     # la vista 404 (http_routing.404) se hereda directamente
        'mail',             # el formulario de contacto escribe en mail.mail
    ],
    'data': [
        'security/ferba_grupos.xml',    # el grupo, antes del ACL que lo referencia
        'security/ir.model.access.csv',
        'views/ferba_evento_views.xml',
        'views/ferba_inicio.xml',
        'views/ferba_header.xml',
        'views/ferba_footer.xml',
        'views/ferba_paginas.xml',      # andamio (hero, cierre, 404)
        'views/ferba_quienes_somos.xml',
        'views/ferba_soluciones.xml',
        'views/ferba_maquinaria.xml',
        'views/ferba_flexquality.xml',
        'views/ferba_frutos.xml',
        'views/ferba_proyectos.xml',
        'views/ferba_postventa.xml',
        'views/ferba_contacto.xml',
        'views/ferba_legales.xml',      # aviso de privacidad y términos
        'views/ferba_catalogo.xml',     # plantillas del catálogo (categoría, ficha, fruto)
        'views/ferba_catalogo_backend.xml',  # gestión en el backend
        'data/pages.xml',               # los registros, después de las plantillas que referencian
        'data/catalogo_empaque.xml',    # contenido M1: Empaque + Flowpack
        'data/catalogo_maquinaria.xml', # contenido M2: 5 categorias + Linea de siembra
        'data/catalogo_frutos.xml',     # contenido M3: 10 frutos + M2M fruto<->maquina
        'data/catalogo_fichas.xml',     # fichas detalladas de equipo (por lotes)
        'data/catalogo_iconos_menu.xml',# iconos del menú por máquina (campo menu_icono), entregados por el cliente
    ],
    'assets': {
        'web.assets_frontend': [
            'ferba_website/static/src/scss/ferba_fonts.scss',       # @font-face primero
            'ferba_website/static/src/scss/ferba_variables.scss',
            'ferba_website/static/src/scss/ferba_website.scss',
            'ferba_website/static/src/scss/ferba_paginas.scss',      # componentes de las interiores
            'ferba_website/static/src/scss/ferba_catalogo.scss',     # catálogo: tarjetas, fichas, frutos
            'ferba_website/static/src/js/ferba_reveal.js',
            'ferba_website/static/src/js/ferba_analitica.js',
        ],
    },
    # Instalación limpia: quita la portada y las páginas de fábrica de Odoo y
    # deja la de Ferba en '/'. En bases ya instaladas lo hacen las
    # migraciones 19.0.1.5.0 y 19.0.1.6.0.
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
