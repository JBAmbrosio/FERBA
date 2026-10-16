"""Entrar a la app oficial de Odoo desde el celular sin teclear contraseña.

La app de Odoo no acepta que otra app le pase la direccion ni el usuario (su
unica liga es la de los correos), asi que Foco la llena con su robot de
accesibilidad. En vez de la contraseña escribe un CODIGO DE UN SOLO USO que
Odoo le da al telefono de la empresa, autenticado con su llave, y solo para el
usuario ligado a ese telefono (16-oct-2026, pedido del usuario: el telefono es
del vendedor y el responde por su uso).

Nunca se guarda ni se transmite la contraseña de nadie. El codigo dura
VIGENCIA_S, sirve una sola vez, solo se acepta en la pagina de acceso web
(/web/login, no por API) y se guarda solo su huella (sha256). Por defecto no
aplica a administradores de Odoo; se enciende con el parametro del sistema
`foco.acceso_movil_admins`.
"""

import hashlib
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

PREFIJO = 'foco1.'
VIGENCIA_S = 120
TOPE_10_MIN = 6


class FocoMobileLogin(models.Model):
    _name = 'foco.mobile.login'
    _description = 'Acceso a Odoo desde el celular (código de un solo uso)'
    _order = 'id desc'

    user_id = fields.Many2one('res.users', string='Usuario', required=True, ondelete='cascade', index=True)
    device_id = fields.Many2one('foco.mobile.device', string='Teléfono', ondelete='set null', index=True)
    code_hash = fields.Char(required=True, index=True)
    expires_at = fields.Datetime(string='Vence', required=True)
    used_at = fields.Datetime(string='Usado')

    @api.model
    def _huella(self, codigo):
        return hashlib.sha256(codigo.encode('utf-8')).hexdigest()

    @api.model
    def emitir(self, device, user):
        """Un codigo nuevo para [user] desde [device]; devuelve el codigo en
        claro (una sola vez, en la respuesta al telefono)."""
        Log = self.sudo()
        ahora = fields.Datetime.now()
        if Log.search_count([('device_id', '=', device.id),
                             ('create_date', '>=', ahora - timedelta(minutes=10))]) >= TOPE_10_MIN:
            raise UserError('Ya se pidieron varios accesos a Odoo desde este teléfono. '
                            'Espera unos minutos y vuelve a intentar.')
        Log.search([('expires_at', '<', ahora - timedelta(days=30))]).unlink()
        codigo = PREFIJO + secrets.token_urlsafe(18)
        Log.create({'user_id': user.id, 'device_id': device.id,
                    'code_hash': self._huella(codigo),
                    'expires_at': ahora + timedelta(seconds=VIGENCIA_S)})
        return codigo

    @api.model
    def canjear(self, user, codigo):
        """True si [codigo] es un codigo vigente y sin usar de [user]; lo marca
        usado en el acto."""
        if not codigo or not codigo.startswith(PREFIJO):
            return False
        ahora = fields.Datetime.now()
        r = self.sudo().search([('user_id', '=', user.id), ('code_hash', '=', self._huella(codigo)),
                                ('used_at', '=', False), ('expires_at', '>=', ahora)], limit=1)
        if not r:
            return False
        r.used_at = ahora
        return True
