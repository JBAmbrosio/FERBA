from . import models
from . import controllers


def post_init_hook(env):
    """Liga los renglones de uso YA EXISTENTES a su sitio del catalogo.

    Sin esto, clasificar 'youtube.com' no movería una sola hora de las que ya
    estaban en la base: esos renglones tienen `host` pero no `site_id`, asi que
    la cadena de recalculo nunca los alcanza. El agente reescribe el dia en
    curso cada 5 min, de modo que solo los dias anteriores necesitan este pase.
    """
    Usage = env['foco.usage'].sudo()
    Site = env['foco.site'].sudo()
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
