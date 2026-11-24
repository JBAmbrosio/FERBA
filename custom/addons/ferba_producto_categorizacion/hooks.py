import logging

_logger = logging.getLogger(__name__)

PARAM_RAIZ = 'ferba_producto_categorizacion.categ_raiz_id'
NOMBRE_RAIZ = 'CATEGORIAS CONTABLES'


def post_init_hook(env):
    """La raiz de las categorias contables es la que ya existe (id 78 en FERBA, creada el 23-sep para
    Francisco). Los productos que ya existian quedan "Categorizado" por el valor por defecto de la
    columna: no se oculta nada. La opcion queda APAGADA en todas las empresas; se enciende a mano."""
    ICP = env['ir.config_parameter'].sudo()
    if not ICP.get_param(PARAM_RAIZ):
        raiz = env['product.category'].search(
            [('name', '=', NOMBRE_RAIZ), ('parent_id', '=', False)], limit=1)
        if raiz:
            ICP.set_param(PARAM_RAIZ, str(raiz.id))
            _logger.info('ferba_producto_categorizacion: raiz de categorias contables = %s (%s)',
                         raiz.display_name, raiz.id)


def uninstall_hook(env):
    """Al desinstalar no deben quedar productos escondidos: los que esperaban categoria se reactivan
    (quedan como productos normales). Los rechazados siguen archivados."""
    pendientes = env['product.template'].with_context(
        active_test=False, ferba_sin_categorizacion=True).search(
        [('ferba_categorizacion', '=', 'pendiente')])
    if pendientes:
        # active_test=True para que Odoo reactive tambien las variantes (ver _ferba_categorizar).
        pendientes.with_context(active_test=True).write({'active': True})
        _logger.info('ferba_producto_categorizacion: %d productos por categorizar reactivados al desinstalar',
                     len(pendientes))
