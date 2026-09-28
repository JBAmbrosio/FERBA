"""Gestion de los celulares de la empresa desde Odoo (Android Enterprise).

QUE DECIDE ODOO Y QUE HACE GOOGLE
    Odoo decide: que perfil tiene cada celular (los MISMOS perfiles de
    navegacion de las laptops), que apps estan aprobadas para el perfil y para
    cada persona, que se bloquea (compartir internet, cuentas personales, apps
    fuera de la Play Store...) y que sitios se bloquean en Chrome. Con eso arma
    UNA politica por celular y se la manda a Google (foco.amapi). Google la
    aplica en el telefono: instala sola la app aprobada, deja la Play Store
    mostrando solo lo aprobado y bloquea lo demas.

    Una politica POR CELULAR y no por perfil: las apps aprobadas a una persona
    (una solicitud) van solo a su telefono. Con pocos telefonos es lo mas
    simple y cada politica cabe en una pantalla.

SOLICITUDES
    La persona pide una app desde Foco en su telefono ("Solicitar una app").
    Llega aqui como foco.mobile.app.request; el administrador la busca en la
    Play Store de la empresa (dentro de Odoo), la aprueba y Odoo la manda
    instalar solo en ese telefono. La respuesta vuelve al telefono en el
    siguiente envio.

ALTA
    Telefono de fabrica -> 6 toques en la pantalla de bienvenida -> escanear el
    QR que genera Odoo. El QR lleva el token de alta de Google con la politica
    del celular; la politica instala Foco y le pasa la direccion de Odoo y su
    codigo de invitacion, asi que Foco se conecta solo, sin teclear nada.
"""

import base64
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

FOCO_PKG = 'net.ferba.foco'
CHROME_PKG = 'com.android.chrome'

PKG_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$')
PLAY_ID_RE = re.compile(r'[?&]id=([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)')

INSTALL_TYPES = [
    ('force', 'Instalar sola'),
    ('available', 'Disponible en su Play Store'),
    ('blocked', 'Bloqueada'),
]
AMAPI_INSTALL = {'force': 'FORCE_INSTALLED', 'available': 'AVAILABLE', 'blocked': 'BLOCKED'}

# Lo que aplica a un celular SIN perfil. Son los mismos valores de fabrica de
# los campos del perfil: un telefono sin perfil no queda abierto, queda como
# un perfil recien creado.
_MOVIL_FABRICA = {
    'modo': 'lista', 'compartir': True, 'cuentas': True, 'sideload': True,
    'reset': True, 'modo_seguro': True, 'depuracion': True, 'usuarios': True,
    'usb': False, 'web': True,
}

ESTADOS_AMAPI = {
    'ACTIVE': 'activo', 'DISABLED': 'deshabilitado', 'DELETED': 'borrado',
    'PROVISIONING': 'aprovisionando', 'LOST': 'perdido',
}


def paquete_de(texto):
    """El paquete Android de un texto: una liga de la Play Store
    (play.google.com/store/apps/details?id=com.x o market://details?id=com.x)
    o el paquete escrito tal cual. '' si no hay."""
    t = (texto or '').strip()
    m = PLAY_ID_RE.search(t)
    if m:
        return m.group(1)
    return t if PKG_RE.match(t) else ''


