"""Liga el uso YA REGISTRADO con el catalogo de sitios recien creado.

POR QUE HACE FALTA ADEMAS DEL post_init_hook
    `post_init_hook` corre SOLO al instalar el modulo. En una base donde Foco ya
    estaba instalado, actualizar no lo ejecuta, asi que los renglones antiguos
    se quedarian con `host` pero sin `site_id` y clasificar 'youtube.com' no
    movería ni una de las horas que ya estaban guardadas. Eso se ve exactamente
    igual que "la clasificacion no sirve".
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Usage = env['foco.usage']
    Site = env['foco.site']
    filas = Usage.search([('host', '!=', False), ('host', '!=', ''),
                          ('site_id', '=', False)])
    por_host = {}
    for fila in filas:
        host = (fila.host or '').strip().lower()
        if not host:
            continue
        if host not in por_host:
            por_host[host] = Site._get_or_create(host).id
        fila.site_id = por_host[host]
