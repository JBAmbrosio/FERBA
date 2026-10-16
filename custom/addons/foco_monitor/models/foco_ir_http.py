"""Abrir la app oficial de Odoo directo en una cotizacion (16-oct-2026).

La app de Odoo no deja que otra app le diga que pagina abrir (ignora cualquier
liga que no sea de sus correos; medido en el moto g17), asi que el salto lo da
Odoo: Foco avisa "el vendedor quiere ver la S0xxxx" (res.users.foco_ir_a), abre
la app desde cero, y la pantalla de Odoo, al arrancar DENTRO de la app (su
navegador se anuncia como "OdooMobile/"), ve el aviso fresco y salta ahi
(static/src/movil/foco_ir_a.js), y lo borra.
"""

from datetime import timedelta

from odoo import fields, models
from odoo.http import request

VIGENCIA_IR_A_S = 90
UA_APP_ODOO = 'OdooMobile/'


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        info = super().session_info()
        try:
            ua = request.httprequest.user_agent.string if request else ''
            if UA_APP_ODOO in (ua or '') and request.session.uid:
                u = self.env.user.sudo()
                if (u.foco_ir_a and u.foco_ir_a_at
                        and u.foco_ir_a_at >= fields.Datetime.now() - timedelta(seconds=VIGENCIA_IR_A_S)):
                    info['foco_ir_a'] = u.foco_ir_a
        except Exception:
            pass
        return info