def _ts(valor):
    """RFC 3339 de Google ('2026-09-28T12:34:56.789Z') -> datetime UTC naive."""
    if not valor:
        return False
    try:
        return datetime.strptime(str(valor)[:19], '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return False


# =============================================================== AJUSTES
class FocoSettingsMdm(models.Model):
    _inherit = 'foco.settings'

    amapi_enterprise = fields.Char(
        string='Empresa en Google', readonly=True, copy=False,
        help='Identificador de la empresa en Android Enterprise '
             '(enterprises/...). Se llena solo al terminar el registro.')
    amapi_enterprise_display = fields.Char(
        string='Nombre de la empresa en Google', default='FERBA')
    amapi_signup_name = fields.Char(readonly=True, copy=False)
    amapi_cuenta = fields.Char(string='Cuenta de servicio', compute='_compute_amapi')
    amapi_estado = fields.Selection([
        ('sin_cuenta', 'Falta la cuenta de servicio de Google'),
        ('sin_empresa', 'Falta registrar la empresa en Google'),
        ('lista', 'Conectada'),
    ], string='Android Enterprise', compute='_compute_amapi')
    amapi_ultima_sync = fields.Datetime(string='Última sincronización', readonly=True)
    amapi_ultimo_error = fields.Char(string='Último error de Google', readonly=True)

    def _compute_amapi(self):
        resumen = self.env['foco.amapi'].resumen_cuenta()
        for s in self:
            s.amapi_cuenta = resumen
            if not resumen:
                s.amapi_estado = 'sin_cuenta'
            elif not s.amapi_enterprise:
                s.amapi_estado = 'sin_empresa'
            else:
                s.amapi_estado = 'lista'

    def _base_url(self):
        return (self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').rstrip('/')

    def action_amapi_cuenta(self):
        return {
            'type': 'ir.actions.act_window', 'name': 'Cuenta de servicio de Google',
            'res_model': 'foco.amapi.cuenta.wizard', 'view_mode': 'form', 'target': 'new',
        }

    def action_amapi_probar(self):
        nombres = self.env['foco.amapi'].probar()
        if nombres:
            msg = 'La cuenta funciona. Empresas de este proyecto en Google: %s.' % ', '.join(nombres)
        else:
            msg = 'La cuenta funciona. El proyecto todavía no tiene empresas: registra la de FERBA.'
        return self._aviso(msg, 'success')

    def action_amapi_registrar(self):
        """Lleva al administrador a la pagina de Google para dar de alta la
        empresa. Al terminar, Google vuelve a /foco/amapi/callback."""
        self.ensure_one()
        base = self._base_url()
        if not base.startswith('https://'):
            raise UserError('La dirección de Odoo (web.base.url) tiene que ser https para registrar la empresa. Hoy es: %s' % base)
        res = self.env['foco.amapi'].url_registro(base + '/foco/amapi/callback')
        self.sudo().amapi_signup_name = res.get('name')
        return {'type': 'ir.actions.act_url', 'url': res.get('url'), 'target': 'self'}

    def action_amapi_play(self):
        return {'type': 'ir.actions.client', 'tag': 'foco_play', 'name': 'Play Store de la empresa'}

    def action_amapi_sync(self):
        n = self.env['foco.mobile.device']._mdm_sync(lanzar=True)
        enviados = self.env['foco.mobile.device'].sudo().search(
            [('amapi_state', 'not in', [False, 'borrado'])])._mdm_enviar()
        return self._aviso('Sincronizado con Google: %d teléfono(s) leídos, %d política(s) enviadas.'
                           % (n, enviados), 'success')

    def action_amapi_alta(self):
        return {
            'type': 'ir.actions.act_window', 'name': 'Alta de un teléfono',
            'res_model': 'foco.mobile.alta.wizard', 'view_mode': 'form', 'target': 'new',
        }

    def _aviso(self, msg, tipo='info'):
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': tipo, 'sticky': False, 'message': msg}}


# =============================================================== PERFIL
class FocoPolicyMovil(models.Model):
    """El perfil de navegacion de las laptops, ahora tambien del celular.

    Las reglas de sitios son LAS MISMAS: en el celular se aplican en Chrome con
    la misma sintaxis (URLBlocklist / URLAllowlist) que ya usa la laptop. Lo que
    se agrega aqui es lo que solo existe en un telefono."""
    _inherit = 'foco.policy'

    mobile_play_mode = fields.Selection([
        ('lista', 'Solo las apps aprobadas'),
        ('abierta', 'Todas, salvo las bloqueadas'),
    ], string='Play Store del celular', default='lista',
        help='Solo las aprobadas: la Play Store del telefono muestra UNICAMENTE '
             'las apps aprobadas para el perfil o para la persona, y cualquier '
             'otra que tuviera instalada se quita. Todas salvo las bloqueadas: la '
             'Play Store queda abierta y solo se impiden las marcadas como '
             'Bloqueada.')
    mobile_block_tethering = fields.Boolean(
        string='Bloquear compartir internet', default=True,
        help='El telefono no comparte sus datos ni su red con otros equipos: ni '
             'punto de acceso Wi-Fi, ni por USB, ni por Bluetooth.')
    mobile_block_accounts = fields.Boolean(
        string='Bloquear cuentas personales', default=True,
        help='No se pueden agregar ni quitar cuentas (Google personal, correo, '
             'redes). El telefono usa la cuenta administrada de la empresa para '
             'la Play Store.')
    mobile_block_sideload = fields.Boolean(
        string='Bloquear apps que no vengan de la Play Store', default=True,
        help='Nadie puede instalar un .apk bajado de internet o pasado por '
             'WhatsApp. Las apps solo llegan por la Play Store de la empresa.')
    mobile_block_factory_reset = fields.Boolean(
        string='Bloquear restablecer de fábrica', default=True,
        help='Desde Ajustes no se puede borrar el telefono para quitarle la '
             'gestion. El administrador si puede, desde Odoo.')
    mobile_block_safe_boot = fields.Boolean(
        string='Bloquear el modo seguro', default=True,
        help='En modo seguro no corren las apps de la empresa; bloquearlo cierra '
             'esa puerta.')
    mobile_block_debug = fields.Boolean(
        string='Bloquear opciones de desarrollador', default=True,
        help='Sin depuracion USB nadie puede manipular el telefono desde una '
             'computadora.')
    mobile_block_add_user = fields.Boolean(
        string='Bloquear agregar usuarios', default=True,
        help='Un segundo usuario en el telefono seria un espacio sin gestion.')
    mobile_block_usb_files = fields.Boolean(
        string='Bloquear pasar archivos por USB', default=False,
        help='Conectado a una computadora, el telefono no deja copiar archivos. '
             'Apagado de fabrica: sirve para bajar fotos de trabajo.')
    mobile_web_filter = fields.Boolean(
        string='Aplicar las reglas de sitios en el celular', default=True,
        help='Las reglas de este perfil (Bloquear / Permitir) se aplican en '
             'Chrome del telefono, igual que en la laptop. Depende tambien del '
             'interruptor general de bloqueo de sitios en Ajustes.')
    mobile_grant_ids = fields.One2many('foco.mobile.grant', 'policy_id', string='Apps del perfil')
    mobile_device_ids = fields.One2many('foco.mobile.device', 'policy_id', string='Celulares')
    mobile_device_count = fields.Integer(compute='_compute_mobile_count')
    mobile_web_nota = fields.Char(compute='_compute_mobile_count')

    @api.depends('mobile_device_ids', 'rule_ids.condicionada', 'rule_ids.active')
    def _compute_mobile_count(self):
        for p in self:
            p.mobile_device_count = len(p.mobile_device_ids)
            cond = len(p.rule_ids.filtered(lambda r: r.active and r.condicionada))
            p.mobile_web_nota = (
                '%d regla(s) con horario o cuota NO aplican en el celular: Chrome '
                'recibe una lista fija. Las demás, sí.' % cond) if cond else ''

    def _mdm_equipos(self):
        """Los celulares que se rigen por estos perfiles, incluidos los que no
        tienen perfil propio si alguno de estos es el perfil por omision."""
        Dev = self.env['foco.mobile.device'].sudo()
        equipos = Dev.search([('policy_id', 'in', self.ids)])
        omision = self.env['foco.settings'].sudo().get_settings().default_policy_id
        if omision and omision in self:
            equipos |= Dev.search([('policy_id', '=', False)])
        return equipos

    def write(self, vals):
        res = super().write(vals)
        self._mdm_equipos()._mdm_programar()
        return res

    def action_play(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'foco_play', 'name': 'Play Store · %s' % self.name,
                'context': {'foco_policy_id': self.id}}

    def action_ver_celulares(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': 'Celulares con el perfil %s' % self.name,
            'res_model': 'foco.mobile.device', 'view_mode': 'list,form',
            'domain': [('policy_id', '=', self.id)],
        }


class FocoPolicyRuleMovil(models.Model):
    _inherit = 'foco.policy.rule'

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.policy_id._mdm_equipos()._mdm_programar()
        return recs

    def write(self, vals):
        res = super().write(vals)
        self.policy_id._mdm_equipos()._mdm_programar()
        return res

    def unlink(self):
        perfiles = self.policy_id
        res = super().unlink()
        perfiles.exists()._mdm_equipos()._mdm_programar()
        return res


# =============================================================== APPS
class FocoMobileAppMdm(models.Model):
    _inherit = 'foco.mobile.app'

    grant_ids = fields.One2many('foco.mobile.grant', 'app_id', string='Aprobaciones')
    sistema = fields.Boolean(
        string='Viene con el teléfono', readonly=True,
        help='Aparece como app del sistema en el inventario de algún teléfono '
             '(YouTube, el navegador del fabricante...). No se puede quitar; si '
             'se puede Bloquear en un perfil.')
    installed_count = fields.Integer(string='Teléfonos que la tienen', compute='_compute_installed_count')

    def _compute_installed_count(self):
        Inst = self.env['foco.mobile.installed'].sudo()
        datos = {pkg: n for pkg, n in Inst._read_group(
            [('package', 'in', self.mapped('package')), ('state', '=', 'instalada')],
            ['package'], ['__count'])}
        for a in self:
            a.installed_count = datos.get(a.package, 0)

    @api.model
    def _asegurar(self, paquete, etiqueta=''):
        """La app del catalogo para ese paquete; la crea si no existe."""
        paquete = (paquete or '').strip()
        if not PKG_RE.match(paquete):
            raise UserError('«%s» no es un paquete de Android válido (algo como com.whatsapp.w4b).' % paquete)
        app = self.sudo().search([('package', '=', paquete)], limit=1)
        if not app:
            ahora = fields.Datetime.now()
            app = self.sudo().create({'package': paquete, 'app_label': etiqueta or '',
                                      'first_seen': ahora, 'last_seen': ahora})
        elif etiqueta and not app.app_label:
            app.app_label = etiqueta
        return app

    @api.model
    def play_ficha(self, contexto=None):
        """Lo que el iframe de la Play Store administrada necesita para abrirse
        dentro de Odoo: el token de un solo uso y la URL. Si falta configurar,
        devuelve el motivo en vez de fallar: la pantalla lo muestra."""
        if not self.env.user.has_group('foco_monitor.group_foco_manager'):
            return {'error': 'Solo los administradores de Foco abren la Play Store de la empresa.'}
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if ajustes.amapi_estado != 'lista':
            return {'error': dict(ajustes._fields['amapi_estado'].selection).get(ajustes.amapi_estado, ''),
                    'estado': ajustes.amapi_estado}
        try:
            token = self.env['foco.amapi'].ficha_play(ajustes._base_url())
        except UserError as e:
            return {'error': str(e)}
        destino = self._play_destino(contexto or {})
        return {
            'url': 'https://play.google.com/work/embedded/search?token=%s&mode=SELECT&locale=es' % token,
            'destino': destino,
        }

    @api.model
    def _play_destino(self, ctx):
        """Para quien es lo que se elija en la Play Store, dicho en una frase."""
        if ctx.get('request_id'):
            req = self.env['foco.mobile.app.request'].browse(int(ctx['request_id'])).exists()
            if req:
                return 'Elige la app para la solicitud de %s: «%s».' % (
                    req.employee_id.name or req.device_id.name, req.name)
        if ctx.get('policy_id'):
            pol = self.env['foco.policy'].browse(int(ctx['policy_id'])).exists()
            if pol:
                return 'Lo que elijas se aprueba para todo el perfil «%s».' % pol.name
        if ctx.get('device_id'):
            dev = self.env['foco.mobile.device'].browse(int(ctx['device_id'])).exists()
            if dev:
                return 'Lo que elijas se instala en el teléfono de %s.' % (
                    dev.employee_id.name or dev.name)
        return 'Lo que elijas entra al catálogo de la empresa; apruébalo después en un perfil o en una solicitud.'

    @api.model
    def aprobar_desde_play(self, paquete, contexto=None):
        """Lo que pasa al elegir una app en la Play Store dentro de Odoo."""
        if not self.env.user.has_group('foco_monitor.group_foco_manager'):
            return {'ok': False, 'mensaje': 'Solo un administrador de Foco puede aprobar apps.'}
        ctx = contexto or {}
        app = self._asegurar(paquete)
        tipo = ctx.get('install_type') if ctx.get('install_type') in AMAPI_INSTALL else False
        if ctx.get('request_id'):
            req = self.env['foco.mobile.app.request'].browse(int(ctx['request_id'])).exists()
            if req:
                req.write({'app_id': app.id, 'package': app.package})
                req.action_aprobar()
                return {'ok': True, 'mensaje': 'Aprobada para %s: se instala en su teléfono.' % (
                    req.employee_id.name or req.device_id.name)}
        Grant = self.env['foco.mobile.grant']
        if ctx.get('policy_id'):
            pol = self.env['foco.policy'].browse(int(ctx['policy_id'])).exists()
            if pol:
                Grant._conceder(app, tipo or 'available', policy=pol)
                return {'ok': True, 'mensaje': '%s aprobada en el perfil «%s».' % (app.app_label or app.package, pol.name)}
        if ctx.get('device_id'):
            dev = self.env['foco.mobile.device'].browse(int(ctx['device_id'])).exists()
            if dev:
                Grant._conceder(app, tipo or 'force', device=dev)
                return {'ok': True, 'mensaje': '%s se instala en el teléfono de %s.' % (
                    app.app_label or app.package, dev.employee_id.name or dev.name)}
        return {'ok': True, 'mensaje': '%s quedó en el catálogo.' % (app.app_label or app.package)}


class FocoMobileGrant(models.Model):
    """Una app aprobada (o bloqueada) para un PERFIL o para UN telefono. La del
    telefono manda sobre la del perfil: asi se aprueba algo a una sola persona
    sin tocar a las demas."""
    _name = 'foco.mobile.grant'
    _description = 'App aprobada o bloqueada en un perfil o un celular'
    _order = 'policy_id, device_id, app_id'

    _perfil_uniq = models.Constraint('unique(policy_id, app_id)', 'Esa app ya está en el perfil.')
    _equipo_uniq = models.Constraint('unique(device_id, app_id)', 'Esa app ya está en ese teléfono.')

    policy_id = fields.Many2one('foco.policy', string='Perfil', ondelete='cascade', index=True)
    device_id = fields.Many2one('foco.mobile.device', string='Teléfono', ondelete='cascade', index=True)
    app_id = fields.Many2one('foco.mobile.app', string='App', required=True, ondelete='cascade', index=True)
    package = fields.Char(related='app_id.package', string='Paquete')
    install_type = fields.Selection(INSTALL_TYPES, string='Qué pasa', required=True, default='available',
                                    help='Instalar sola: aparece en el teléfono sin que la persona haga nada. '
                                         'Disponible: aparece en su Play Store para instalarla si la necesita. '
                                         'Bloqueada: no se puede instalar ni usar.')
    request_id = fields.Many2one('foco.mobile.app.request', string='Por la solicitud', ondelete='set null')
    note = fields.Char(string='Por qué')

    @api.constrains('policy_id', 'device_id')
    def _check_destino(self):
        for g in self:
            if bool(g.policy_id) == bool(g.device_id):
                raise ValidationError('Una aprobación es de un perfil o de un teléfono, no de los dos ni de ninguno.')
            if g.app_id.package == FOCO_PKG:
                raise ValidationError('Foco se instala siempre; no hace falta aprobarla.')

    @api.model
    def _conceder(self, app, tipo, policy=None, device=None, request=None):
        dom = [('app_id', '=', app.id)]
        dom.append(('policy_id', '=', policy.id) if policy else ('device_id', '=', device.id))
        g = self.sudo().search(dom, limit=1)
        vals = {'install_type': tipo}
        if request:
            vals['request_id'] = request.id
        if g:
            g.write(vals)
        else:
            vals.update({'app_id': app.id, 'policy_id': policy.id if policy else False,
                         'device_id': device.id if device else False})
            g = self.sudo().create(vals)
        return g

    def _mdm_equipos(self):
        return self.device_id | self.policy_id._mdm_equipos()

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._mdm_equipos()._mdm_programar()
        return recs

    def write(self, vals):
        antes = self._mdm_equipos()
        res = super().write(vals)
        (antes | self._mdm_equipos())._mdm_programar()
        return res

    def unlink(self):
        equipos = self._mdm_equipos()
        res = super().unlink()
        equipos.exists()._mdm_programar()
        return res


class FocoMobileInstalled(models.Model):
    """Lo que Google dice que tiene instalado cada telefono (inventario). Es la
    verdad del telefono, no la de la politica: si algo aprobado no aparece
    aqui, no se instalo."""
    _name = 'foco.mobile.installed'
    _description = 'App instalada en un celular (inventario de Google)'
    _order = 'source, name'

    _uniq = models.Constraint('unique(device_id, package)', 'Ese paquete ya está en el inventario del teléfono.')

    device_id = fields.Many2one('foco.mobile.device', string='Teléfono', required=True,
                                ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='device_id.employee_id', store=True, string='Empleado')
    department_id = fields.Many2one(related='device_id.department_id', store=True, string='Departamento')
    package = fields.Char(string='Paquete', required=True, index=True)
    name = fields.Char(string='App')
    version = fields.Char(string='Versión')
    source = fields.Selection([('play', 'Play Store'), ('sistema', 'Del sistema'), ('otro', 'Otro origen')],
                              string='Origen', index=True)
    state = fields.Selection([('instalada', 'Instalada'), ('quitada', 'Quitada')], string='Estado',
                             default='instalada', index=True)

    def action_bloquear_en_perfil(self):
        """Bloquea esta app en el perfil del telefono (p.ej. YouTube que viene
        de fabrica). Es la forma de apagar una app del sistema."""
        for i in self:
            perfil = i.device_id._mdm_perfil()
            if not perfil:
                raise UserError('El teléfono de %s no tiene perfil: asígnale uno para bloquear apps.' % (
                    i.device_id.employee_id.name or i.device_id.name))
            app = self.env['foco.mobile.app']._asegurar(i.package, i.name)
            self.env['foco.mobile.grant']._conceder(app, 'blocked', policy=perfil)
        return True


# =============================================================== SOLICITUDES
class FocoMobileAppRequest(models.Model):
    """La persona pide una app desde su telefono. El administrador decide."""
    _name = 'foco.mobile.app.request'
    _description = 'Solicitud de app desde un celular'
    _inherit = ['mail.thread']
    _order = 'create_date desc'

    device_id = fields.Many2one('foco.mobile.device', string='Teléfono', required=True,
                                ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='device_id.employee_id', store=True, string='Empleado', index=True)
    department_id = fields.Many2one(related='device_id.department_id', store=True, string='Departamento')
    name = fields.Char(string='App que pide', required=True, tracking=True)
    package = fields.Char(string='Paquete', help='Lo trae la solicitud si la persona pegó la liga de la Play Store; '
                                                 'si no, se llena al elegir la app en la Play Store.')
    reason = fields.Text(string='Para qué la necesita')
    state = fields.Selection([
        ('pendiente', 'Pendiente'),
        ('aprobada', 'Aprobada'),
        ('instalada', 'Instalada'),
        ('rechazada', 'Rechazada'),
    ], string='Estado', default='pendiente', required=True, index=True, tracking=True)
    app_id = fields.Many2one('foco.mobile.app', string='App aprobada')
    install_type = fields.Selection(INSTALL_TYPES[:2], string='Al aprobar', default='force',
                                    help='Instalar sola: aparece en su teléfono sin que haga nada. '
                                         'Disponible: la ve en su Play Store y la instala si quiere.')
    answer = fields.Char(string='Respuesta a la persona',
                         help='Lo lee en su teléfono. Obligatoria al rechazar.')
    decided_by = fields.Many2one('res.users', string='Decidió', readonly=True)
    decided_at = fields.Datetime(string='Decidida el', readonly=True)

    @api.model
    def desde_telefono(self, device, datos):
        """Crea la solicitud que manda el telefono. Una igual y pendiente del
        mismo telefono no se duplica: se devuelve la que ya estaba."""
        nombre = (datos.get('name') or '').strip()[:120]
        paquete = paquete_de(datos.get('package') or '') or paquete_de(nombre)
        motivo = (datos.get('reason') or '').strip()[:500]
        if not (nombre or paquete):
            return None, 'falta_app'
        if not motivo:
            return None, 'falta_motivo'
        dom = [('device_id', '=', device.id), ('state', '=', 'pendiente')]
        dom += [('package', '=', paquete)] if paquete else [('name', '=ilike', nombre)]
        ya = self.sudo().search(dom, limit=1)
        if ya:
            return ya, ''
        # Llega por la ruta publica del telefono: sin esto el historial la
        # firma "Public user". La firma la persona que la pide, y sobra el
        # "creado" automatico (el mensaje de abajo ya dice todo).
        rec = self.sudo().with_context(mail_create_nolog=True).create({
            'device_id': device.id, 'name': nombre or paquete, 'package': paquete or False,
            'reason': motivo,
        })
        grupo = self.env.ref('foco_monitor.group_foco_manager', raise_if_not_found=False)
        if grupo:
            rec.message_subscribe(partner_ids=grupo.user_ids.partner_id.ids)
        autor = device.employee_id.work_contact_id
        rec.message_post(
            body='%s pide «%s» desde su teléfono. Motivo: %s' % (
                device.employee_id.name or device.name, rec.name, motivo),
            author_id=autor.id or None,
            subtype_xmlid='mail.mt_comment')
        return rec, ''

    @api.model
    def para_telefono(self, device):
        """Las ultimas solicitudes de ese telefono, para su pantalla."""
        salida = []
        for r in self.sudo().search([('device_id', '=', device.id)], limit=20):
            salida.append({
                'id': r.id, 'name': r.name, 'package': r.package or '',
                'state': r.state, 'answer': r.answer or '',
                # "available" = aprobada para que la instale desde la Play
                # Store; "force" = se instala sola. El aviso del telefono
                # dice una cosa u otra segun esto.
                'install': r.install_type or '',
                'at': fields.Datetime.to_string(r.create_date) if r.create_date else '',
            })
        return salida

    def action_play(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'foco_play', 'name': 'Play Store · %s' % self.name,
                'context': {'foco_request_id': self.id}}

    def action_aprobar(self):
        for r in self:
            if r.state not in ('pendiente', 'rechazada'):
                continue
            app = r.app_id
            if not app and r.package:
                app = self.env['foco.mobile.app']._asegurar(r.package, r.name)
            if not app:
                raise UserError('Falta elegir la app: pulsa «Buscar en Play Store» y selecciónala, '
                                'o escribe su paquete (por ejemplo com.whatsapp.w4b).')
            self.env['foco.mobile.grant']._conceder(app, r.install_type or 'force',
                                                    device=r.device_id, request=r)
            r.write({'state': 'aprobada', 'app_id': app.id, 'package': app.package,
                     'decided_by': self.env.uid, 'decided_at': fields.Datetime.now()})
            r.message_post(body='Aprobada: %s (%s).' % (
                app.app_label or app.package, dict(INSTALL_TYPES)[r.install_type or 'force']))
        return True

    def action_rechazar(self):
        for r in self:
            if not (r.answer or '').strip():
                raise UserError('Escribe en «Respuesta a la persona» por qué no se aprueba: es lo que lee en su teléfono.')
            r.write({'state': 'rechazada', 'decided_by': self.env.uid,
                     'decided_at': fields.Datetime.now()})
            r.message_post(body='Rechazada: %s' % r.answer)
        return True

    def action_reabrir(self):
        self.write({'state': 'pendiente', 'decided_by': False, 'decided_at': False})
        return True


# =============================================================== TELEFONO
class FocoMobileDeviceMdm(models.Model):
    _inherit = 'foco.mobile.device'

    policy_id = fields.Many2one(
        'foco.policy', string='Perfil', ondelete='set null', index=True,
        help='Qué apps tiene, qué se bloquea y qué sitios se bloquean en Chrome. '
             'Vacío = el perfil por omisión de Ajustes.')
    grant_ids = fields.One2many('foco.mobile.grant', 'device_id', string='Apps de esta persona')
    installed_ids = fields.One2many('foco.mobile.installed', 'device_id', string='Instaladas')
    request_ids = fields.One2many('foco.mobile.app.request', 'device_id', string='Solicitudes')
    request_pendientes = fields.Integer(compute='_compute_mdm_cuentas')
    installed_count = fields.Integer(compute='_compute_mdm_cuentas')

    amapi_name = fields.Char(string='Equipo en Google', readonly=True, copy=False, index=True)
    amapi_state = fields.Selection([
        ('pendiente', 'Por aprovisionar'),
        ('aprovisionando', 'Aprovisionando'),
        ('activo', 'Gestionado'),
        ('deshabilitado', 'Deshabilitado'),
        ('perdido', 'Modo perdido'),
        ('borrado', 'Borrado'),
        ('otro', 'Otro'),
    ], string='Gestión Android', readonly=True, copy=False, index=True)
    amapi_compliant = fields.Boolean(string='Cumple la política', readonly=True)
    amapi_incumple = fields.Text(string='Qué no cumple', readonly=True)
    amapi_reporte_at = fields.Datetime(string='Último reporte a Google', readonly=True)
    amapi_alta_at = fields.Datetime(string='Aprovisionado el', readonly=True)
    amapi_serial = fields.Char(string='Número de serie', readonly=True)
    amapi_politica_aplicada = fields.Char(string='Versión aplicada', readonly=True)
    amapi_hash = fields.Char(readonly=True, copy=False)
    amapi_envio_at = fields.Datetime(string='Política enviada el', readonly=True)
    amapi_error = fields.Char(string='Error al enviar la política', readonly=True)

    def _compute_mdm_cuentas(self):
        for d in self:
            d.request_pendientes = len(d.request_ids.filtered(lambda r: r.state == 'pendiente'))
            d.installed_count = len(d.installed_ids.filtered(lambda i: i.state == 'instalada'))

    # ------------------------------------------------------------ la politica
    def _mdm_perfil(self):
        self.ensure_one()
        propio = self.policy_id.filtered('active')
        return propio or self.env['foco.settings'].sudo().get_settings().default_policy_id.filtered('active')

    def _mdm_codigo(self):
        """El codigo de invitacion que Foco usa para conectarse sola. Estable:
        mientras el ultimo siga sirviendo (o ya se haya usado) es el mismo, asi
        la politica no cambia en cada envio."""
        self.ensure_one()
        if not self.employee_id:
            return ''
        Inv = self.env['foco.invitation'].sudo()
        inv = Inv.search([('mobile_id', '=', self.id)], order='id desc', limit=1)
        vencida = inv and inv.state in ('draft', 'sent') and inv.expiry \
            and inv.expiry < fields.Datetime.now()
        if not inv or inv.state == 'expired' or vencida or inv.employee_id != self.employee_id:
            inv = Inv.create({'employee_id': self.employee_id.id, 'mobile_id': self.id,
                              'expiry': fields.Datetime.now() + timedelta(days=30)})
        return inv.token

    def _mdm_neutral(self):
        """La politica en terminos de Odoo, sin nada de Google: que apps, que
        restricciones, que sitios. Es lo que un motor distinto (otro MDM, la
        propia Foco como administrador) tendria que traducir."""
        self.ensure_one()
        perfil = self._mdm_perfil()
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if perfil:
            r = {
                'modo': perfil.mobile_play_mode or 'lista',
                'compartir': perfil.mobile_block_tethering,
                'cuentas': perfil.mobile_block_accounts,
                'sideload': perfil.mobile_block_sideload,
                'reset': perfil.mobile_block_factory_reset,
                'modo_seguro': perfil.mobile_block_safe_boot,
                'depuracion': perfil.mobile_block_debug,
                'usuarios': perfil.mobile_block_add_user,
                'usb': perfil.mobile_block_usb_files,
                'web': perfil.mobile_web_filter,
            }
        else:
            r = dict(_MOVIL_FABRICA)
        apps = {}
        for g in (perfil.mobile_grant_ids if perfil else self.env['foco.mobile.grant']):
            apps[g.app_id.package] = g.install_type
        for g in self.grant_ids:
            apps[g.app_id.package] = g.install_type      # la de la persona manda
        bloquear, permitir = [], []
        if perfil and r['web'] and ajustes.block_enabled:
            bloquear, permitir = perfil._listas()
            permitir = sorted(set(permitir) | set(self.env['foco.policy']._salvavidas()))
        return {
            'perfil': perfil.name if perfil else '',
            'apps': apps,
            'restricciones': r,
            'web': {'bloquear': bloquear, 'permitir': permitir},
            'foco': {'url': ajustes._base_url(), 'codigo': self._mdm_codigo()},
        }

    def _mdm_json(self):
        """La politica tal como la entiende Google (Android Management API)."""
        self.ensure_one()
        n = self._mdm_neutral()
        r = n['restricciones']
        apps = [{
            # Foco: siempre instalada, no se puede quitar ni detener, con sus
            # permisos concedidos y la direccion y el codigo para conectarse.
            'packageName': FOCO_PKG,
            'installType': 'FORCE_INSTALLED',
            'defaultPermissionPolicy': 'GRANT',
            'userControlSettings': 'USER_CONTROL_DISALLOWED',
            'autoUpdateMode': 'AUTO_UPDATE_HIGH_PRIORITY',
            'managedConfiguration': {'odoo_url': n['foco']['url'], 'enroll_code': n['foco']['codigo']},
        }]
        chrome = {'packageName': CHROME_PKG, 'installType': 'FORCE_INSTALLED'}
        if n['web']['bloquear'] or n['web']['permitir']:
            # Chrome recibe las listas como texto JSON: su configuracion
            # administrada declara estas politicas como cadena.
            chrome['managedConfiguration'] = {
                'URLBlocklist': json.dumps(n['web']['bloquear']),
                'URLAllowlist': json.dumps(n['web']['permitir']),
            }
        apps.append(chrome)
        for paquete, tipo in sorted(n['apps'].items()):
            if paquete in (FOCO_PKG, CHROME_PKG):
                continue
            apps.append({'packageName': paquete, 'installType': AMAPI_INSTALL[tipo]})
        return {
            'applications': apps,
            'playStoreMode': 'WHITELIST' if r['modo'] == 'lista' else 'BLACKLIST',
            'locationMode': 'LOCATION_ENFORCED',
            'modifyAccountsDisabled': bool(r['cuentas']),
            'addUserDisabled': bool(r['usuarios']),
            'factoryResetDisabled': bool(r['reset']),
            'safeBootDisabled': bool(r['modo_seguro']),
            'advancedSecurityOverrides': {
                'untrustedAppsPolicy': 'DISALLOW_INSTALL' if r['sideload'] else 'ALLOW_INSTALL_DEVICE_WIDE',
                'developerSettings': 'DEVELOPER_SETTINGS_DISABLED' if r['depuracion'] else 'DEVELOPER_SETTINGS_ALLOWED',
            },
            'deviceConnectivityManagement': {
                'tetheringSettings': 'DISALLOW_ALL_TETHERING' if r['compartir'] else 'ALLOW_ALL_TETHERING',
                'usbDataAccess': 'DISALLOW_USB_FILE_TRANSFER' if r['usb'] else 'ALLOW_USB_DATA_TRANSFER',
            },
            # Solo el servicio de accesibilidad de Foco (y los del sistema).
            'permittedAccessibilityServices': {'packageNames': [FOCO_PKG]},
            # Que Google cuente que tiene instalado y como esta: es el
            # inventario de Odoo.
            'statusReportingSettings': {
                'applicationReportsEnabled': True,
                'applicationReportingSettings': {'includeRemovedApps': True},
                'deviceSettingsEnabled': True,
                'softwareInfoEnabled': True,
                'networkInfoEnabled': True,
                'memoryInfoEnabled': True,
                'powerManagementEventsEnabled': True,
            },
        }

    @api.model
    def _mdm_hash(self, politica):
        return hashlib.sha256(json.dumps(politica, sort_keys=True).encode()).hexdigest()[:16]

    def _mdm_id_politica(self):
        self.ensure_one()
        return 'foco-%d' % self.id

    def _mdm_nombre_politica(self):
        return '%s/policies/%s' % (self.env['foco.amapi'].empresa(), self._mdm_id_politica())

    def _mdm_enviar(self, forzar=False, lanzar=False):
        """Manda la politica de cada telefono a Google si cambio. Devuelve
        cuantas se enviaron. Sin empresa registrada no hace nada."""
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if not ajustes.amapi_enterprise:
            if lanzar:
                self.env['foco.amapi'].empresa()      # levanta el mensaje claro
            return 0
        A = self.env['foco.amapi']
        enviadas = 0
        for dev in self.sudo():
            try:
                pol = dev._mdm_json()
                huella = self._mdm_hash(pol)
                if not forzar and huella == dev.amapi_hash:
                    continue
                A.politica(dev._mdm_id_politica(), pol)
                dev.write({'amapi_hash': huella, 'amapi_envio_at': fields.Datetime.now(),
                           'amapi_error': False})
                enviadas += 1
            except UserError as e:
                dev.write({'amapi_error': str(e)[:500]})
                _logger.warning('Foco MDM: politica de %s no enviada: %s', dev.id, e)
                if lanzar:
                    raise
        return enviadas

    def _mdm_programar(self):
        """Encola el envio de la politica para cuando se confirme la operacion
        (un solo envio por telefono aunque cambien diez cosas)."""
        if not self:
            return
        if not self.env['foco.settings'].sudo().get_settings().amapi_enterprise:
            return
        datos = self.env.cr.precommit.data
        pendientes = datos.setdefault('foco.mdm.pendientes', set())
        primera = not pendientes
        pendientes.update(self.ids)
        if primera:
            env = self.env

            def _enviar():
                ids = env.cr.precommit.data.pop('foco.mdm.pendientes', set())
                env['foco.mobile.device'].sudo().browse(list(ids)).exists()._mdm_enviar()
                env.flush_all()

            env.cr.precommit.add(_enviar)

    def write(self, vals):
        res = super().write(vals)
        if 'policy_id' in vals or 'employee_id' in vals:
            self._mdm_programar()
        return res

    # ------------------------------------------------------------ inventario
    @api.model
    def _mdm_sync(self, lanzar=False):
        """Lee de Google todos los telefonos de la empresa: estado, si cumplen,
        y que tienen instalado."""
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if not ajustes.amapi_enterprise:
            if lanzar:
                self.env['foco.amapi'].empresa()
            return 0
        try:
            equipos = self.env['foco.amapi'].dispositivos()
        except UserError as e:
            ajustes.write({'amapi_ultimo_error': str(e)[:500]})
            if lanzar:
                raise
            _logger.warning('Foco MDM: no se pudo leer la lista de equipos: %s', e)
            return 0
        Dev = self.sudo()
        for d in equipos:
            dev = Dev.browse()
            m = re.match(r'^foco:(\d+)$', d.get('enrollmentTokenData') or '')
            if m:
                dev = Dev.browse(int(m.group(1))).exists()
            if not dev and d.get('name'):
                dev = Dev.search([('amapi_name', '=', d['name'])], limit=1)
            if not dev:
                hw = d.get('hardwareInfo') or {}
                dev = Dev.create({
                    'name': ('%s %s' % (hw.get('brand') or '', hw.get('model') or '')).strip()
                    or 'Teléfono sin asignar',
                    'amapi_name': d.get('name'),
                })
            dev._mdm_leer(d)
        ajustes.write({'amapi_ultima_sync': fields.Datetime.now(), 'amapi_ultimo_error': False})
        return len(equipos)

    def _mdm_leer(self, d):
        self.ensure_one()
        hw = d.get('hardwareInfo') or {}
        sw = d.get('softwareInfo') or {}
        lineas = []
        for nc in (d.get('nonComplianceDetails') or []):
            partes = [nc.get('settingName') or '', nc.get('nonComplianceReason') or '']
            if nc.get('packageName'):
                partes.append(nc['packageName'])
            if nc.get('installationFailureReason'):
                partes.append(nc['installationFailureReason'])
            lineas.append(' · '.join(p for p in partes if p))
        vals = {
            'amapi_name': d.get('name') or self.amapi_name,
            'amapi_state': ESTADOS_AMAPI.get(d.get('state'), 'otro'),
            'amapi_compliant': bool(d.get('policyCompliant')),
            'amapi_incumple': '\n'.join(lineas) or False,
            'amapi_reporte_at': _ts(d.get('lastStatusReportTime')),
            'amapi_alta_at': _ts(d.get('enrollmentTime')),
            'amapi_serial': hw.get('serialNumber') or False,
            'amapi_politica_aplicada': str(d.get('appliedPolicyVersion') or ''),
        }
        if sw.get('androidVersion') and not self.os_version:
            vals['os_version'] = 'Android %s' % sw['androidVersion']
        self.write(vals)
        self._mdm_inventario(d.get('applicationReports') or [])

    def _mdm_inventario(self, reportes):
        self.ensure_one()
        Inst = self.env['foco.mobile.installed'].sudo()
        actuales = {i.package: i for i in self.installed_ids}
        origen = {'SYSTEM_APP_FACTORY_VERSION': 'sistema', 'SYSTEM_APP_UPDATED_VERSION': 'sistema',
                  'INSTALLED_FROM_PLAY_STORE': 'play'}
        vistos, instaladas, sistema = set(), set(), []
        for rep in reportes:
            paquete = rep.get('packageName')
            if not paquete or paquete in vistos:
                continue
            vistos.add(paquete)
            estado = 'quitada' if rep.get('state') == 'REMOVED' else 'instalada'
            fuente = origen.get(rep.get('applicationSource'), 'otro')
            vals = {'name': rep.get('displayName') or paquete, 'version': rep.get('versionName') or '',
                    'source': fuente, 'state': estado}
            if paquete in actuales:
                actuales[paquete].write(vals)
            else:
                vals.update({'device_id': self.id, 'package': paquete})
                Inst.create(vals)
            if estado == 'instalada':
                instaladas.add(paquete)
            if fuente == 'sistema':
                sistema.append(paquete)
        if sistema:
            self.env['foco.mobile.app'].sudo().search(
                [('package', 'in', sistema), ('sistema', '=', False)]).write({'sistema': True})
        # Lo aprobado que ya aparece instalado cierra su solicitud.
        self.request_ids.filtered(
            lambda q: q.state == 'aprobada' and q.package in instaladas).write({'state': 'instalada'})

    @api.model
    def _mdm_cron(self):
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if not ajustes.amapi_enterprise:
            return
        self._mdm_sync()
        # Reintento: lo que cambio y no se pudo mandar, se manda ahora.
        self.sudo().search([('amapi_state', 'not in', [False, 'borrado'])])._mdm_enviar()

    # ------------------------------------------------------------ acciones
    def _mdm_equipo_google(self):
        self.ensure_one()
        if not self.amapi_name:
            raise UserError('Este teléfono todavía no está gestionado por Android Enterprise: '
                            'aprovisiónalo con el QR de alta.')
        return self.amapi_name

    def action_mdm_enviar(self):
        n = self._mdm_enviar(forzar=True, lanzar=True)
        return self.env['foco.settings'].sudo().get_settings()._aviso(
            'Política enviada a Google (%d). El teléfono la aplica en su siguiente conexión.' % n, 'success')

    def action_mdm_bloquear(self):
        self.env['foco.amapi'].comando(self._mdm_equipo_google(), 'LOCK')
        self.message_post(body='Se ordenó bloquear la pantalla desde Odoo.')
        return self.env['foco.settings'].sudo().get_settings()._aviso('Orden enviada: el teléfono se bloquea.', 'success')

    def action_mdm_reiniciar(self):
        self.env['foco.amapi'].comando(self._mdm_equipo_google(), 'REBOOT')
        self.message_post(body='Se ordenó reiniciar el teléfono desde Odoo.')
        return self.env['foco.settings'].sudo().get_settings()._aviso('Orden enviada: el teléfono se reinicia.', 'success')

    def action_mdm_borrar(self):
        nombre = self._mdm_equipo_google()
        self.env['foco.amapi'].borrar_equipo(nombre, 'Restablecido por la empresa desde Odoo.')
        self.write({'amapi_state': 'borrado', 'amapi_hash': False})
        self.message_post(body='Se ordenó RESTABLECER DE FÁBRICA el teléfono desde Odoo (%s).' % self.env.user.name)
        return self.env['foco.settings'].sudo().get_settings()._aviso(
            'Orden enviada: el teléfono se restablece de fábrica en su siguiente conexión.', 'warning')

    def action_mdm_play(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'foco_play',
                'name': 'Play Store · %s' % (self.employee_id.name or self.name),
                'context': {'foco_device_id': self.id}}

    def action_mdm_alta(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': 'Alta del teléfono',
            'res_model': 'foco.mobile.alta.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_device_id': self.id, 'default_employee_id': self.employee_id.id,
                        'default_policy_id': self.policy_id.id},
        }

    def action_ver_solicitudes(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Solicitudes de %s' % (self.employee_id.name or self.name),
                'res_model': 'foco.mobile.app.request', 'view_mode': 'list,form',
                'domain': [('device_id', '=', self.id)]}

    def action_ver_instaladas(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Apps de %s' % (self.employee_id.name or self.name),
                'res_model': 'foco.mobile.installed', 'view_mode': 'list',
                'domain': [('device_id', '=', self.id)],
                'context': {'search_default_instaladas': 1}}


# =============================================================== ALTA POR QR
class FocoMobileAltaWizard(models.TransientModel):
    """El QR con el que un telefono de fabrica queda gestionado por la empresa."""
    _name = 'foco.mobile.alta.wizard'
    _description = 'Alta de un teléfono con Android Enterprise'

    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True)
    policy_id = fields.Many2one(
        'foco.policy', string='Perfil',
        default=lambda self: self.env['foco.settings'].sudo().get_settings().default_policy_id)
    device_id = fields.Many2one('foco.mobile.device', string='Teléfono (volver a dar de alta)')
    dias = fields.Integer(string='El QR sirve durante (días)', default=7)
    conservar_sistema = fields.Boolean(
        string='Conservar las apps del teléfono (cámara, galería, calculadora...)', default=True,
        help='Apagado, Android deja solo lo indispensable al aprovisionar. Lo que '
             'venga de fábrica y no quieras (YouTube, redes) se bloquea después '
             'desde el inventario del teléfono.')
    wifi_ssid = fields.Char(string='Red Wi-Fi para la instalación',
                            help='Opcional. El teléfono se conecta solo a esta red al escanear el QR. '
                                 'Vacía, el asistente del teléfono pide conectarse.')
    wifi_password = fields.Char(string='Contraseña de la red')
    wifi_tipo = fields.Selection([('WPA', 'WPA / WPA2'), ('WEP', 'WEP'), ('NONE', 'Abierta')],
                                 string='Seguridad de la red', default='WPA')
    estado = fields.Selection([('datos', 'Datos'), ('qr', 'QR listo')], default='datos')
    qr = fields.Binary(string='QR', readonly=True)
    expira = fields.Datetime(string='El QR caduca', readonly=True)
    telefono_id = fields.Many2one('foco.mobile.device', string='Teléfono en Odoo', readonly=True)

    def action_generar(self):
        self.ensure_one()
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if ajustes.amapi_estado != 'lista':
            raise UserError('Android Enterprise no está conectado todavía (%s).' % dict(
                ajustes._fields['amapi_estado'].selection).get(ajustes.amapi_estado))
        Dev = self.env['foco.mobile.device'].sudo()
        dev = self.device_id
        if not dev:
            # Un alta pendiente de la misma persona se reusa: generar dos QR no
            # debe dejar dos telefonos fantasma.
            dev = Dev.search([('employee_id', '=', self.employee_id.id),
                              ('amapi_state', '=', 'pendiente'), ('amapi_name', '=', False)], limit=1)
        vals = {'employee_id': self.employee_id.id, 'policy_id': self.policy_id.id or False}
        if dev:
            vals['amapi_state'] = 'pendiente' if not dev.amapi_name else dev.amapi_state
            dev.write(vals)
        else:
            vals.update({'name': 'Teléfono de %s' % self.employee_id.name, 'amapi_state': 'pendiente'})
            dev = Dev.create(vals)
        # La politica tiene que existir en Google ANTES del QR.
        dev._mdm_enviar(forzar=True, lanzar=True)
        token = self.env['foco.amapi'].token_alta(
            dev._mdm_nombre_politica(), 'foco:%d' % dev.id, max(1, min(90, self.dias or 7)))
        try:
            carga = json.loads(token.get('qrCode') or '{}')
        except ValueError:
            raise UserError('Google no devolvió un QR válido.')
        if self.conservar_sistema:
            carga['android.app.extra.PROVISIONING_LEAVE_ALL_SYSTEM_APPS_ENABLED'] = True
        if self.wifi_ssid:
            carga['android.app.extra.PROVISIONING_WIFI_SSID'] = self.wifi_ssid
            carga['android.app.extra.PROVISIONING_WIFI_SECURITY_TYPE'] = self.wifi_tipo or 'WPA'
            if self.wifi_password and self.wifi_tipo != 'NONE':
                carga['android.app.extra.PROVISIONING_WIFI_PASSWORD'] = self.wifi_password
        from reportlab.graphics.barcode import createBarcodeDrawing
        dibujo = createBarcodeDrawing('QR', value=json.dumps(carga, ensure_ascii=False),
                                      format='png', width=460, height=460)
        self.write({
            'estado': 'qr', 'telefono_id': dev.id,
            'qr': base64.b64encode(dibujo.asString('png')),
            'expira': _ts(token.get('expirationTimestamp')) or (fields.Datetime.now() + timedelta(days=self.dias or 7)),
            # La contraseña de la red no se queda guardada.
            'wifi_password': False,
        })
        dev.message_post(body='Se generó el QR de alta (caduca %s).' % fields.Datetime.to_string(self.expira))
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new', 'name': 'Alta del teléfono'}
