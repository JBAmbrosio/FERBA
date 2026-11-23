# -*- coding: utf-8 -*-
import base64
import hashlib
import secrets

from odoo import api, fields, models

# Huella del NIP que se manda a las tabletas para validarlo sin red.
# PBKDF2-HMAC-SHA256 con sal propia por empleado. Un NIP de 4 digitos se puede
# adivinar a fuerza bruta teniendo la huella, asi que SOLO se entrega a tabletas
# dadas de alta y con el modo sin conexion encendido.
HUELLA_VUELTAS = 120_000


def huella_nip(nip, sal=None, vueltas=HUELLA_VUELTAS):
    sal = sal or secrets.token_bytes(16)
    resumen = hashlib.pbkdf2_hmac('sha256', str(nip).encode(), sal, vueltas)
    return 'v1$%d$%s$%s' % (
        vueltas, base64.b64encode(sal).decode(), base64.b64encode(resumen).decode())


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # Contador de NIP fallidos seguidos en el checador y, si se paso del tope,
    # hasta cuando queda bloqueada su tarjeta. Solo lo ve RH.
    checador_fallos = fields.Integer(
        string='NIP fallidos seguidos', default=0, copy=False,
        groups='hr.group_hr_user')
    checador_bloqueado_hasta = fields.Datetime(
        string='Checador bloqueado hasta', copy=False,
        groups='hr.group_hr_user')
    checador_nip_huella = fields.Char(
        string='Huella del NIP (checador)', compute='_compute_checador_nip_huella',
        store=True, copy=False, groups='base.group_system')

    @api.depends('pin')
    def _compute_checador_nip_huella(self):
        for emp in self:
            emp.checador_nip_huella = huella_nip(emp.pin) if emp.pin else False

    def action_checador_desbloquear(self):
        self.sudo().write({'checador_fallos': 0, 'checador_bloqueado_hasta': False})
