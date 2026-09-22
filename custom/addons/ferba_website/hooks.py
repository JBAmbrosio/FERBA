# -*- coding: utf-8 -*-
"""Portada del sitio.

La página de Ferba se sirve en '/' y la portada de fábrica de Odoo (la
página «Home» que crea el módulo website) se elimina. La misma función se
ejecuta en dos momentos distintos:

- post_init_hook: instalación limpia. pages.xml ya crea la página de Ferba
  en '/', pero la «Home» de Odoo sigue existiendo y hay que quitarla.
- migrations/19.0.1.5.0/post-migrate.py: bases que ya tenían el módulo con
  la portada en /inicio-ferba. pages.xml es noupdate="1" y no toca registros
  existentes, así que el cambio de URL tiene que hacerse por código, una vez.

Es idempotente: ejecutarla de nuevo no produce nada distinto a lo descrito.

retirar_paginas_de_fabrica() hace lo mismo con el resto de lo que Odoo
publica por su cuenta y no es de Ferba: las páginas /contactus y
/contactus-thank-you (se eliminan, como la Home) y los controladores /terms,
/website/info y /jobs (no son páginas: se retiran con la regla «404» de
website.rewrite, la misma que ofrece el backend en Sitio web > Redirecciones).
/contactus, además, redirige al contacto de Ferba: la cabecera del tema de
Odoo lleva un botón «Contáctenos» fijo a esa URL que se ve en las páginas que
no son de Ferba (la de acceso, por ejemplo), y sin la redirección acabaría en
un 404. Corre al instalar y en las migraciones 19.0.1.6.0 y 19.0.1.6.1.

aplicar_indexacion() enciende website_indexed en las 9 páginas de contenido y
les pone título y descripción para el buscador; las dos legales siguen fuera
mientras sean andamio. Corre al instalar y en la migración 19.0.1.7.0.

Si una actualización futura del módulo website volviera a crear /contactus
(su dato es noupdate con forcecreate y al borrar el registro desaparece su
ir.model.data), la página ganaría a la redirección: basta con volver a
actualizar este módulo para que el hook la retire de nuevo.
"""
import logging

_logger = logging.getLogger(__name__)


def _purgar_sitemap(env):
    """El sitemap se sirve desde un adjunto que solo caduca a las 12 h."""
    adjuntos = env['ir.attachment'].sudo().search([
        ('type', '=', 'binary'), ('url', '=like', '/sitemap-%.xml')])
    if adjuntos:
        adjuntos.unlink()

URL_ANTERIOR = '/inicio-ferba'


def fijar_portada(env):
    Page = env['website.page'].sudo()
    inicio = env.ref('ferba_website.page_ferba_inicio', raise_if_not_found=False)
    if not inicio:
        _logger.warning("ferba_website: no existe page_ferba_inicio; la portada no se toca")
        return
    inicio = inicio.sudo()
    website = (env.ref('website.default_website', raise_if_not_found=False)
               or env['website'].sudo().search([], limit=1)).sudo()

    # 1) Fuera las otras páginas en '/': la «Home» de fábrica y cualquier
    #    copia que el constructor haya creado al editarla (copy-on-write).
    #    unlink() de website.page borra también la vista si nadie más la usa.
    #    Va ANTES de mover la de Ferba: write() aplica get_unique_path() y con
    #    otra página en '/' la nuestra acabaría en '/-1'.
    # active_test=False: website.page hereda `active` de su ir.ui.view y una
    # «Home» archivada seguiría ocupando '/' para get_unique_path().
    otras = Page.with_context(active_test=False).search([('url', '=', '/'), ('id', '!=', inicio.id)])
    if otras:
        _logger.info("ferba_website: se eliminan %d página(s) en '/': %s",
                     len(otras), ', '.join(otras.mapped('name')))
        otras.unlink()

    # 2) La página de Ferba, en '/' y ligada al sitio. Una página específica
    #    del sitio gana a una genérica con la misma URL (_get_page_info ordena
    #    por website_id): si una actualización del módulo website volviera a
    #    crear la «Home» (su dato es noupdate con forcecreate), la portada
    #    seguiría siendo la de Ferba.
    vals = {}
    if inicio.url != '/':
        vals['url'] = '/'
    if website and inicio.website_id != website:
        vals['website_id'] = website.id
    if vals:
        inicio.with_context(website_id=website.id if website else False).write(vals)
        _logger.info("ferba_website: portada -> %s", vals)

    # 3) homepage_url vacío: Odoo sirve entonces la página cuya URL es '/'.
    #    Si apuntara a /inicio-ferba, '/' se reencaminaría a /inicio-ferba y
    #    la redirección de abajo lo devolvería a '/': bucle.
    if website and website.homepage_url and website.homepage_url.rstrip('/') in ('', URL_ANTERIOR):
        website.write({'homepage_url': False})

    # 4) Redirección permanente de la URL vieja: para los enlaces que ya
    #    circulen y para que el buscador traspase lo que tuviera indexado.
    Rewrite = env['website.rewrite'].sudo()
    if not Rewrite.search([('url_from', '=', URL_ANTERIOR),
                           ('redirect_type', 'in', ('301', '302', '308'))], limit=1):
        Rewrite.create({
            'name': 'Ferba: /inicio-ferba -> / (portada)',
            'url_from': URL_ANTERIOR,
            'url_to': '/',
            'redirect_type': '301',
            'website_id': website.id if website else False,
        })
        _logger.info("ferba_website: redirección 301 %s -> /", URL_ANTERIOR)


