import logging

_logger = logging.getLogger(__name__)

# Correo del director que pidio el flujo (reunion del 25-sep-2026). Se da de alta
# como aprobador al instalar para que el flujo arranque con alguien; despues se
# administra desde Ventas > Configuracion > Ajustes > Aprobacion de cotizaciones.
APROBADOR_INICIAL = 'F.Iaquinta@ferba.net'


def post_init_hook(env):
    """Lo que ya salio antes del modulo no se bloquea.

    Una cotizacion enviada, confirmada o cancelada antes de instalar ya paso por
    las manos de alguien: se marca aprobada sin dejar rastro en el chatter (miles
    de registros, un mensaje cada uno no es informacion). Las que siguen en
    borrador si entran al flujo: es justo lo que pidio el director.
    """
    Order = env['sale.order'].with_context(tracking_disable=True, mail_notrack=True,
                                           ferba_sin_reset=True)
    viejas = Order.search([('state', 'in', ('sent', 'sale', 'cancel'))])
    viejas.write({'aprobacion_state': 'aprobada'})
    _logger.info('ferba_aprobacion_cotizaciones: %d cotizaciones previas marcadas aprobadas', len(viejas))

    usuario = env['res.users'].search([('login', '=ilike', APROBADOR_INICIAL)], limit=1)
    if usuario:
        grupo = env.ref('ferba_aprobacion_cotizaciones.group_aprobador_cotizaciones')
        grupo.sudo().write({'user_ids': [(4, usuario.id)]})
        _logger.info('ferba_aprobacion_cotizaciones: %s dado de alta como aprobador', usuario.login)
