from . import models


def post_init_hook(env):
    """Activa la opción en la empresa principal (en FERBA: INDUSTRIAS TECNOLOGICAS EMP)."""
    company = env.ref('base.main_company', raise_if_not_found=False)
    if company:
        company.ferba_compra_precio_manual = True
