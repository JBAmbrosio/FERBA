"""Lado movil: celulares Android PROPIEDAD DE LA EMPRESA.

Es el equivalente del agente de Windows, pero para telefonos entregados por la
empresa. Reporta contra el MISMO Odoo, con el MISMO patron (enrolamiento por
codigo de invitacion -> api_key por dispositivo -> envios firmados con
`X-Foco-Key`).

QUE MIDE (y sus limites reales en Android)
    - Ubicacion: lat/lon periodica (el celular es de la empresa, se sabe donde
      esta). Cada punto trae precision, bateria y fuente.
    - Uso de apps: segundos en primer plano por paquete y dia
      (`UsageStatsManager`). Es el analogo de `fg_active` del Windows.
    - Llamadas: numero, direccion y duracion del registro de llamadas del
      sistema. Se deduplica por el id que da el propio Android (`external_id`).

APAGADO DE FABRICA
    `foco.settings.mobile_enabled` nace en False. Mientras este apagado, la
    ingesta NO guarda nada -igual que las capturas- y el aviso de privacidad
    tiene que cubrir ubicacion y llamadas antes de encenderlo.
"""

import base64
import secrets
import math
from datetime import datetime, timedelta, time
from urllib.parse import urlparse

import pytz

from odoo import api, fields, models, tools
from odoo.exceptions import UserError

from .foco_mobile_login import VIGENCIA_S

# En linea si su ultima señal es de hace menos de esto. Mismo criterio que el
# resto de Foco (3 envios perdidos del movil ~ 15 min).
MOBILE_ONLINE_SECS = 15 * 60