# Páginas de fábrica de Odoo que no forman parte del sitio de Ferba.
PAGINAS_FUERA = ('/contactus', '/contactus-thank-you')
# Controladores de fábrica que se retiran con una regla 404. La clave es la
# ruta EXACTA de la regla de ruteo (ir.http._generate_routing_rules compara
# url_from con la ruta declarada en el @route, parámetros incluidos).
# Rutas retiradas que SÍ tienen equivalente en Ferba: en vez de un 404, un 301.
REDIRECCIONES = (
    ('/contactus', '/contactenos'),
)
RUTAS_FUERA = (
    '/terms',                                   # account: términos de facturación
    '/website/info',                            # website: ficha de la instancia
    '/jobs',                                    # website_hr_recruitment
    '/jobs/page/<int:page>',
    '/jobs/detail/<model("hr.job"):job>',
    '/jobs/<model("hr.job"):job>',
    '/jobs/apply/<model("hr.job"):job>',
)


def retirar_paginas_de_fabrica(env):
    website = (env.ref('website.default_website', raise_if_not_found=False)
               or env['website'].sudo().search([], limit=1)).sudo()

    # 1) Páginas: se eliminan (con su vista, si nadie más la usa). active_test
    #    False por lo mismo que en fijar_portada.
    paginas = env['website.page'].sudo().with_context(active_test=False).search([('url', 'in', PAGINAS_FUERA)])
    if paginas:
        _logger.info("ferba_website: se eliminan %d página(s) de fábrica: %s",
                     len(paginas), ', '.join(paginas.mapped('url')))
        paginas.unlink()

    # 2) Menús de la cabecera de fábrica de Odoo que apuntaban a lo retirado
    #    («Contáctenos», «Empleos»). La cabecera de Ferba no los usa, pero la
    #    de Odoo sigue saliendo en páginas que no son de Ferba y mostraría
    #    enlaces a un 404.
    menus = env['website.menu'].sudo().search([('url', 'in', PAGINAS_FUERA + ('/jobs', '/terms', '/website/info'))])
    if menus:
        _logger.info("ferba_website: se eliminan %d menú(s): %s", len(menus), ', '.join(menus.mapped('name')))
        menus.unlink()

    # 3) Controladores: regla 404 por ruta. website.rewrite limpia la caché
    #    de ruteo al crearse; la ruta desaparece también del sitemap.
    Rewrite = env['website.rewrite'].sudo()
    hechas = set(Rewrite.search([('url_from', 'in', RUTAS_FUERA), ('redirect_type', '=', '404')]).mapped('url_from'))
    nuevas = [{
        'name': 'Ferba: fuera del sitio %s' % ruta,
        'url_from': ruta,
        'redirect_type': '404',
        'website_id': website.id if website else False,
    } for ruta in RUTAS_FUERA if ruta not in hechas]
    if nuevas:
        Rewrite.create(nuevas)
        _logger.info("ferba_website: %d regla(s) 404: %s", len(nuevas), ', '.join(v['url_from'] for v in nuevas))

    # 3b) Lo retirado que tiene equivalente en Ferba: 301 en vez de 404. Va
    #     después de borrar la página: mientras exista una website.page con
    #     esa URL, _serve_page la sirve antes de mirar las redirecciones.
    for origen, destino in REDIRECCIONES:
        if Rewrite.search([('url_from', '=', origen), ('redirect_type', 'in', ('301', '302', '308'))], limit=1):
            continue
        Rewrite.create({
            'name': 'Ferba: %s -> %s' % (origen, destino),
            'url_from': origen,
            'url_to': destino,
            'redirect_type': '301',
            'website_id': website.id if website else False,
        })
        _logger.info("ferba_website: redirección 301 %s -> %s", origen, destino)

    # 4) El sitemap se guarda como adjunto y solo se regenera cada 12 h: se
    #    purga para que deje de anunciar lo retirado desde ya.
    _purgar_sitemap(env)


