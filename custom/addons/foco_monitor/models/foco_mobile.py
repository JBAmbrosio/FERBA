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

import secrets
from datetime import datetime, timedelta

from odoo import api, fields, models

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

    @api.model
    def dashboard(self, desde, hasta):
        """Todo lo que pinta el Tablero movil, agregado en el servidor, en un
        solo viaje: KPIs, marcadores y recorrido para el mapa, resumen de
        llamadas y top de apps. `desde`/`hasta` son dias 'YYYY-MM-DD'.

        Respeta las reglas de registro: quien tiene alcance de un departamento
        solo ve sus equipos, porque parte de `self.search([])` (no sudo)."""
        Loc = self.env['foco.location']
        Call = self.env['foco.call']
        Usage = self.env['foco.mobile.usage']

        d1 = fields.Date.to_date(desde) or fields.Date.context_today(self)
        d2 = fields.Date.to_date(hasta) or d1
        if d2 < d1:
            d1, d2 = d2, d1
        dt1 = datetime.combine(d1, datetime.min.time())
        dt2 = datetime.combine(d2 + timedelta(days=1), datetime.min.time())
        ahora = fields.Datetime.now()
        dom_loc = [('at', '>=', dt1), ('at', '<', dt2)]
        dom_call = [('at', '>=', dt1), ('at', '<', dt2)]
        dom_use = [('date', '>=', d1), ('date', '<=', d2)]

        devices = self.search([])
        dev_ids = devices.ids

        # --- conteos por dispositivo en el rango ---
        puntos = {}
        for g in Loc._read_group(dom_loc + [('device_id', 'in', dev_ids)],
                                 ['device_id'], ['__count']):
            puntos[g[0].id] = g[1]
        llam = {}
        for g in Call._read_group(dom_call + [('device_id', 'in', dev_ids)],
                                  ['device_id'], ['__count']):
            llam[g[0].id] = g[1]

        # --- filas de dispositivos + marcadores de ultima posicion ---
        dev_rows, marcadores, en_linea, retirados, sin_senal = [], [], 0, 0, 0
        for dv in devices:
            online = bool(dv.last_seen and
                          (ahora - dv.last_seen).total_seconds() < MOBILE_ONLINE_SECS)
            # estado: retirado (aviso del equipo) manda sobre en linea / sin señal.
            if dv.removed:
                estado = 'removed'
                retirados += 1
            elif online:
                estado = 'online'
                en_linea += 1
            else:
                estado = 'silent'
                if dv.last_seen:      # alguna vez reporto: su silencio importa
                    sin_senal += 1
            dev_rows.append({
                'estado': estado,
                'removed_at': fields.Datetime.to_string(dv.removed_at) if dv.removed_at else '',
                'id': dv.id, 'name': dv.name or '',
                'employee': dv.employee_id.display_name or 'Sin empleado',
                'dept': dv.department_id.display_name or '',
                'battery': dv.battery, 'online': online,
                'last_seen': fields.Datetime.to_string(dv.last_seen) if dv.last_seen else '',
                'last_lat': dv.last_lat, 'last_lon': dv.last_lon,
                'points': puntos.get(dv.id, 0), 'calls': llam.get(dv.id, 0),
                'health': dv.health_status, 'health_issues': dv.health_issues or '',
            })
            if dv.last_lat or dv.last_lon:
                marcadores.append({
                    'id': dv.id, 'name': dv.name or '',
                    'employee': dv.employee_id.display_name or '',
                    'lat': dv.last_lat, 'lon': dv.last_lon, 'online': online,
                    'estado': estado, 'battery': dv.battery,
                    'at': fields.Datetime.to_string(dv.last_fix_at) if dv.last_fix_at else '',
                })

        # --- recorrido del rango, por dispositivo (para las lineas del mapa) ---
        track = {}
        pts = Loc.search_read(dom_loc + [('device_id', 'in', dev_ids)],
                              ['device_id', 'lat', 'lon'], order='device_id, at', limit=6000)
        for p in pts:
            did = p['device_id'][0]
            track.setdefault(str(did), []).append([p['lat'], p['lon']])

        # --- llamadas ---
        dir_lbl = dict(Call._fields['direction'].selection)
        por_dir = {'in': 0, 'out': 0, 'missed': 0, 'rejected': 0, 'blocked': 0, 'other': 0}
        for g in Call._read_group(dom_call + [('device_id', 'in', dev_ids)],
                                  ['direction'], ['__count']):
            if g[0]:
                por_dir[g[0]] = g[1]
        total_seg = sum(Call.search(dom_call + [('device_id', 'in', dev_ids)]).mapped('duration_seconds'))
        por_dia = []
        for g in Call._read_group(dom_call + [('device_id', 'in', dev_ids)],
                                  ['at:day'], ['__count']):
            por_dia.append({'dia': fields.Date.to_string(g[0].date() if hasattr(g[0], 'date') else g[0]),
                            'n': g[1]})
        # top numeros/contactos
        top = {}
        for g in Call._read_group(dom_call + [('device_id', 'in', dev_ids)],
                                  ['number'], ['__count', 'duration_seconds:sum']):
            num = g[0] or '—'
            top[num] = {'number': g[0] or '', 'count': g[1],
                        'minutes': round((g[2] or 0) / 60.0, 1), 'contact': ''}
        for c in Call.search_read(
                dom_call + [('device_id', 'in', dev_ids), ('contact', '!=', False),
                            ('contact', '!=', '')], ['number', 'contact'], limit=3000):
            n = c['number'] or '—'
            if n in top and not top[n]['contact']:
                top[n]['contact'] = c['contact']
        top_list = sorted(top.values(), key=lambda x: (-x['count'], -x['minutes']))[:8]

        # --- top de apps del movil en el rango ---
        apps = []
        for g in Usage._read_group(dom_use + [('device_id', 'in', dev_ids)],
                                   ['app_label'], ['foreground_seconds:sum']):
            etq = g[0] or 'Sin nombre'
            apps.append({'label': etq, 'hours': round((g[1] or 0) / 3600.0, 2)})
        apps = sorted(apps, key=lambda x: -x['hours'])[:8]

        return {
            'kpis': {
                'devices': len(devices), 'online': en_linea,
                'retirados': retirados, 'sin_senal': sin_senal,
                'con_problemas': sum(1 for r in dev_rows
                                     if r.get('health') in ('bad', 'warn')),
                'points': sum(puntos.values()),
                'calls': sum(por_dir.values()),
                'calls_in': por_dir['in'], 'calls_out': por_dir['out'],
                'calls_missed': por_dir['missed'],
                'minutes': round(total_seg / 60.0, 1),
            },
            'devices': dev_rows,
            'markers': marcadores,
            'track': track,
            'calls': {
                'in': por_dir['in'], 'out': por_dir['out'], 'missed': por_dir['missed'],
                'rejected': por_dir['rejected'], 'blocked': por_dir['blocked'],
                'other': por_dir['other'],
                'total': sum(por_dir.values()), 'minutes': round(total_seg / 60.0, 1),
                'by_day': por_dia, 'top': top_list, 'labels': dir_lbl,
            },
            'apps': apps,
        }

    # ------------------------------------------------------- visitas de campo
    def clientes_para(self):
        """Cartera de clientes de ventas para la pantalla de Visita del telefono.
        Si el vendedor (usuario del empleado del equipo) tiene cartera asignada,
        devuelve la suya; si no (aun no se asignan), devuelve todos. Campos
        minimos para ubicar y elegir en el telefono."""
        self.ensure_one()
        Partner = self.env['res.partner'].sudo()
        base = [('foco_cliente_ventas', '=', True)]
        emp = self.employee_id
        user = emp.user_id if emp else Partner.env['res.users']
        dom = base
        if user and Partner.search_count(base + [('user_id', '=', user.id)]):
            dom = base + [('user_id', '=', user.id)]
        filas = []
        for c in Partner.search(dom, limit=3000):
            filas.append({
                'id': c.id, 'name': c.display_name,
                'lat': c.partner_latitude, 'lon': c.partner_longitude,
                'zona': c.foco_zona or '', 'cultivo': c.foco_cultivo or '',
                'phone': c.phone or '', 'estatus': c.foco_estatus or 'prospecto',
            })
        return filas

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
        for v in (visitas or []):
            uuid = (v.get('uuid') or '').strip()
            if not uuid:
                continue
            if Visita.search_count([('device_uuid', '=', uuid)]):
                guardados.append(uuid)          # ya estaba: idempotente
                continue
            lat = v.get('lat') or 0.0
            lon = v.get('lon') or 0.0
            partner = None
            if v.get('partner_id'):
                partner = Partner.browse(int(v['partner_id']))
                if not partner.exists():
                    partner = None
            if partner is None and (v.get('nuevo_nombre') or '').strip():
                partner = Partner.create({
                    'name': v['nuevo_nombre'].strip()[:120],
                    'company_type': 'company',
                    'foco_cliente_ventas': True, 'foco_estatus': 'prospecto',
                    'user_id': user.id if user else False,
                    'partner_latitude': lat, 'partner_longitude': lon,
                })
            if partner is None:
                continue
            Visita.create({
                'device_uuid': uuid, 'device_id': self.id,
                'employee_id': emp.id if emp else False,
                'user_id': user.id if user else False,
                'partner_id': partner.id,
                'check_in': v.get('at') or fields.Datetime.now(),
                'latitude': lat, 'longitude': lon,
                'precision_m': v.get('accuracy') or 0.0,
                'origen': 'gps',
                'nota': (v.get('nota') or '').strip() or False,
            })
            guardados.append(uuid)
        return guardados


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
