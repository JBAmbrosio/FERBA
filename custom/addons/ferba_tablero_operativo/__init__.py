from . import models


def _post_init_visibilidad(env):
    """Default al INSTALAR: que todos los usuarios internos vean el tablero.

    Se hace aqui y no en XML porque `base.group_user` tiene noupdate=true y
    Odoo se salta las modificaciones por dato a ese registro. Al ser hook de
    instalacion, NO corre en upgrades, asi que si el admin cambia a "solo
    seleccionados", esa eleccion se conserva al actualizar el modulo.
    """
    grupo = env.ref('ferba_tablero_operativo.group_ferba_operativo_user',
                    raise_if_not_found=False)
    g_user = env.ref('base.group_user', raise_if_not_found=False)
    if grupo and g_user and grupo not in g_user.implied_ids:
        g_user.write({'implied_ids': [(4, grupo.id)]})
