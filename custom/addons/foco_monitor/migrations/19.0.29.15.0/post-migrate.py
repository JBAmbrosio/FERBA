"""Foco por empresa (15-oct-2026): lo que existia antes es de la empresa PRINCIPAL.

La columna `company_id` nueva de foco.settings y foco.policy ya se llena con la
empresa del superusuario al crearse; esto lo deja explicito -la principal, por
su xmlid- por si el superusuario tuviera otra. Antes de esto habia UNA sola
configuracion y cinco perfiles, todos de Industrias.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("SELECT res_id FROM ir_model_data WHERE module = 'base' AND name = 'main_company'")
    fila = cr.fetchone()
    principal = fila[0] if fila else 1
    for tabla in ('foco_settings', 'foco_policy'):
        cr.execute('UPDATE %s SET company_id = %%s WHERE company_id IS NULL' % tabla, (principal,))
    # La configuracion que existia (la mas antigua) es la de la principal, aunque
    # la columna la hubiera llenado con otra empresa.
    cr.execute("""
        UPDATE foco_settings SET company_id = %s
         WHERE id = (SELECT MIN(id) FROM foco_settings)
           AND NOT EXISTS (SELECT 1 FROM foco_settings WHERE company_id = %s)
    """, (principal, principal))
