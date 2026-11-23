# -*- coding: utf-8 -*-
"""Checador = una tableta dada de alta para marcar asistencia.

La tableta NO usa un usuario de Odoo ni una direccion publica: se da de alta una
vez con un codigo de un solo uso y recibe una credencial propia. El servidor
solo guarda la huella (sha256) de esa credencial, asi que ni leyendo la base se
puede suplantar a la tableta.
"""
import hashlib
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

MINUTOS_CODIGO = 15
MINUTOS_EN_LINEA = 15


def huella(token):
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


class FerbaChecador(models.Model):
    _name = 'ferba.checador'
    _description = 'Checador (tableta)'
    _inherit = ['mail.thread']
    _order = 'name'

    name = fields.Char(string='Nombre', required=True, tracking=True,
                       help='Por ejemplo: Checador entrada planta.')
    company_id = fields.Many2one(
        'res.company', string='Empresa', required=True, tracking=True,
        default=lambda self: self.env.company,
        help='La tableta muestra a los empleados de esta empresa.')
    ubicacion = fields.Char(string='Ubicacion')
    estado = fields.Selection(
        [('pendiente', 'Pendiente de alta'),
         ('activo', 'Activo'),
         ('revocado', 'Revocado')],
        string='Estado', default='pendiente', required=True, tracking=True, copy=False)

    # --- alta ---
    codigo_alta = fields.Char(string='Codigo de alta', copy=False, readonly=True)
    codigo_vence = fields.Datetime(string='El codigo vence', copy=False, readonly=True)
    token_hash = fields.Char(copy=False, readonly=True, groups='base.group_system')
    alta_el = fields.Datetime(string='Dada de alta el', readonly=True, copy=False)
    alta_por = fields.Many2one('res.users', string='Codigo generado por',
                               readonly=True, copy=False)

    # --- reglas (por checador) ---
    max_fallos = fields.Integer(
        string='NIP fallidos antes de bloquear', default=5,
        help='Intentos fallidos seguidos de un mismo empleado antes de bloquear su tarjeta.')
    minutos_bloqueo = fields.Integer(string='Minutos de bloqueo', default=5)
    segundos_doble_marca = fields.Integer(
        string='Segundos entre marcas', default=120,
        help='Una segunda marca del mismo empleado antes de este tiempo se rechaza '
             '(evita la doble marca por tocar dos veces).')
    exige_foto = fields.Boolean(
        string='Tomar foto en cada marca', default=True,
        help='Si la camara falla, la marca se acepta igual y queda senalada "sin foto".')
    segundos_regreso = fields.Integer(
        string='Segundos de la confirmacion', default=5,
        help='Cuanto dura en pantalla la confirmacion antes de volver a la lista.')
    permite_offline = fields.Boolean(
        string='Marcar sin conexion', default=True,
        help='Si se cae la red, la tableta valida el NIP con una huella que recibio en la '
             'ultima sincronizacion, guarda la marca y la envia al volver la conexion.')
    horas_offline = fields.Integer(
        string='Horas maximas sin conexion', default=72,
        help='Una marca guardada que llegue mas tarde que esto no se convierte en asistencia; '
             'queda en la bitacora para que Recursos Humanos la revise.')
    pin_mantenimiento = fields.Char(
        string='NIP de mantenimiento', copy=False,
        help='Lo pide la tableta para salir del modo checador (solo IT).')

    # --- senal ---
    ultima_senal = fields.Datetime(string='Ultima senal', readonly=True, copy=False)
    en_linea = fields.Boolean(string='En linea', compute='_compute_en_linea')
    version_app = fields.Char(string='Version de la app', readonly=True, copy=False)
    modelo = fields.Char(string='Modelo', readonly=True, copy=False)
    android = fields.Char(string='Android', readonly=True, copy=False)
    bateria = fields.Integer(string='Bateria %', readonly=True, copy=False)
    cargando = fields.Boolean(string='Cargando', readonly=True, copy=False)
    ip = fields.Char(string='IP', readonly=True, copy=False)
    actualizacion = fields.Char(
        string='Actualizacion de la app', readonly=True, copy=False,
        help='Lo que reporta la tableta: descargando, lista para instalar, o por que no pudo.')

    evento_count = fields.Integer(compute='_compute_evento_count')

    @api.depends('ultima_senal', 'estado')
    def _compute_en_linea(self):
        limite = fields.Datetime.now() - timedelta(minutes=MINUTOS_EN_LINEA)
        for c in self:
            c.en_linea = bool(c.estado == 'activo' and c.ultima_senal
                              and c.ultima_senal >= limite)

    def _compute_evento_count(self):
        datos = self.env['ferba.checador.evento']._read_group(
            [('checador_id', 'in', self.ids)], ['checador_id'], ['__count'])
        mapa = {c.id: n for c, n in datos}
        for c in self:
            c.evento_count = mapa.get(c.id, 0)

    # ------------------------------------------------------------------
    # Alta y revocacion
    # ------------------------------------------------------------------
    def action_generar_codigo(self):
        """Genera el codigo de un solo uso que se teclea en la tableta. Dar un
        codigo nuevo a un checador ya activo DESCONECTA a la tableta anterior."""
        for c in self:
            c.write({
                'codigo_alta': '%08d' % secrets.randbelow(10 ** 8),
                'codigo_vence': fields.Datetime.now() + timedelta(minutes=MINUTOS_CODIGO),
                'alta_por': self.env.user.id,
            })
        return True

    def action_revocar(self):
        self.write({'estado': 'revocado', 'codigo_alta': False, 'codigo_vence': False})
        self.sudo().write({'token_hash': False})
        return True

    @api.model
    def _dar_de_alta(self, codigo, info):
        """Canjea el codigo por una credencial. Devuelve (checador, token) o
        (vacio, None). El codigo sirve UNA vez."""
        codigo = (codigo or '').strip()
        if len(codigo) != 8 or not codigo.isdigit():
            return self.browse(), None
        c = self.sudo().search([
            ('codigo_alta', '=', codigo),
            ('codigo_vence', '>=', fields.Datetime.now()),
        ], limit=1)
        if not c:
            return self.browse(), None
        token = secrets.token_urlsafe(32)
        c.write({
            'token_hash': huella(token),
            'estado': 'activo',
            'codigo_alta': False,
            'codigo_vence': False,
            'alta_el': fields.Datetime.now(),
            'ultima_senal': fields.Datetime.now(),
            **self._vals_senal(info),
        })
        c.message_post(body='Tableta dada de alta: %s (Android %s).' % (
            c.modelo or 'modelo desconocido', c.android or '?'))
        return c, token

    @api.model
    def _por_token(self, token):
        if not token:
            return self.browse()
        return self.sudo().search([
            ('token_hash', '=', huella(token)), ('estado', '=', 'activo')], limit=1)

    @api.model
    def _vals_senal(self, info):
        info = info or {}
        vals = {}
        for campo, largo in (('version_app', 32), ('modelo', 64), ('android', 16), ('ip', 64)):
            if info.get(campo):
                vals[campo] = str(info[campo])[:largo]
        if isinstance(info.get('bateria'), int) and 0 <= info['bateria'] <= 100:
            vals['bateria'] = info['bateria']
        if 'actualizacion' in info:
            vals['actualizacion'] = str(info['actualizacion'] or '')[:120] or False
        if 'cargando' in info:
            vals['cargando'] = bool(info['cargando'])
        return vals

    def _registrar_senal(self, info):
        self.ensure_one()
        self.write({'ultima_senal': fields.Datetime.now(), **self._vals_senal(info)})

    def action_ver_eventos(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Bitacora de %s' % self.name,
            'res_model': 'ferba.checador.evento',
            'view_mode': 'list,form',
            'domain': [('checador_id', '=', self.id)],
        }

    @api.constrains('max_fallos', 'minutos_bloqueo', 'segundos_doble_marca', 'segundos_regreso',
                    'horas_offline')
    def _check_reglas(self):
        for c in self:
            if c.max_fallos < 1 or c.minutos_bloqueo < 1:
                raise UserError('El tope de intentos y los minutos de bloqueo deben ser de al menos 1.')
            if c.horas_offline < 1:
                raise UserError('Las horas maximas sin conexion deben ser de al menos 1.')
            if c.segundos_doble_marca < 0 or c.segundos_regreso < 1:
                raise UserError('Revisa los segundos entre marcas y los de la confirmacion.')