class FocoMobileDevice(models.Model):
    _name = 'foco.mobile.device'
    _description = 'Dispositivo movil monitoreado'
    _inherit = ['mail.thread']
    _order = 'last_seen desc'

    _api_key_uniq = models.Constraint('unique(api_key)',
                                      'La API key debe ser unica.')

    name = fields.Char(string='Dispositivo', required=True,
                       help='Marca/modelo o etiqueta del telefono.')
    employee_id = fields.Many2one('hr.employee', string='Empleado',
                                  ondelete='set null', index=True)
    department_id = fields.Many2one(related='employee_id.department_id',
                                    store=True, string='Departamento')
    android_id = fields.Char(string='ID de Android', index=True, copy=False,
                             help='Identificador estable del equipo; sirve para '
                                  'reconocerlo si se reinstala el agente.')
    phone_number = fields.Char(string='Numero')
    os_version = fields.Char(string='Android')
    app_version = fields.Char(string='Version del agente')
    battery = fields.Integer(string='Bateria %', readonly=True)

    api_key = fields.Char(
        string='API key', required=True, copy=False, readonly=True,
        default=lambda self: secrets.token_urlsafe(24),
        help='Clave con la que el telefono se autentica. Una por dispositivo.')
    last_seen = fields.Datetime(string='Ultima señal', readonly=True)

    # Retiro / manipulacion. El telefono avisa (ultimo aliento al desactivar su
    # administrador) cuando lo estan quitando. Sin eso, un equipo que se calla
    # podria estar apagado o desinstalado y no se distinguiria; con esto, el
    # retiro por la via normal se marca al instante. Cualquier reporte posterior
    # lo limpia (el equipo volvio).
    removed = fields.Boolean(string='Retirado', default=False, readonly=True)
    removed_at = fields.Datetime(string='Retirado el', readonly=True)
    removed_reason = fields.Char(string='Motivo del retiro', readonly=True)

    # Ultima posicion conocida, desnormalizada para verla sin abrir el historial.
    last_lat = fields.Float(string='Ultima latitud', digits=(10, 7), readonly=True)
    last_lon = fields.Float(string='Ultima longitud', digits=(10, 7), readonly=True)
    last_fix_at = fields.Datetime(string='Ultima ubicacion', readonly=True)

    # --- Salud del agente (P0-3) --------------------------------------------
    # Telemetria del PROPIO equipo (no del empleado): permite ver en el tablero
    # POR QUE un telefono no reporta bien -ubicacion apagada, permiso revocado,
    # bateria matando el proceso, buffer sin vaciar- sin cable ni adb. El
    # telefono la manda en CADA envio, aunque el monitoreo este apagado.
    health_at = fields.Datetime(string='Salud reportada', readonly=True)
    health_device_owner = fields.Boolean(string='Gestionado (Device Owner)', readonly=True)
    health_location_on = fields.Boolean(string='Ubicacion encendida', readonly=True)
    health_perm_location = fields.Boolean(string='Permiso de ubicacion', readonly=True)
    health_perm_bg_location = fields.Boolean(string='Ubicacion en 2º plano', readonly=True)
    health_perm_calls = fields.Boolean(string='Permiso de llamadas', readonly=True)
    health_perm_contacts = fields.Boolean(string='Permiso de contactos', readonly=True)
    health_usage_access = fields.Boolean(string='Acceso a uso de apps', readonly=True)
    health_battery_unrestricted = fields.Boolean(string='Bateria sin restriccion', readonly=True)
    health_accessibility = fields.Boolean(string='Accesibilidad activa', readonly=True)
    health_service_running = fields.Boolean(string='Servicio activo', readonly=True)
    health_uptime_s = fields.Integer(string='Uptime del servicio (s)', readonly=True)
    health_buffered = fields.Integer(string='Eventos sin enviar', readonly=True)
    health_status = fields.Selection([
        ('unknown', 'Sin datos'), ('ok', 'OK'),
        ('warn', 'Atencion'), ('bad', 'Problema'),
    ], string='Salud', compute='_compute_health', store=True, default='unknown')
    health_issues = fields.Char(string='Problemas', compute='_compute_health', store=True)

    @api.depends('health_at', 'health_location_on', 'health_perm_location',
                 'health_service_running', 'health_perm_bg_location',
                 'health_usage_access', 'health_battery_unrestricted',
                 'health_buffered')
    def _compute_health(self):
        for r in self:
            if not r.health_at:
                r.health_status = 'unknown'
                r.health_issues = ''
                continue
            bad, warn = [], []
            if not r.health_location_on:
                bad.append('Ubicación apagada')
            if not r.health_perm_location:
                bad.append('Sin permiso de ubicación')
            if not r.health_service_running:
                bad.append('Servicio detenido')
            if not r.health_perm_bg_location:
                warn.append('Sin ubicación en 2º plano')
            if not r.health_usage_access:
                warn.append('Sin acceso a uso de apps')
            if not r.health_battery_unrestricted:
                warn.append('Batería puede matarlo')
            if r.health_buffered and r.health_buffered > 50:
                warn.append('%d eventos sin enviar' % r.health_buffered)
            r.health_status = 'bad' if bad else ('warn' if warn else 'ok')
            r.health_issues = ' · '.join(bad + warn)

    def _health_vals(self, health):
        """Traduce el bloque `health` que manda el telefono a valores del modelo.
        Devuelve {} si no vino (agente viejo), para no pisar lo anterior."""
        if not isinstance(health, dict) or not health:
            return {}
        def b(k):
            return bool(health.get(k))
        return {
            'health_at': fields.Datetime.now(),
            'health_device_owner': b('device_owner'),
            'health_location_on': b('location_on'),
            'health_perm_location': b('perm_location'),
            'health_perm_bg_location': b('perm_bg_location'),
            'health_perm_calls': b('perm_calls'),
            'health_perm_contacts': b('perm_contacts'),
            'health_usage_access': b('usage_access'),
            'health_battery_unrestricted': b('battery_unrestricted'),
            'health_accessibility': b('accessibility'),
            'health_service_running': b('service_running'),
            'health_uptime_s': int(health.get('uptime_s') or 0),
            'health_buffered': int(health.get('buffered') or 0),
        }

    # --- Ventana nocturna de actualizacion de apps -----------------------------
    # Con la Play Store oculta, Foco la abre una vez al dia (hora del perfil,
    # `mobile_update_hour`) tapada por el aviso, pulsa "Actualizar todo" y la
    # vuelve a ocultar. El telefono reporta el ULTIMO resultado en cada envio
    # (`play_update`); aqui solo se escribe cuando cambia, asi `play_update_at`
    # es cuando TERMINO esa ventana (al minuto del siguiente envio), no cada envio.
    play_update_at = fields.Datetime(string='Última actualización de apps', readonly=True)
    play_update_date = fields.Date(string='Día de la ventana', readonly=True)
    play_update_mode = fields.Selection([
        ('activo', 'Activa (pulsó "Actualizar todo")'),
        ('pasivo', 'Pasiva (bloqueado con PIN: tienda en 2º plano)'),
        ('ninguno', 'No corrió'),
    ], string='Modo', readonly=True)
    play_update_result = fields.Char(
        string='Resultado', readonly=True,
        help='ok = terminó de actualizar · sin_pendientes = no había nada que actualizar · '
             'tope = se agotó el tiempo con actualizaciones en curso · pasivo / '
             'pasivo_interrumpido = teléfono bloqueado con PIN, la tienda quedó disponible '
             'en 2º plano (hasta que alguien lo desbloqueó) · sin_wifi · sin_cuenta (la '
             'tienda pide iniciar sesión) · sin_accesibilidad · fallo_abrir / fallo_pantalla '
             '(no reconoció la pantalla de la tienda) · fallo.')
    play_update_secs = fields.Integer(string='Duración (s)', readonly=True)

    def _play_update_vals(self, bloque):
        """Traduce el bloque `play_update` del telefono a valores del modelo.
        {} si no vino (agente viejo) o si es el MISMO resultado ya guardado."""
        if not isinstance(bloque, dict) or not bloque:
            return {}
        self.ensure_one()
        modo = bloque.get('modo') or 'ninguno'
        if modo not in ('activo', 'pasivo', 'ninguno'):
            modo = 'ninguno'
        try:
            dia = fields.Date.to_date(str(bloque.get('fecha') or '')[:10])
        except Exception:
            dia = False
        resultado = str(bloque.get('resultado') or '')[:64]
        try:
            segundos = int(bloque.get('segundos') or 0)
        except (TypeError, ValueError):
            segundos = 0
        if (self.play_update_date == dia and self.play_update_mode == modo
                and (self.play_update_result or '') == resultado
                and (self.play_update_secs or 0) == segundos):
            return {}
        return {
            'play_update_at': fields.Datetime.now(),
            'play_update_date': dia,
            'play_update_mode': modo,
            'play_update_result': resultado,
            'play_update_secs': segundos,
        }

    def _update_hour(self):
        """Hora local (0-23) de la ventana de actualizacion: la del perfil del
        equipo; sin perfil, las 3 de la manana."""
        self.ensure_one()
        p = self.policy_id
        if p and 0 <= (p.mobile_update_hour or 0) <= 23:
            return p.mobile_update_hour or 0
        return 3

    active = fields.Boolean(default=True)

    location_ids = fields.One2many('foco.location', 'device_id', string='Ubicaciones')
    call_ids = fields.One2many('foco.call', 'device_id', string='Llamadas')
    location_count = fields.Integer(compute='_compute_counts')
    call_count = fields.Integer(compute='_compute_counts')
    capture_count = fields.Integer(compute='_compute_counts')

    def _compute_counts(self):
        Loc = self.env['foco.location']
        Call = self.env['foco.call']
        Cap = self.env['foco.mobile.capture']
        for rec in self:
            rec.location_count = Loc.search_count([('device_id', '=', rec.id)])
            rec.call_count = Call.search_count([('device_id', '=', rec.id)])
            rec.capture_count = Cap.search_count([('device_id', '=', rec.id)])

    def action_solicitar_captura(self):
        """El administrador pide una captura AHORA. Se encola una orden que el
        telefono ejecuta en su proximo ciclo de envio (cada N min)."""
        self.ensure_one()
        self.env['foco.mobile.command'].create({
            'device_id': self.id, 'kind': 'screenshot',
            'payload': 'Solicitada desde Odoo',
        })
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'type': 'success', 'sticky': False,
                'message': 'Captura solicitada. Llegará en el próximo ciclo del teléfono.',
            },
        }

    def action_ver_capturas(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': 'Capturas',
            'res_model': 'foco.mobile.capture', 'view_mode': 'kanban,list,form',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id},
        }

    @api.model
    def _authenticate(self, key):
        if not key:
            return self.browse()
        return self.sudo().search(
            [('api_key', '=', key), ('active', '=', True)], limit=1)

    def action_reset_key(self):
        for rec in self:
            rec.api_key = secrets.token_urlsafe(24)

    def action_ver_ubicaciones(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Ubicaciones de %s' % (self.name or ''),
            'res_model': 'foco.location',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id)],
        }

    # ------------------------------------------------------------ tablero
    @api.model
    def _zona_rango(self):
        """La zona con la que se cortan los DIAS del periodo: la del horario de
        la empresa (el reloj de la oficina), y si no hay, la del usuario."""
        nombre = self.env.company.resource_calendar_id.tz or self.env.user.tz or 'UTC'
        try:
            return pytz.timezone(nombre)
        except Exception:
            return pytz.UTC

    @api.model
    def _zona_de(self, device):
        """La zona con la que se fecha lo que hace ESE telefono: la de su
        persona (RRHH / horario), como el resto de Foco; sin persona, la de la
        empresa. El telefono no reporta desfase como la PC, asi que aqui no hay
        reloj de maquina que mande."""
        if device.employee_id:
            return self.env['foco.settings'].sudo()._tzinfo_for(device.employee_id) or pytz.UTC
        return self._zona_rango()

    @staticmethod
    def _limites_utc(d1, d2, zona):
        """[dt1, dt2) en UTC naive que cubre los dias LOCALES d1..d2. Antes el
        corte era la medianoche UTC, que en Mazatlan son las 17:00: una llamada
        de las seis de la tarde caia en el dia siguiente."""
        a = zona.localize(datetime.combine(d1, time.min)).astimezone(pytz.UTC)
        b = zona.localize(datetime.combine(d2 + timedelta(days=1), time.min)).astimezone(pytz.UTC)
        return a.replace(tzinfo=None), b.replace(tzinfo=None)

    @staticmethod
    def _hora_local(dt, zona):
        """Hora del dia como decimal (8.5 = 08:30) de un datetime UTC naive."""
        loc = pytz.UTC.localize(dt).astimezone(zona)
        return round(loc.hour + loc.minute / 60.0 + loc.second / 3600.0, 4)

    @staticmethod
    def _txt_local(dt, zona, con_dia=True):
        if not dt:
            return ''
        loc = pytz.UTC.localize(dt).astimezone(zona)
        return loc.strftime('%d/%m %H:%M' if con_dia else '%H:%M')

    @api.model
    def dashboard(self, desde, hasta):
        """Todo lo que pinta el Tablero movil, agregado en el servidor, en un
        solo viaje. `desde`/`hasta` son dias LOCALES 'YYYY-MM-DD'.

        Respeta las reglas de registro: quien tiene alcance de un departamento
        solo ve sus equipos, porque parte de `self.search([])` (no sudo).

        Lo que devuelve, en el orden en que lo lee el tablero:
          kpis / prev   las cifras del periodo y las mismas del periodo
                        anterior igual de largo (para las flechas)
          devices       una fila por telefono: su persona, estado, salud,
                        bateria, pantalla (total y en apps de trabajo),
                        llamadas, visitas y puntos
          markers / track / visit_markers   lo del mapa
          calls         por direccion, por dia local y con quien mas
          apps          el top de apps con su marca de "registrada" (trabajo)
          linea         solo en UN dia: por telefono, las horas locales de sus
                        pings, llamadas y visitas, para la linea del dia
        """
        Loc = self.env['foco.location']
        Call = self.env['foco.call']
        Usage = self.env['foco.mobile.usage']
        Visita = self.env['foco.visita']
        App = self.env['foco.mobile.app']

        d1 = fields.Date.to_date(desde) or fields.Date.context_today(self)
        d2 = fields.Date.to_date(hasta) or d1
        if d2 < d1:
            d1, d2 = d2, d1
        zona = self._zona_rango()
        dt1, dt2 = self._limites_utc(d1, d2, zona)
        ndias = (d2 - d1).days + 1
        p1, p2 = d1 - timedelta(days=ndias), d1 - timedelta(days=1)
        pt1, pt2 = self._limites_utc(p1, p2, zona)
        ahora = fields.Datetime.now()

        devices = self.search([])
        dev_ids = devices.ids
        emp_ids = devices.mapped('employee_id').ids
        registradas = set(App.registradas())

        dom_loc = [('device_id', 'in', dev_ids), ('at', '>=', dt1), ('at', '<', dt2)]
        dom_call = [('device_id', 'in', dev_ids), ('at', '>=', dt1), ('at', '<', dt2)]
        dom_use = [('device_id', 'in', dev_ids), ('date', '>=', d1), ('date', '<=', d2)]
        # Una visita puede venir del telefono (device_id) o capturarse desde la
        # web por la misma persona (solo employee_id): las dos son suyas.
        dom_vis = ['|', ('device_id', 'in', dev_ids), ('employee_id', 'in', emp_ids),
                   ('check_in', '>=', dt1), ('check_in', '<', dt2)]

        # --- puntos de ubicacion por telefono ---
        puntos = {g[0].id: g[1] for g in Loc._read_group(dom_loc, ['device_id'], ['__count'])}

        # --- llamadas: UNA lectura y todo se cuenta aqui, en dia LOCAL ---
        llamadas = Call.search_read(
            dom_call, ['device_id', 'at', 'direction', 'duration_seconds', 'number', 'contact'],
            order='at', limit=20000)
        llam = {}            # device -> {'n', 'seg', 'in', 'out', 'missed'}
        por_dir = {'in': 0, 'out': 0, 'missed': 0, 'rejected': 0, 'blocked': 0, 'other': 0}
        por_dia = {}
        top = {}
        calls_por_dev = {}
        total_seg = 0
        for c in llamadas:
            did = c['device_id'][0]
            d = llam.setdefault(did, {'n': 0, 'seg': 0, 'in': 0, 'out': 0, 'missed': 0})
            seg = c['duration_seconds'] or 0
            direc = c['direction'] if c['direction'] in por_dir else 'other'
            d['n'] += 1
            d['seg'] += seg
            if direc in d:
                d[direc] += 1
            por_dir[direc] += 1
            total_seg += seg
            dia = fields.Date.to_string(pytz.UTC.localize(c['at']).astimezone(zona).date())
            por_dia[dia] = por_dia.get(dia, 0) + 1
            num = c['number'] or '—'
            t = top.setdefault(num, {'number': c['number'] or '', 'count': 0, 'seg': 0, 'contact': ''})
            t['count'] += 1
            t['seg'] += seg
            if c['contact'] and not t['contact']:
                t['contact'] = c['contact']
            calls_por_dev.setdefault(did, []).append(c)
        top_list = sorted(
            [{'number': t['number'], 'contact': t['contact'], 'count': t['count'],
              'minutes': round(t['seg'] / 60.0, 1)} for t in top.values()],
            key=lambda x: (-x['count'], -x['minutes']))[:8]
        dias_llam = [{'dia': k, 'n': v} for k, v in sorted(por_dia.items())]

        # --- pantalla por telefono: total, en apps REGISTRADAS y su app mayor ---
        pantalla, trabajo, top_app = {}, {}, {}
        apps_tot = {}
        for g in Usage._read_group(dom_use, ['device_id', 'package', 'app_label'],
                                   ['foreground_seconds:sum']):
            did, paquete, etq, seg = g[0].id, g[1] or '', g[2] or g[1] or 'Sin nombre', (g[3] or 0)
            pantalla[did] = pantalla.get(did, 0) + seg
            if paquete in registradas:
                trabajo[did] = trabajo.get(did, 0) + seg
            if seg > top_app.get(did, ('', 0))[1]:
                top_app[did] = (etq, seg)
            a = apps_tot.setdefault(paquete, {'label': etq, 'seg': 0, 'registrada': paquete in registradas,
                                              'package': paquete})
            a['seg'] += seg
            if etq and etq != 'Sin nombre':
                a['label'] = etq
        apps = sorted(({'label': a['label'], 'package': a['package'], 'registrada': a['registrada'],
                        'hours': round(a['seg'] / 3600.0, 2)} for a in apps_tot.values()),
                      key=lambda x: -x['hours'])[:10]

        # --- visitas de campo del periodo ---
        res_lbl = dict(Visita._fields['resultado'].selection)
        vis_rows = Visita.search_read(
            dom_vis, ['device_id', 'employee_id', 'partner_id', 'check_in', 'check_out',
                      'estado', 'en_sitio', 'resultado', 'latitude', 'longitude'],
            order='check_in', limit=4000)
        dev_por_emp = {}
        for dv in devices:
            if dv.employee_id:
                dev_por_emp.setdefault(dv.employee_id.id, dv.id)
        vis_dev, vis_por_dev, visit_markers = {}, {}, []
        vis_tot = {'n': 0, 'en_sitio': 0, 'lejos': 0, 'en_curso': 0}
        for v in vis_rows:
            did = (v['device_id'][0] if v['device_id']
                   else dev_por_emp.get(v['employee_id'][0] if v['employee_id'] else 0))
            if not did:
                continue
            c = vis_dev.setdefault(did, {'n': 0, 'en_sitio': 0, 'lejos': 0, 'en_curso': 0})
            for cc in (c, vis_tot):
                cc['n'] += 1
                if v['en_sitio'] == 'si':
                    cc['en_sitio'] += 1
                elif v['en_sitio'] == 'lejos':
                    cc['lejos'] += 1
                if v['estado'] == 'en_curso':
                    cc['en_curso'] += 1
            vis_por_dev.setdefault(did, []).append(v)
            if v['latitude'] or v['longitude']:
                visit_markers.append({
                    'device_id': did, 'lat': v['latitude'], 'lon': v['longitude'],
                    'cliente': v['partner_id'][1] if v['partner_id'] else '',
                    'employee': v['employee_id'][1] if v['employee_id'] else '',
                    'at': self._txt_local(v['check_in'], zona),
                    'en_sitio': v['en_sitio'] or '', 'estado': v['estado'] or '',
                    'resultado': res_lbl.get(v['resultado'], ''),
                })

        # --- una fila por telefono + marcadores de ultima posicion ---
        dev_rows, marcadores = [], []
        en_linea = retirados = sin_senal = nunca = con_problemas = bateria_baja = 0
        for dv in devices:
            online = bool(dv.last_seen and
                          (ahora - dv.last_seen).total_seconds() < MOBILE_ONLINE_SECS)
            # retirado (aviso del propio equipo) manda sobre en linea / sin señal
            if dv.removed:
                estado = 'removed'
                retirados += 1
            elif online:
                estado = 'online'
                en_linea += 1
            elif dv.last_seen:
                estado = 'silent'       # alguna vez reporto: su silencio importa
                sin_senal += 1
            else:
                estado = 'never'
                nunca += 1
            if estado != 'removed' and dv.health_status in ('bad', 'warn'):
                con_problemas += 1
            baja = bool(dv.last_seen and estado != 'removed' and (dv.battery or 0) < 20)
            if baja:
                bateria_baja += 1
            ll = llam.get(dv.id, {'n': 0, 'seg': 0, 'in': 0, 'out': 0, 'missed': 0})
            vc = vis_dev.get(dv.id, {'n': 0, 'en_sitio': 0, 'lejos': 0, 'en_curso': 0})
            dev_rows.append({
                'id': dv.id, 'name': dv.name or '', 'estado': estado, 'online': online,
                'employee_id': dv.employee_id.id or 0,
                'employee': dv.employee_id.display_name or '',
                'dept': dv.department_id.display_name or '',
                'battery': dv.battery, 'bateria_baja': baja,
                'since_min': int((ahora - dv.last_seen).total_seconds() // 60) if dv.last_seen else None,
                'last_seen': fields.Datetime.to_string(dv.last_seen) if dv.last_seen else '',
                'removed_at': self._txt_local(dv.removed_at, zona),
                'removed_reason': dv.removed_reason or '',
                'last_lat': dv.last_lat, 'last_lon': dv.last_lon,
                'last_fix_min': int((ahora - dv.last_fix_at).total_seconds() // 60) if dv.last_fix_at else None,
                'points': puntos.get(dv.id, 0),
                'calls': ll['n'], 'calls_minutes': round(ll['seg'] / 60.0, 1),
                'calls_in': ll['in'], 'calls_out': ll['out'], 'calls_missed': ll['missed'],
                'screen_hours': round(pantalla.get(dv.id, 0) / 3600.0, 2),
                'work_hours': round(trabajo.get(dv.id, 0) / 3600.0, 2),
                'top_app': top_app.get(dv.id, ('', 0))[0],
                'visitas': vc['n'], 'visitas_en_sitio': vc['en_sitio'],
                'visitas_lejos': vc['lejos'], 'visitas_en_curso': vc['en_curso'],
                'health': dv.health_status, 'health_issues': dv.health_issues or '',
                'health_at': self._txt_local(dv.health_at, zona),
                'buffered': dv.health_buffered or 0,
                'app_version': dv.app_version or '', 'os_version': dv.os_version or '',
            })
            if dv.last_lat or dv.last_lon:
                marcadores.append({
                    'id': dv.id, 'name': dv.name or '',
                    'employee': dv.employee_id.display_name or '',
                    'lat': dv.last_lat, 'lon': dv.last_lon, 'online': online,
                    'estado': estado, 'battery': dv.battery,
                    'at': self._txt_local(dv.last_fix_at, zona),
                })

        # --- recorrido del periodo, por telefono (las lineas del mapa) ---
        track = {}
        for p in Loc.search_read(dom_loc, ['device_id', 'lat', 'lon'],
                                 order='device_id, at', limit=6000):
            track.setdefault(str(p['device_id'][0]), []).append([p['lat'], p['lon']])

        # --- el periodo anterior, igual de largo, para las flechas ---
        prev_call = Call._read_group(
            [('device_id', 'in', dev_ids), ('at', '>=', pt1), ('at', '<', pt2)],
            [], ['__count', 'duration_seconds:sum'])
        prev_use = Usage._read_group(
            [('device_id', 'in', dev_ids), ('date', '>=', p1), ('date', '<=', p2)],
            [], ['foreground_seconds:sum'])
        prev_vis = Visita.search_count(
            ['|', ('device_id', 'in', dev_ids), ('employee_id', 'in', emp_ids),
             ('check_in', '>=', pt1), ('check_in', '<', pt2)])
        prev = {
            'calls': (prev_call[0][0] if prev_call else 0) or 0,
            'minutes': round(((prev_call[0][1] if prev_call else 0) or 0) / 60.0, 1),
            'screen_hours': round(((prev_use[0][0] if prev_use else 0) or 0) / 3600.0, 2),
            'visitas': prev_vis,
        }

        # --- la linea del dia: solo cuando el periodo es UN dia ---
        linea = None
        if d1 == d2:
            lo, hi = 8.0, 20.0
            filas = []
            pings_por_dev = {}
            for p in Loc.search_read(dom_loc, ['device_id', 'at'], order='at', limit=20000):
                pings_por_dev.setdefault(p['device_id'][0], []).append(p['at'])
            for dv in devices:
                z = self._zona_de(dv)
                # un ping cada 5 min como mucho: es "con señal", no cada punto
                pings = sorted({round(self._hora_local(a, z) * 12) / 12.0
                                for a in pings_por_dev.get(dv.id, [])})
                cal = []
                for c in calls_por_dev.get(dv.id, []):
                    a = self._hora_local(c['at'], z)
                    cal.append({'a': a, 'b': min(24.0, a + max(c['duration_seconds'] or 0, 60) / 3600.0),
                                'dir': c['direction'] if c['direction'] in por_dir else 'other',
                                'quien': c['contact'] or c['number'] or ''})
                vis = []
                for v in vis_por_dev.get(dv.id, []):
                    a = self._hora_local(v['check_in'], z)
                    b = self._hora_local(v['check_out'], z) if v['check_out'] else None
                    if b is not None and b < a:      # cerro al dia siguiente
                        b = 24.0
                    vis.append({'a': a, 'b': b, 'estado': v['estado'] or '',
                                'cliente': v['partner_id'][1] if v['partner_id'] else '',
                                'en_sitio': v['en_sitio'] or '',
                                'resultado': res_lbl.get(v['resultado'], '')})
                if not (pings or cal or vis):
                    continue
                for h in pings:
                    lo, hi = min(lo, h), max(hi, h)
                for s in cal + vis:
                    lo, hi = min(lo, s['a']), max(hi, s['b'] if s['b'] is not None else s['a'])
                filas.append({'device_id': dv.id, 'employee_id': dv.employee_id.id or 0,
                              'pings': pings, 'calls': cal, 'visitas': vis})
            linea = {'ventana': [int(math.floor(lo)), int(min(24, math.ceil(hi)))], 'dias': filas}

        return {
            'kpis': {
                'devices': len(devices), 'online': en_linea,
                'retirados': retirados, 'sin_senal': sin_senal, 'nunca': nunca,
                'con_problemas': con_problemas, 'bateria_baja': bateria_baja,
                'points': sum(puntos.values()),
                'calls': sum(por_dir.values()),
                'calls_in': por_dir['in'], 'calls_out': por_dir['out'],
                'calls_missed': por_dir['missed'],
                'minutes': round(total_seg / 60.0, 1),
                'screen_hours': round(sum(pantalla.values()) / 3600.0, 2),
                'work_hours': round(sum(trabajo.values()) / 3600.0, 2),
                'visitas': vis_tot['n'], 'visitas_en_sitio': vis_tot['en_sitio'],
                'visitas_lejos': vis_tot['lejos'], 'visitas_en_curso': vis_tot['en_curso'],
            },
            'prev': prev,
            'devices': dev_rows,
            'markers': marcadores,
            'visit_markers': visit_markers,
            'track': track,
            'calls': {
                'in': por_dir['in'], 'out': por_dir['out'], 'missed': por_dir['missed'],
                'rejected': por_dir['rejected'], 'blocked': por_dir['blocked'],
                'other': por_dir['other'],
                'total': sum(por_dir.values()), 'minutes': round(total_seg / 60.0, 1),
                'by_day': dias_llam, 'top': top_list,
                'labels': dict(Call._fields['direction'].selection),
            },
            'apps': apps,
            'linea': linea,
        }

    @api.model
    def ficha(self, device_id, desde, hasta):
        """Lo que la ficha lateral muestra de UN telefono en el periodo y que
        no viaja en `dashboard` para no cargar el tablero: sus ultimas
        llamadas, sus visitas, sus apps y la salud del equipo, ya en la zona
        de su persona. Parte de `self.search` (no sudo): mismas reglas."""
        Loc = self.env['foco.location']
        Call = self.env['foco.call']
        Usage = self.env['foco.mobile.usage']
        Visita = self.env['foco.visita']
        Cap = self.env['foco.mobile.capture']

        dv = self.search([('id', '=', int(device_id))], limit=1)
        if not dv:
            return {}
        d1 = fields.Date.to_date(desde) or fields.Date.context_today(self)
        d2 = fields.Date.to_date(hasta) or d1
        if d2 < d1:
            d1, d2 = d2, d1
        dt1, dt2 = self._limites_utc(d1, d2, self._zona_rango())
        z = self._zona_de(dv)
        con_dia = d1 != d2
        registradas = set(self.env['foco.mobile.app'].registradas())

        dir_lbl = dict(Call._fields['direction'].selection)
        llamadas = []
        for c in Call.search_read(
                [('device_id', '=', dv.id), ('at', '>=', dt1), ('at', '<', dt2)],
                ['at', 'direction', 'duration_seconds', 'number', 'contact'],
                order='at desc', limit=12):
            llamadas.append({
                'cuando': self._txt_local(c['at'], z, con_dia),
                'quien': c['contact'] or c['number'] or '—', 'numero': c['number'] or '',
                'dir': c['direction'] if c['direction'] in dir_lbl else 'other',
                'dir_txt': dir_lbl.get(c['direction'], 'Otra'),
                'minutos': round((c['duration_seconds'] or 0) / 60.0, 1),
            })

        res_lbl = dict(Visita._fields['resultado'].selection)
        sitio_lbl = dict(Visita._fields['en_sitio'].selection)
        dom_vis = [('device_id', '=', dv.id)]
        if dv.employee_id:
            dom_vis = ['|', ('device_id', '=', dv.id), ('employee_id', '=', dv.employee_id.id)]
        visitas = []
        for v in Visita.search_read(
                dom_vis + [('check_in', '>=', dt1), ('check_in', '<', dt2)],
                ['partner_id', 'check_in', 'check_out', 'estado', 'en_sitio', 'resultado',
                 'distancia_m', 'analisis_puntaje', 'audio_estado'],
                order='check_in desc', limit=30):
            visitas.append({
                'id': v['id'], 'cliente': v['partner_id'][1] if v['partner_id'] else '',
                'llegada': self._txt_local(v['check_in'], z, con_dia),
                'salida': self._txt_local(v['check_out'], z, False) if v['check_out'] else '',
                'estado': v['estado'] or '', 'en_sitio': v['en_sitio'] or '',
                'en_sitio_txt': sitio_lbl.get(v['en_sitio'], ''),
                'distancia_m': int(v['distancia_m'] or 0),
                'resultado': v['resultado'] or '', 'resultado_txt': res_lbl.get(v['resultado'], ''),
                'puntaje': v['analisis_puntaje'] or 0,
                'analizada': v['audio_estado'] == 'listo',
            })

        apps = []
        for g in Usage._read_group(
                [('device_id', '=', dv.id), ('date', '>=', d1), ('date', '<=', d2)],
                ['package', 'app_label'], ['foreground_seconds:sum']):
            apps.append({'label': g[1] or g[0] or 'Sin nombre', 'package': g[0] or '',
                         'registrada': (g[0] or '') in registradas,
                         'hours': round((g[2] or 0) / 3600.0, 2)})
        apps = sorted(apps, key=lambda x: -x['hours'])[:8]

        checks = []
        if dv.health_at:
            checks = [
                {'k': 'Servicio activo', 'ok': dv.health_service_running},
                {'k': 'Ubicación encendida', 'ok': dv.health_location_on},
                {'k': 'Permiso de ubicación', 'ok': dv.health_perm_location},
                {'k': 'Ubicación en segundo plano', 'ok': dv.health_perm_bg_location},
                {'k': 'Acceso a uso de apps', 'ok': dv.health_usage_access},
                {'k': 'Batería sin restricción', 'ok': dv.health_battery_unrestricted},
                {'k': 'Permiso de llamadas', 'ok': dv.health_perm_calls},
                {'k': 'Accesibilidad activa', 'ok': dv.health_accessibility},
                {'k': 'Gestionado (Device Owner)', 'ok': dv.health_device_owner},
            ]
        return {
            'llamadas': llamadas,
            'visitas': visitas,
            'apps': apps,
            'ubicaciones': Loc.search_count([('device_id', '=', dv.id), ('at', '>=', dt1), ('at', '<', dt2)]),
            'capturas': Cap.search_count([('device_id', '=', dv.id), ('at', '>=', dt1), ('at', '<', dt2)]),
            'ultima_ubicacion': {
                'lat': dv.last_lat, 'lon': dv.last_lon,
                'cuando': self._txt_local(dv.last_fix_at, z),
            } if (dv.last_lat or dv.last_lon) else None,
            'salud': {
                'status': dv.health_status, 'issues': dv.health_issues or '',
                'cuando': self._txt_local(dv.health_at, z), 'checks': checks,
                'uptime_h': round((dv.health_uptime_s or 0) / 3600.0, 1),
                'buffered': dv.health_buffered or 0,
            },
            'play_update': {
                'cuando': self._txt_local(dv.play_update_at, z),
                'modo': dv.play_update_mode or '', 'resultado': dv.play_update_result or '',
                'segundos': dv.play_update_secs or 0,
            } if dv.play_update_at else None,
            'equipo': {
                'name': dv.name or '', 'numero': dv.phone_number or '',
                'android': dv.os_version or '', 'agente': dv.app_version or '',
            },
        }

    # ------------------------------------------------------- visitas de campo
    def clientes_para(self):
        """Cartera de clientes de ventas para las pestanas Visita y Mapa del
        telefono: TODOS los clientes de ventas, para todos los vendedores
        (decision del 14-oct-2026). Antes, si el usuario del telefono tenia
        aunque fuera un cliente asignado, solo veia los suyos; como registrar un
        prospecto desde el telefono se lo asigna, al primer prospecto el
        vendedor perdia el resto de la cartera. Campos minimos para ubicar y
        elegir en el telefono."""
        self.ensure_one()
        Partner = self.env['res.partner'].sudo()
        filas = []
        for c in Partner.search([('foco_cliente_ventas', '=', True)], limit=3000):
            filas.append({
                'id': c.id, 'name': c.display_name,
                'lat': c.partner_latitude, 'lon': c.partner_longitude,
                'zona': c.foco_zona or '', 'cultivo': c.foco_cultivo or '',
                'phone': c.phone or '', 'estatus': c.foco_estatus or 'prospecto',
                # Zona por ESTADO (pestana Mapa): el codigo empata con el de los
                # poligonos que viajan en el APK (assets/mx_estados.json).
                'estado': c.foco_estado_id.name or '',
                'estado_code': c.foco_estado_id.code or '',
                'contacto': c.foco_contacto or '',
                # todos ven a todos: la ficha dice de quien es el cliente
                'vendedor': c.user_id.name or '',
                'ultima_visita': (fields.Datetime.to_string(c.foco_ultima_visita)
                                  if c.foco_ultima_visita else ''),
                # Todos sus telefonos (el del cliente y los de sus contactos):
                # el vendedor elige a cual llamar o escribir (15-oct-2026).
                'telefonos': self._telefonos_de(c),
            })
        return filas

    @api.model
    def _telefonos_de(self, partner):
        """[{nombre, numero}] del cliente y de sus contactos, sin repetir numero."""
        salida, vistos = [], set()
        for p in partner | partner.child_ids.filtered('active'):
            numero = (p.phone or '').strip()
            digitos = ''.join(ch for ch in numero if ch.isdigit())
            if len(digitos) < 7 or digitos[-10:] in vistos:
                continue
            vistos.add(digitos[-10:])
            salida.append({'nombre': p.name if p != partner else (partner.foco_contacto or ''),
                           'numero': numero})
        return salida

    # ------------------------------------------------- ventas en el celular
    #
    # Acordado con el Ing. Francisco el 14-oct-2026: en el telefono el vendedor
    # BUSCA sus cotizaciones y las MANDA por WhatsApp o por correo (crearlas
    # desde el telefono no). Todo corre CON EL USUARIO DE ODOO DEL VENDEDOR
    # (`with_user`), asi que valen sus permisos -"solo sus documentos"- y la
    # aprobacion de cotizaciones de FERBA tal cual: sin aprobar no se genera el
    # PDF ni se envia, porque el candado vive en el motor de reportes y en
    # action_quotation_send, no en esta pantalla.

    def _vendedor(self):
        self.ensure_one()
        return self.sudo().employee_id.user_id

    @api.model
    def _requiere_aprobacion(self, orden):
        return ('aprobacion_state' in orden._fields and orden.requiere_aprobacion
                and orden.aprobacion_state != 'aprobada')

    @api.model
    def _es_aprobador(self, user):
        grupo = self.env.ref('ferba_aprobacion_cotizaciones.group_aprobador_cotizaciones',
                             raise_if_not_found=False)
        return bool(grupo) and grupo in user.all_group_ids

    def cotizaciones_para(self, partner_id=None, q='', limite=60):
        """Las cotizaciones que el vendedor de este telefono ve en Odoo."""
        self.ensure_one()
        user = self._vendedor()
        if not user:
            return {'ok': False, 'error': 'sin_usuario',
                    'mensaje': 'Tu teléfono no está ligado a un usuario de Odoo. Pídele al '
                               'administrador que lo ligue en tu ficha de empleado.'}
        if 'sale.order' not in self.env:
            return {'ok': False, 'error': 'sin_ventas', 'mensaje': 'Ventas no está instalado en Odoo.'}
        # las etiquetas (Cotizacion, Pedido de venta...) en el idioma del vendedor
        SO = self.env['sale.order'].with_user(user).with_context(lang=user.lang or 'es_MX')
        dom = [('state', '!=', 'cancel')]
        if partner_id:
            cliente = self.env['res.partner'].sudo().browse(int(partner_id)).exists()
            if not cliente:
                return {'ok': True, 'cotizaciones': []}
            dom.append(('partner_id', 'child_of', cliente.commercial_partner_id.id))
        q = (q or '').strip()
        if q:
            dom += ['|', '|', ('name', 'ilike', q), ('partner_id', 'ilike', q),
                    ('client_order_ref', 'ilike', q)]
        try:
            etq_estado = dict(SO._fields['state']._description_selection(SO.env))
        except Exception:
            etq_estado = dict(SO._fields['state'].selection)
        etq_aprob = (dict(SO._fields['aprobacion_state'].selection)
                     if 'aprobacion_state' in SO._fields else {})
        filas = []
        for o in SO.search(dom, order='date_order desc, id desc', limit=int(limite or 60)):
            bloqueada = self._requiere_aprobacion(o)
            aprob = o.aprobacion_state if 'aprobacion_state' in o._fields else ''
            productos = o.order_line.filtered(lambda l: not l.display_type and l.product_id)
            filas.append({
                'id': o.id, 'name': o.name,
                'fecha': fields.Datetime.to_string(o.date_order) if o.date_order else '',
                'vigencia': fields.Date.to_string(o.validity_date) if o.validity_date else '',
                'monto': o.amount_total, 'moneda': o.currency_id.name or '',
                'simbolo': o.currency_id.symbol or '$',
                'estado': o.state, 'estado_txt': etq_estado.get(o.state, o.state),
                'aprobacion': aprob,
                'aprobacion_txt': etq_aprob.get(aprob, '') if bloqueada or aprob == 'aprobada' else '',
                'motivo_rechazo': (o.aprobacion_motivo or '') if aprob == 'rechazada' else '',
                'puede_enviar': not bloqueada,
                # el mismo boton «Enviar a revision» de Odoo: sin revisar o rechazada
                'puede_revision': bloqueada and aprob in ('sin', 'rechazada'),
                'cliente': o.partner_id.display_name or '',
                'cliente_id': o.partner_id.commercial_partner_id.id,
                'correo': o.partner_id.email or '',
                # a cual de sus numeros mandarla por WhatsApp
                'telefonos': self._telefonos_de(o.partner_id.commercial_partner_id.sudo()),
                'vendedor': o.user_id.name or '',
                'productos': [l.product_id.display_name for l in productos[:3]],
                'n_lineas': len(productos),
                'url': '%s/odoo/sales/%d' % (o.get_base_url(), o.id),
            })
        return {'ok': True, 'cotizaciones': filas, 'vendedor': user.name}

    def _orden_del_vendedor(self, order_id):
        user = self._vendedor()
        if not user:
            raise UserError('Tu teléfono no está ligado a un usuario de Odoo.')
        orden = self.env['sale.order'].with_user(user).browse(int(order_id or 0)).exists()
        if not orden:
            raise UserError('Esa cotización ya no existe.')
        if not orden.has_access('read'):
            raise UserError('La cotización %s no es tuya: con tus permisos de Odoo no la '
                            'puedes ver ni enviar.' % orden.sudo().name)
        return orden, user

    def cotizacion_pdf(self, order_id):
        """(nombre de archivo, bytes del PDF) de la cotizacion, generado CON EL
        USUARIO del vendedor: si no esta aprobada, el candado de FERBA lo detiene
        y aqui se devuelve su mensaje tal cual."""
        self.ensure_one()
        orden, user = self._orden_del_vendedor(order_id)
        if self._requiere_aprobacion(orden):
            raise UserError('La cotización %s todavía no está aprobada: en cuanto la aprueben '
                            'la podrás enviar.' % orden.name)
        pdf, _tipo = self.env['ir.actions.report'].with_user(user)._render_qweb_pdf(
            'sale.report_saleorder', [orden.id])
        nombre = 'Cotizacion %s.pdf' % (orden.name or orden.id)
        orden.sudo().message_post(
            body='%s descargó la cotización desde su teléfono para enviarla por WhatsApp.' % user.name,
            message_type='notification', subtype_xmlid='mail.mt_note')
        return nombre, pdf

    def cotizacion_correo(self, order_id, correo=None, guardar=True):
        """Manda la cotizacion por correo al cliente con la plantilla de Odoo, como
        el boton «Enviar por correo», con el usuario del vendedor.

        `correo` (16-oct-2026): el vendedor puede corregirlo en el telefono -el
        de Odoo ya no es, o el cliente lo quiere en otro-. Con `guardar` queda en
        el contacto de la cotizacion; sin el, va solo esta vez, a un contacto del
        cliente con ese correo (el existente o uno nuevo), como en Odoo."""
        self.ensure_one()
        orden, user = self._orden_del_vendedor(order_id)
        actual = (orden.partner_id.email or '').strip()
        nuevo = (correo or '').strip() or actual
        if not nuevo:
            raise UserError('%s no tiene correo en Odoo. Escríbelo o mándala por WhatsApp.'
                            % orden.partner_id.display_name)
        normal = tools.email_normalize(nuevo)
        if not normal:
            raise UserError('«%s» no es un correo válido.' % nuevo)
        # Un aprobador pasa: action_quotation_send la aprueba en el acto, como en Odoo.
        if self._requiere_aprobacion(orden) and not self._es_aprobador(user):
            raise UserError('La cotización %s todavía no está aprobada: en cuanto la aprueben '
                            'la podrás enviar.' % orden.name)
        destino = orden.partner_id
        if normal != tools.email_normalize(actual):
            if guardar:
                # con SUS permisos: si no puede editar contactos, Odoo lo dice
                orden.partner_id.with_user(user).write({'email': nuevo})
            else:
                Partner = self.env['res.partner'].with_user(user)
                cliente = orden.partner_id.commercial_partner_id
                destino = (Partner.search([('email_normalized', '=', normal),
                                           ('commercial_partner_id', '=', cliente.id)], limit=1)
                           or Partner.create({'name': nuevo, 'email': nuevo,
                                              'parent_id': cliente.id, 'type': 'contact'}))
        accion = orden.action_quotation_send()
        ctx = dict((accion or {}).get('context') or {})
        Mail = self.env['mail.mail'].sudo()
        antes = set(Mail.search([('model', '=', 'sale.order'), ('res_id', '=', orden.id)]).ids)
        composer = self.env['mail.compose.message'].with_user(user).with_context(ctx).create({})
        if destino != orden.partner_id:
            composer.partner_ids = [(6, 0, destino.ids)]
        composer.action_send_mail()
        # Odoo manda el correo DESPUES del commit (postcommit), asi que aqui
        # todavia no se sabe si salio. `verificar` le dice al controlador que
        # correos revisar ya confirmada la transaccion: antes el telefono decia
        # "Se envio" con el correo atorado (16-oct-2026, la plantilla tenia un
        # servidor de correo archivado).
        return {'ok': True, 'mensaje': 'Se envió %s a %s.' % (orden.name, nuevo),
                'verificar': {'orden': orden.id, 'antes': sorted(antes), 'partner': destino.id}}

    def odoo_acceso(self):
        """Lo que necesita el robot de Foco para entrar a la app oficial de Odoo
        como el vendedor de este telefono: servidor, usuario y un codigo de un
        solo uso en lugar de la contraseña (ver foco_mobile_login, 16-oct-2026)."""
        self.ensure_one()
        user = self._vendedor()
        if not user or not user.active or user.share:
            raise UserError('Tu teléfono no está ligado a un usuario de Odoo. Pídele al '
                            'administrador que lo ligue en tu ficha de empleado.')
        ICP = self.env['ir.config_parameter'].sudo()
        if (user.has_group('base.group_system')
                and (ICP.get_param('foco.acceso_movil_admins') or '').lower() not in ('1', 'true', 'si', 'sí')):
            raise UserError('Entrar a Odoo desde el celular no está disponible para usuarios '
                            'administradores. Entra con tu contraseña.')
        codigo = self.env['foco.mobile.login'].emitir(self, user)
        base = (ICP.get_param('web.base.url') or '').rstrip('/')
        return {'ok': True, 'servidor': urlparse(base).netloc or base, 'url': base,
                'login': user.login, 'codigo': codigo, 'vence_s': VIGENCIA_S}

    def odoo_ir_a(self, res_model, res_id):
        """El vendedor quiere ver esta cotizacion en la app oficial de Odoo: se
        deja el aviso en su usuario y la pantalla de Odoo salta ahi al arrancar
        dentro de la app (foco_ir_http, 16-oct-2026). Solo lo que el ve."""
        self.ensure_one()
        user = self._vendedor()
        if not user:
            raise UserError('Tu teléfono no está ligado a un usuario de Odoo.')
        if res_model != 'sale.order':
            raise UserError('Eso no se puede abrir desde el teléfono.')
        orden = self.env['sale.order'].with_user(user).browse(int(res_id or 0)).exists()
        if not orden or not orden.has_access('read'):
            raise UserError('Esa cotización ya no existe o no la puedes ver con tus permisos.')
        user.sudo().write({'foco_ir_a': '/odoo/sales/%d' % orden.id,
                           'foco_ir_a_at': fields.Datetime.now()})
        return {'ok': True}

    def cliente_telefono(self, partner_id, numero):
        """Guarda en el cliente el numero que el vendedor escribio en el telefono
        para mandarle una cotizacion por WhatsApp (16-oct-2026). Con SUS permisos."""
        self.ensure_one()
        user = self._vendedor()
        if not user:
            raise UserError('Tu teléfono no está ligado a un usuario de Odoo.')
        numero = (numero or '').strip()
        if len([c for c in numero if c.isdigit()]) < 7:
            raise UserError('«%s» no parece un número de teléfono.' % numero)
        cliente = self.env['res.partner'].with_user(user).browse(int(partner_id or 0)).exists()
        if not cliente:
            raise UserError('Ese cliente ya no existe en Odoo.')
        cliente = cliente.commercial_partner_id
        cliente.write({'phone': numero})
        return {'ok': True, 'mensaje': 'Guardé %s en %s.' % (numero, cliente.display_name)}

    def cliente_ubicacion(self, partner_id, lat, lon, precision=0.0):
        """Corrige el PIN (ubicacion) de un cliente con el GPS actual del
        vendedor, cuando la direccion que trae esta mal. Con SUS permisos. Mueve
        el mismo registro que ve la cartera (no el comercial), porque es el que
        usa la verificacion 'en sitio' de las visitas."""
        self.ensure_one()
        user = self._vendedor()
        if not user:
            raise UserError('Tu teléfono no está ligado a un usuario de Odoo.')
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            raise UserError('No tengo una ubicación válida para fijar.')
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
            raise UserError('No tengo una ubicación válida para fijar.')
        cliente = self.env['res.partner'].with_user(user).browse(int(partner_id or 0)).exists()
        if not cliente:
            raise UserError('Ese cliente ya no existe en Odoo.')
        cliente.write({'partner_latitude': lat, 'partner_longitude': lon})
        try:
            cliente.message_post(body='Ubicación corregida desde el teléfono (%.6f, %.6f%s).'
                                      % (lat, lon, ', ±%dm' % int(precision) if precision else ''))
        except Exception:
            pass
        return {'ok': True, 'lat': lat, 'lon': lon,
                'mensaje': 'Actualicé la ubicación de %s.' % cliente.display_name}

    def crear_cliente(self, nombre, lat, lon, telefono='', precision=0.0):
        """Registra un cliente NUEVO (prospecto) con su pin SIN estar en el sitio:
        la ubicacion llega de lo que el cliente compartio por WhatsApp/Maps, de un
        toque en el mapa, o del GPS. Nombre obligatorio, telefono opcional,
        ubicacion obligatoria. Corre con los permisos del vendedor y queda
        asignado a el, como un prospecto (igual que el alta por check-in)."""
        self.ensure_one()
        user = self._vendedor()
        if not user:
            raise UserError('Tu teléfono no está ligado a un usuario de Odoo.')
        nombre = (nombre or '').strip()
        if not nombre:
            raise UserError('Ponle un nombre al cliente.')
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            raise UserError('No tengo una ubicación válida para el cliente.')
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
            raise UserError('No tengo una ubicación válida para el cliente.')
        vals = {
            'name': nombre[:120],
            'company_type': 'company',
            'foco_cliente_ventas': True, 'foco_estatus': 'prospecto',
            'user_id': user.id,
            'partner_latitude': lat, 'partner_longitude': lon,
        }
        tel = (telefono or '').strip()
        if tel:
            vals['phone'] = tel[:32]
        cliente = self.env['res.partner'].with_user(user).create(vals)
        try:
            cliente.message_post(
                body='Cliente registrado desde el teléfono con la ubicación compartida '
                     '(%.6f, %.6f%s).' % (lat, lon, ', ±%dm' % int(precision) if precision else ''))
        except Exception:  # noqa: BLE001
            pass
        return {'ok': True, 'id': cliente.id, 'lat': lat, 'lon': lon,
                'name': cliente.display_name,
                'mensaje': 'Registré a %s.' % cliente.display_name}

    # ----------------------------------------------------- viaticos (gastos)
    @staticmethod
    def _b64(d):
        """Quita el prefijo data: de una imagen en base64 del telefono."""
        d = d or ''
        return d.split(',', 1)[1] if (isinstance(d, str) and d.strip().startswith('data:') and ',' in d) else d

    def ocr_ticket(self, fotos, tipo='gasolina'):
        """Lee la(s) foto(s) del ticket con la IA de vision y devuelve los datos
        para que el vendedor los CONFIRME antes de guardar. `fotos`: lista de
        {mime, data(b64)}. No crea nada."""
        self.ensure_one()
        pares = []
        for f in (fotos or []):
            d = self._b64((f or {}).get('data'))
            if d:
                pares.append(((f or {}).get('mime') or 'image/jpeg', d))
        if not pares:
            raise UserError('Toma la foto del ticket primero.')
        return self.env['foco.openai'].leer_ticket(pares, tipo=tipo)

    def crear_viatico(self, datos):
        """Registra un gasto desde el telefono (apartado Gastos). La foto SIEMPRE
        se conserva como evidencia. En gasolina lleva vehiculo/litros/odometro, que
        alimentan el rendimiento y el cruce con el GPS."""
        self.ensure_one()
        emp = self.employee_id
        if not emp:
            raise UserError('Tu teléfono no está ligado a un empleado.')
        datos = datos or {}
        tipos = dict(self.env['foco.viatico'].TIPOS)
        tipo = datos.get('tipo') if datos.get('tipo') in tipos else 'otro'
        foto = self._b64(datos.get('foto'))
        if not foto:
            raise UserError('Falta la foto del ticket.')
        try:
            monto = float(datos.get('monto') or 0)
        except (TypeError, ValueError):
            monto = 0.0
        if monto <= 0:
            raise UserError('Pon el monto del gasto.')
        moneda = (emp.company_id or self.env.company).currency_id
        cod = (datos.get('moneda') or '').strip().upper()
        if cod:
            m = self.env['res.currency'].sudo().search([('name', '=', cod)], limit=1)
            if m:
                moneda = m
        vals = {
            'tipo': tipo, 'employee_id': emp.id,
            'company_id': (emp.company_id or self.env.company).id,
            'fecha': datos.get('fecha') or fields.Date.context_today(self),
            'monto': monto, 'currency_id': moneda.id, 'foto': foto,
            'lugar': (datos.get('lugar') or '')[:120],
            'lat': float(datos.get('lat') or 0.0), 'lon': float(datos.get('lon') or 0.0),
            'ocr_json': datos.get('ocr_json') or False,
        }
        try:
            if datos.get('tipo_cambio'):
                vals['tipo_cambio'] = float(datos['tipo_cambio'])
        except (TypeError, ValueError):
            pass
        if tipo == 'gasolina':
            fo = self._b64(datos.get('foto_odometro'))
            if fo:
                vals['foto_odometro'] = fo
            if datos.get('vehiculo_id'):
                veh = self.env['foco.vehiculo'].sudo().browse(int(datos['vehiculo_id'])).exists()
                if veh:
                    vals['vehiculo_id'] = veh.id
            for k, caster in (('litros', float), ('precio_litro', float), ('odometro', int)):
                if datos.get(k) not in (None, ''):
                    try:
                        vals[k] = caster(datos[k])
                    except (TypeError, ValueError):
                        pass
        viatico = self.env['foco.viatico'].sudo().create(vals)
        return {'ok': True, 'id': viatico.id, 'mensaje': 'Gasto registrado.'}

    def mis_viaticos(self, limite=30):
        """Los gastos recientes del vendedor + su presupuesto de la semana."""
        self.ensure_one()
        emp = self.employee_id
        if not emp:
            return {'gastos': [], 'presupuesto': None, 'moneda': '', 'simbolo': ''}
        Viatico = self.env['foco.viatico'].sudo()
        gastos = Viatico.search([('employee_id', '=', emp.id)], limit=int(limite))
        hoy = fields.Date.context_today(self)
        pres = self.env['foco.viatico.presupuesto'].sudo().search(
            [('employee_id', '=', emp.id), ('desde', '<=', hoy), ('hasta', '>=', hoy)], limit=1)
        moneda = (emp.company_id or self.env.company).currency_id
        return {
            'moneda': moneda.name, 'simbolo': moneda.symbol or '',
            'presupuesto': ({'monto': pres.monto, 'consumido': pres.consumido,
                             'restante': pres.restante, 'desde': str(pres.desde),
                             'hasta': str(pres.hasta)} if pres else None),
            'gastos': [{'id': g.id, 'tipo': g.tipo, 'fecha': str(g.fecha), 'monto': g.monto,
                        'lugar': g.lugar or '', 'estado': g.estado,
                        'vehiculo': g.vehiculo_id.name or ''} for g in gastos],
        }

    def mis_vehiculos(self):
        """Los vehiculos de la empresa del vendedor (para elegir en una carga)."""
        self.ensure_one()
        emp = self.employee_id
        comp = (emp.company_id if emp else False) or self.env.company
        vehs = self.env['foco.vehiculo'].sudo().search(
            [('company_id', '=', comp.id), ('active', '=', True)], order='name')
        return [{'id': v.id, 'name': v.name, 'matricula': v.matricula,
                 'odometro': v.odometro_actual} for v in vehs]

    def cotizacion_revision(self, order_id):
        """El boton «Enviar a revision» de Odoo, con el usuario del vendedor: le
        llega el chat y la actividad a los aprobadores, y al vendedor la respuesta."""
        self.ensure_one()
        orden, user = self._orden_del_vendedor(order_id)
        if not hasattr(orden, 'action_enviar_revision'):
            raise UserError('La aprobación de cotizaciones no está instalada en Odoo.')
        orden.action_enviar_revision()
        return {'ok': True, 'mensaje': 'Mandaste %s a revisión. Cuando la aprueben te llega '
                                      'un chat y ya la puedes enviar.' % orden.name}

    def registrar_visitas(self, visitas):
        """Crea foco.visita desde el telefono (check-in del boton 'Llegue').
        Deduplica por `uuid` (el telefono reenvia sin internet y no debe
        duplicar). Un prospecto nuevo -sin partner_id pero con nombre- se crea
        con las coordenadas del check-in como su pin. Devuelve los uuid
        guardados, para que el telefono los saque de su cola."""
        self.ensure_one()
        Visita = self.env['foco.visita'].sudo()
        Partner = self.env['res.partner'].sudo()
        emp = self.employee_id
        user = emp.user_id if emp else self.env['res.users']
        guardados = []
        sel = dict(Visita._fields['resultado'].selection)
        for v in (visitas or []):
            uuid = (v.get('uuid') or '').strip()
            if not uuid:
                continue
            fase = (v.get('fase') or 'llegada').strip()
            existing = Visita.search([('device_uuid', '=', uuid)], limit=1)
            # CIERRE: la visita ya la creo la 'llegada' (al llegar); aqui, al
            # TERMINAR, se le pega el resultado/nota/proximo y la hora de salida.
            if fase == 'cierre':
                if existing:
                    upd = {'check_out': fields.Datetime.now()}
                    res = (v.get('resultado') or '').strip()
                    if res in sel:
                        upd['resultado'] = res
                    nota = (v.get('nota') or '').strip()
                    if nota:
                        upd['nota'] = nota
                    pf = (v.get('proxima_fecha') or '').strip()
                    if pf:
                        upd['proxima_fecha'] = pf[:10]
                    existing.write(upd)
                    foto = v.get('foto')
                    if foto:
                        try:
                            existing.message_post(
                                body='Foto de la visita',
                                attachments=[('visita_%s.jpg' % uuid[:8], base64.b64decode(foto))])
                        except Exception:
                            pass
                if uuid not in guardados:
                    guardados.append(uuid)
                continue
            # LLEGADA (check-in con GPS) o MANUAL (registrada sin estar en sitio):
            # crear la visita (idempotente si ya estaba). La manual se captura
            # completa de una vez (resultado incluido) y no lleva coordenadas.
            manual = (fase == 'manual')
            if existing:
                if uuid not in guardados:
                    guardados.append(uuid)
                continue
            lat = v.get('lat') or 0.0
            lon = v.get('lon') or 0.0
            partner = None
            if v.get('partner_id'):
                partner = Partner.browse(int(v['partner_id']))
                if not partner.exists():
                    partner = None
            # Un prospecto nuevo se crea con su pin SOLO desde el GPS, no a mano
            # (una manual no tiene coordenadas que darle de pin).
            if partner is None and not manual and (v.get('nuevo_nombre') or '').strip():
                partner = Partner.create({
                    'name': v['nuevo_nombre'].strip()[:120],
                    'company_type': 'company',
                    'foco_cliente_ventas': True, 'foco_estatus': 'prospecto',
                    'user_id': user.id if user else False,
                    'partner_latitude': lat, 'partner_longitude': lon,
                })
            if partner is None:
                continue
            vals = {
                'device_uuid': uuid, 'device_id': self.id,
                'employee_id': emp.id if emp else False,
                'user_id': user.id if user else False,
                'partner_id': partner.id,
                'check_in': v.get('at') or fields.Datetime.now(),
                'latitude': lat, 'longitude': lon,
                'precision_m': v.get('accuracy') or 0.0,
                'origen': 'manual' if manual else 'gps',
                'nota': (v.get('nota') or '').strip() or False,
                'grabacion_consentida': bool(v.get('grabacion_consentida')),
            }
            if manual:
                vals['check_out'] = v.get('at') or fields.Datetime.now()
            res = (v.get('resultado') or '').strip()
            if res in dict(Visita._fields['resultado'].selection):
                vals['resultado'] = res
            pf = (v.get('proxima_fecha') or '').strip()
            if pf:
                vals['proxima_fecha'] = pf[:10]
            visita = Visita.create(vals)
            # Foto de la visita (opcional): queda en el chatter y como adjunto.
            foto = v.get('foto')
            if foto:
                try:
                    visita.message_post(
                        body='Foto de la visita',
                        attachments=[('visita_%s.jpg' % uuid[:8], base64.b64decode(foto))])
                except Exception:
                    pass
            guardados.append(uuid)
        return guardados

    def guardar_audio_visita(self, uuid, data, duracion=0, name='visita.m4a'):
        """Recibe el audio de una visita (lo sube el telefono tras cortar la
        grabacion) y lo deja PENDIENTE para que la IA lo analice. Solo si el
        interruptor del servidor esta encendido; dedup por visita."""
        self.ensure_one()
        if not self.env['foco.settings'].sudo().get_settings().visita_grabar:
            return {'ok': False, 'error': 'off'}
        visita = self.env['foco.visita'].sudo().search(
            [('device_uuid', '=', uuid), ('device_id', '=', self.id)], limit=1)
        if not visita:
            return {'ok': False, 'error': 'no_visita'}
        if visita.audio:
            return {'ok': True}          # ya estaba: reenvio
        visita.write({
            'audio': base64.b64encode(data or b''), 'audio_name': name or 'visita.m4a',
            'audio_duracion_s': int(duracion or 0), 'audio_estado': 'pendiente',
        })
        return {'ok': True}

    def guardar_audio_llamada(self, uuid, data, duracion=0, name='llamada.wav',
                              started=None, consent=False, origen='whatsapp'):
        """Recibe el WAV de una llamada (WhatsApp o celular) que el telefono sube
        al colgar y lo deja PENDIENTE para el coaching. Dedup por (telefono, uuid).
        Solo si el interruptor del servidor esta encendido."""
        self.ensure_one()
        if not self.env['foco.settings'].sudo().get_settings().coaching_llamadas:
            return {'ok': False, 'error': 'off'}
        if origen not in ('whatsapp', 'celular', 'otro'):
            origen = 'whatsapp'
        Llamada = self.env['foco.llamada'].sudo()
        rec = Llamada.search([('uuid', '=', uuid), ('device_id', '=', self.id)], limit=1)
        if rec and rec.audio:
            return {'ok': True}          # ya estaba: reenvio
        vals = {
            'device_id': self.id, 'uuid': uuid,
            'audio': base64.b64encode(data or b''),
            'audio_name': name or 'llamada.wav',
            'audio_estado': 'pendiente',
            'duration_s': int(duracion or 0),
            'grabacion_consentida': bool(consent),
            'origen': origen,
        }
        if started:
            vals['started_at'] = started
        if rec:
            rec.write(vals)
        else:
            Llamada.create(vals)
        return {'ok': True}

    def mis_llamadas_coaching(self, limite=20):
        """Las llamadas YA analizadas de este telefono, para que el vendedor lea
        su coaching en la app."""
        self.ensure_one()
        recs = self.env['foco.llamada'].sudo().search(
            [('device_id', '=', self.id), ('audio_estado', '=', 'listo')],
            limit=int(limite or 20))
        return [r.coaching_para_telefono() for r in recs]


class FocoLocation(models.Model):
    _name = 'foco.location'
    _description = 'Ubicacion reportada por un dispositivo movil'
    _order = 'at desc'

    device_id = fields.Many2one('foco.mobile.device', string='Dispositivo',
                                required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='device_id.employee_id', store=True,
                                  string='Empleado', index=True)
    at = fields.Datetime(string='Momento', required=True, index=True)
    lat = fields.Float(string='Latitud', digits=(10, 7))
    lon = fields.Float(string='Longitud', digits=(10, 7))
    accuracy = fields.Float(string='Precision (m)')
    speed = fields.Float(string='Velocidad (m/s)')
    battery = fields.Integer(string='Bateria %')
    source = fields.Char(string='Fuente',
                         help='gps, network o fused, segun de donde salio el punto.')

    def action_abrir_mapa(self):
        """Abre el punto en un mapa externo. Odoo Community no trae vista de
        mapa, asi que se resuelve con un enlace a OpenStreetMap -sin depender
        de una API con llave-."""
        self.ensure_one()
        url = 'https://www.openstreetmap.org/?mlat=%s&mlon=%s#map=17/%s/%s' % (
            self.lat, self.lon, self.lat, self.lon)
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}


class FocoMobileUsage(models.Model):
    _name = 'foco.mobile.usage'
    _description = 'Uso de apps en un dispositivo movil'
    _order = 'date desc, foreground_seconds desc'

    _uniq = models.Constraint('unique(device_id, date, package)',
                              'Ya hay un renglon de uso para ese dia y paquete.')

    device_id = fields.Many2one('foco.mobile.device', string='Dispositivo',
                                required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='device_id.employee_id', store=True,
                                  string='Empleado', index=True)
    date = fields.Date(string='Dia', required=True, index=True)
    package = fields.Char(string='Paquete', required=True, index=True,
                          help='Nombre del paquete Android, p.ej. com.whatsapp.')
    app_label = fields.Char(string='Aplicacion')
    foreground_seconds = fields.Integer(string='Primer plano (s)')
    foreground_hours = fields.Float(string='Primer plano (h)',
                                    compute='_compute_horas', store=True)

    @api.depends('foreground_seconds')
    def _compute_horas(self):
        for rec in self:
            rec.foreground_hours = round((rec.foreground_seconds or 0) / 3600.0, 3)


class FocoMobileApp(models.Model):
    """Catalogo de apps que ha visto la flota, espejo movil de `foco.app`. Se
    auto-descubre del uso que ya reporta el telefono: cada paquete nuevo entra
    SIN registrar. Sirve para decidir a que apps se les toma captura periodica:
    a una app REGISTRADA (herramienta de trabajo reconocida) no se le toma; a
    una NO registrada (personal o desconocida) si, cada N min mientras este al
    frente. `no_captura` es una garantia aparte: apps que NUNCA se fotografian
    (banca, gestor de contrasenas), esten registradas o no."""
    _name = 'foco.mobile.app'
    _description = 'Aplicacion movil vista en la flota'
    _order = 'registrada, app_label, package'

    _uniq = models.Constraint('unique(package)',
                              'Ese paquete ya esta en el catalogo.')

    package = fields.Char(string='Paquete', required=True, index=True)
    app_label = fields.Char(string='Aplicacion')
    registrada = fields.Boolean(
        string='Registrada (app de trabajo)', default=False, index=True,
        help='Marcala si la empresa reconoce esta app como herramienta de '
             'trabajo. A una app REGISTRADA no se le toma captura periodica; a '
             'las NO registradas si, cada N min mientras esten al frente.')
    no_captura = fields.Boolean(
        string='Nunca capturar con esta app al frente', default=False,
        help='Con esta app al frente NUNCA se toma captura, este registrada o '
             'no: la banca, el gestor de contrasenas, salud. Es una garantia de '
             'privacidad, no una clasificacion.')
    first_seen = fields.Datetime(string='Vista por primera vez', readonly=True)
    last_seen = fields.Datetime(string='Vista por ultima vez', readonly=True, index=True)
    # Icono de la app (PNG 192 px) tomado UNA vez de su ficha publica en la Play
    # (og:image). El telefono lo pinta en la escena "app en camino" mientras la
    # instala: la app todavia no esta en el equipo, asi que no puede leerlo de ahi.
    icon = fields.Binary(string='Icono', attachment=True)
    icon_intentos = fields.Integer(default=0)

    def _asegurar_icono(self):
        """Baja el icono (y el nombre oficial, si falta) de la ficha publica de la
        Play Store. Hasta 3 intentos; nunca falla hacia afuera: sin icono el
        telefono pinta la inicial del nombre."""
        import base64
        import logging
        import re
        import requests
        from odoo.tools.image import image_process
        log = logging.getLogger(__name__)
        for app in self:
            if app.icon or (app.icon_intentos or 0) >= 3 or not app.package:
                continue
            vals = {'icon_intentos': (app.icon_intentos or 0) + 1}
            try:
                r = requests.get('https://play.google.com/store/apps/details',
                                 params={'id': app.package, 'hl': 'es'}, timeout=6,
                                 headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) '
                                                        'AppleWebKit/537.36 Chrome/124 Safari/537.36'})
                if r.ok:
                    m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', r.text)
                    if m:
                        # La imagen de la Play acepta el tamano en la URL (=s192 → PNG 192 px).
                        url = m.group(1).split('=')[0] + '=s192'
                        ri = requests.get(url, timeout=6)
                        if ri.ok and ri.content:
                            png = image_process(ri.content, size=(192, 192), output_format='PNG')
                            vals['icon'] = base64.b64encode(png)
                    if not app.app_label:
                        t = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', r.text)
                        if t:
                            nombre = re.sub(r'\s*[-–|].*?Google Play.*$', '', t.group(1)).strip()
                            if nombre:
                                vals['app_label'] = nombre[:120]
            except Exception as e:  # red, HTML distinto, imagen rara: no pasa nada
                log.info('foco: sin icono de la Play para %s: %s', app.package, e)
            app.sudo().write(vals)

    def _icono_b64(self):
        """El icono como texto base64 para el telefono ('' si no hay)."""
        self.ensure_one()
        if not self.icon:
            return ''
        return self.icon.decode('ascii') if isinstance(self.icon, bytes) else str(self.icon)

    @api.model
    def descubrir(self, pares):
        """Upsert del catalogo desde el uso del telefono. `pares` = lista de
        (package, label). Las nuevas entran SIN registrar; nunca se degrada una
        etiqueta a vacio. Robusto a que dos telefonos vean el mismo paquete
        nuevo a la vez (savepoint por alta)."""
        etq = {}
        for pkg, label in (pares or []):
            pkg = (pkg or '').strip()
            if not pkg:
                continue
            if label:
                etq[pkg] = label
            else:
                etq.setdefault(pkg, '')
        if not etq:
            return
        ahora = fields.Datetime.now()
        existentes = {a.package: a for a in self.sudo().search(
            [('package', 'in', list(etq.keys()))])}
        for pkg, label in etq.items():
            rec = existentes.get(pkg)
            if rec:
                vals = {'last_seen': ahora}
                if label and label != rec.app_label:
                    vals['app_label'] = label
                rec.write(vals)
            else:
                try:
                    with self.env.cr.savepoint():
                        self.sudo().create({
                            'package': pkg, 'app_label': label or '',
                            'first_seen': ahora, 'last_seen': ahora,
                        })
                except Exception:
                    # otro envio la creo en paralelo: no es error
                    pass

    @api.model
    def registradas(self):
        """Paquetes que la empresa reconoce como apps de trabajo."""
        return self.sudo().search([('registrada', '=', True)]).mapped('package')

    @api.model
    def no_captura_apps(self):
        """Paquetes con los que NUNCA se toma captura."""
        return self.sudo().search([('no_captura', '=', True)]).mapped('package')