# Páginas que se indexan, con el título y la descripción que verá el buscador.
# El título SUSTITUYE al de fábrica («Ferba — Inicio | Ferba», que repetía la
# marca dos veces): si website_meta_title está puesto, website.layout lo usa
# tal cual y ya no añade « | <nombre del sitio>».
# El texto sale de lo que cada página ya dice; no se inventa nada.
SEO = {
    'page_ferba_inicio': (
        'Maquinaria y líneas postcosecha para empacadoras | FERBA',
        'Diseñamos, fabricamos e integramos maquinaria y automatización para '
        'recepción, lavado, selección y empaque de frutas y hortalizas.'),
    'page_quienes_somos': (
        'Quiénes somos | FERBA Postharvest Technology',
        'Diseño, fabricación e integración de maquinaria agroindustrial postcosecha. '
        'Raíces españolas, más de 20 años y presencia en Estados Unidos, España, México y Perú.'),
    'page_soluciones': (
        'Soluciones postcosecha a la medida de tu operación | FERBA',
        'Líneas completas, modernización de equipos existentes, automatización y proyectos '
        'llave en mano, adaptados al producto, la capacidad y el espacio de cada cliente.'),
    'page_maquinaria': (
        'Maquinaria postcosecha por etapa del proceso | FERBA',
        'Equipos para recepción, lavado, secado y cepillado, transporte, selección y '
        'calibrado, empaque, automatización y sembradoras.'),
    'page_flexquality': (
        'FlexQuality: selección con visión artificial e IA | FERBA',
        'FlexQuality analiza cada fruto en tiempo real (color, tamaño, forma, calidad y '
        'defectos) y se integra en líneas nuevas o en calibradores existentes.'),
    'page_frutos': (
        'Soluciones postcosecha por tipo de fruto | FERBA',
        'Tomate, pepino, pimiento, calabaza, mango, melón, aguacate, cítricos, espárrago y '
        'sandía: maquinaria y líneas adaptadas a cada producto.'),
    'page_proyectos': (
        'Proyectos e instalaciones postcosecha | FERBA',
        'Líneas, equipos e integraciones desarrolladas por FERBA para empacadoras que buscan '
        'mejorar la eficiencia, la calidad y el control de sus procesos postcosecha.'),
    'page_postventa': (
        'Soporte postventa, mantenimiento y refacciones | FERBA',
        'Soporte técnico, mantenimiento, capacitación y refacciones para que tu línea '
        'postcosecha opere con continuidad y al máximo rendimiento.'),
    'page_contacto': (
        'Contacto | FERBA Postharvest Technology',
        'Cuéntanos qué producto procesas, qué capacidad necesitas y qué etapa quieres mejorar. '
        'Culiacán, Sinaloa. Teléfono +52 667 121 7267, ventas@ferba.net.'),
}
# Fuera del buscador a propósito: son el andamio «Estamos terminando esta
# sección». Se indexan cuando tengan el texto legal de verdad.
SIN_INDEXAR = ('page_privacidad', 'page_terminos')


def aplicar_indexacion(env):
    """website_indexed a True en las 9 páginas de contenido, con su título y
    descripción. La descripción y el título solo se escriben si están vacíos:
    si alguien los ajusta desde el backend (Sitio web > Optimizar SEO), una
    actualización posterior no se los pisa."""
    website = (env.ref('website.default_website', raise_if_not_found=False)
               or env['website'].sudo().search([], limit=1)).sudo()
    # Los campos meta son translate=True: se escriben en el idioma por defecto
    # del sitio, que es el que ve quien entra sin prefijo de idioma. Con el
    # contexto vacío se escribirían en en_US y el buscador leería la ficha en
    # el idioma equivocado.
    idioma = website.default_lang_id.code if website and website.default_lang_id else None
    ctx = {'lang': idioma} if idioma else {}

    for xmlid, (titulo, descripcion) in SEO.items():
        pagina = env.ref('ferba_website.' + xmlid, raise_if_not_found=False)
        if not pagina:
            _logger.warning("ferba_website: no existe %s; no se indexa", xmlid)
            continue
        pagina = pagina.sudo().with_context(**ctx)
        vals = {}
        if not pagina.website_indexed:
            vals['website_indexed'] = True
        if not pagina.website_meta_title:
            vals['website_meta_title'] = titulo
        if not pagina.website_meta_description:
            vals['website_meta_description'] = descripcion
        if vals:
            pagina.write(vals)
            _logger.info("ferba_website: %s -> %s", xmlid, sorted(vals))

    for xmlid in SIN_INDEXAR:
        pagina = env.ref('ferba_website.' + xmlid, raise_if_not_found=False)
        if pagina and pagina.sudo().website_indexed:
            pagina.sudo().write({'website_indexed': False})
            _logger.info("ferba_website: %s queda sin indexar (en preparación)", xmlid)

    _purgar_sitemap(env)


def post_init_hook(env):
    fijar_portada(env)
    retirar_paginas_de_fabrica(env)
    aplicar_indexacion(env)