class FocoCall(models.Model):
    _name = 'foco.call'
    _description = 'Llamada registrada en un dispositivo movil'
    _order = 'at desc'

    # Idempotencia real: el registro de llamadas de Android da un _ID por
    # llamada, que viaja como `external_id`. Sin el, un reenvio del mismo lote
    # duplicaria llamadas.
    _uniq = models.Constraint('unique(device_id, external_id)',
                              'Esa llamada ya estaba registrada.')

    device_id = fields.Many2one('foco.mobile.device', string='Dispositivo',
                                required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='device_id.employee_id', store=True,
                                  string='Empleado', index=True)
    at = fields.Datetime(string='Momento', required=True, index=True)
    number = fields.Char(string='Numero')
    contact = fields.Char(string='Contacto',
                          help='Nombre en la agenda del telefono, si lo hay.')
    direction = fields.Selection([
        ('in', 'Entrante'), ('out', 'Saliente'), ('missed', 'Perdida'),
        ('rejected', 'Rechazada'), ('blocked', 'Bloqueada'), ('other', 'Otra'),
    ], string='Direccion', index=True)
    duration_seconds = fields.Integer(string='Duracion (s)')
    duration_text = fields.Char(string='Duracion', compute='_compute_dur')
    external_id = fields.Char(string='ID en el equipo', index=True, copy=False)

    @api.depends('duration_seconds')
    def _compute_dur(self):
        for rec in self:
            s = rec.duration_seconds or 0
            rec.duration_text = '%d:%02d' % (s // 60, s % 60)


class FocoMobileCommand(models.Model):
    """Orden puntual a un telefono (hoy solo 'captura ahora'). Viaja en la
    respuesta del ingest; el telefono la ejecuta y la captura que sube trae el
    id de la orden para cerrar el circulo."""
    _name = 'foco.mobile.command'
    _description = 'Orden a un dispositivo movil'
    _order = 'create_date desc'

    device_id = fields.Many2one('foco.mobile.device', string='Dispositivo',
                                required=True, ondelete='cascade', index=True)
    kind = fields.Selection([('screenshot', 'Solicitar captura')],
                            string='Tipo', default='screenshot', required=True)
    payload = fields.Char(string='Motivo')
    state = fields.Selection([
        ('pending', 'Pendiente'), ('sent', 'Enviada'), ('done', 'Hecha'),
    ], string='Estado', default='pending', index=True)
    sent_at = fields.Datetime(readonly=True)
    done_at = fields.Datetime(readonly=True)


class FocoMobileCapture(models.Model):
    """Captura de pantalla de un telefono. Espeja `foco.capture` de la laptop:
    mismos disparadores (manual / sin_clasificar / monitoreo), interruptor
    apagado de fabrica, y una fecha de caducidad por imagen para la purga.

    En Android el pantallazo SILENCIOSO solo es posible via el servicio de
    accesibilidad (takeScreenshot, Android 11+), el mismo que protege la app.
    'sin clasificar' aqui = un paquete del que aun no hay captura: se toma UNA
    para ver que app es y ese paquete no vuelve a dispararse."""
    _name = 'foco.mobile.capture'
    _description = 'Captura de pantalla de un movil'
    _order = 'at desc'

    device_id = fields.Many2one('foco.mobile.device', string='Dispositivo',
                                required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='device_id.employee_id', store=True,
                                  string='Empleado', index=True)
    department_id = fields.Many2one(related='device_id.employee_id.department_id',
                                    store=True, string='Departamento')
    at = fields.Datetime(string='Momento', required=True, index=True)
    trigger = fields.Selection([
        ('manual', 'La pidio un administrador'),
        ('sin_clasificar', 'Aplicacion nueva/sin clasificar'),
        ('monitoreo', 'Monitoreo periodico de la app'),
    ], string='Por que se tomo', required=True, index=True)
    reason = fields.Char(string='Motivo')
    requested_by = fields.Many2one('res.users', string='La pidio', readonly=True)
    package = fields.Char(string='Paquete', index=True)
    app_label = fields.Char(string='Aplicacion al frente')
    image = fields.Binary(string='Imagen', attachment=True)
    width = fields.Integer(readonly=True)
    height = fields.Integer(readonly=True)
    bytes = fields.Integer(string='Peso (bytes)', readonly=True)
    expires_at = fields.Datetime(string='Caduca', readonly=True, index=True)
    notified = fields.Boolean(string='Se le aviso a la persona', readonly=True)

    @api.model
    def registrar(self, device, datos):
        """Guarda una captura que sube el telefono. Devuelve el id, o 0. El
        interruptor se re-verifica AQUI: apagado significa que no se guarda nada,
        aunque un agente viejo o una llamada a mano manden la imagen."""
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if not (ajustes.mobile_enabled and ajustes.mobile_screenshot_enabled):
            return 0
        imagen = datos.get('image')
        if not imagen:
            return 0
        disparador = datos.get('trigger')
        if disparador not in ('manual', 'sin_clasificar', 'monitoreo'):
            disparador = 'sin_clasificar'
        at = self.env['foco.event']._parse_utc(datos.get('at')) or fields.Datetime.now()
        dias = ajustes.screenshot_retention_days or 0
        vals = {
            'device_id': device.id,
            'at': at,
            'trigger': disparador,
            'image': imagen,
            'package': (datos.get('package') or '')[:128],
            'app_label': (datos.get('app_label') or '')[:128],
            'width': int(datos.get('width') or 0),
            'height': int(datos.get('height') or 0),
            'bytes': int(datos.get('bytes') or 0),
            'notified': bool(datos.get('notified')),
            'expires_at': (at + timedelta(days=dias)) if dias > 0 else False,
        }
        if datos.get('command_id'):
            orden = self.env['foco.mobile.command'].sudo().browse(
                int(datos['command_id'])).exists()
            if orden and orden.device_id == device:
                vals['reason'] = orden.payload or ''
                vals['requested_by'] = orden.create_uid.id
                orden.write({'state': 'done', 'done_at': fields.Datetime.now()})
        return self.sudo().create(vals).id

    @api.model
    def paquetes_capturados(self, device):
        """Paquetes de los que ya hay una captura 'sin clasificar' en este
        equipo, para que el telefono no repita: la captura de descubrimiento
        existe para IDENTIFICAR la app, una vez hay imagen no aporta repetir."""
        caps = self.sudo().search([
            ('device_id', '=', device.id),
            ('trigger', '=', 'sin_clasificar'),
            ('package', '!=', False)])
        return list({p for p in caps.mapped('package') if p})
