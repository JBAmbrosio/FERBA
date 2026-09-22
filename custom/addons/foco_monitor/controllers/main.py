import base64
import csv
import io
import json
import logging
from datetime import datetime, time as _time, timedelta

import pytz

from odoo import fields, http
from odoo.http import content_disposition, request

_logger = logging.getLogger(__name__)


def _sec_to_h(v):
    try:
        return float(v) / 3600.0
    except (TypeError, ValueError):
        return 0.0


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


class FocoController(http.Controller):

    def _auth(self):
        key = request.httprequest.headers.get('X-Foco-Key')
        return request.env['foco.computer']._authenticate(key)

    def _body(self):
        try:
            return json.loads(request.httprequest.get_data() or b'{}')
        except ValueError:
            return None

    def _check_day_totals(self, computer, fechas):
        """Un dia no puede tener mas primer plano que tiempo transcurrido.

        Devuelve los avisos en vez de escribirlos: en un mismo envio pueden
        saltar varias anomalias y `integrity_alert` es un solo campo. Quien
        llama las junta y las guarda de una vez.
        """
        avisos = []
        if not fechas:
            return avisos
        Usage = request.env['foco.usage'].sudo()
        Settings = request.env['foco.settings'].sudo()
        tz = Settings._tzinfo_for(computer.employee_id, computer)
        ahora = pytz.UTC.localize(datetime.utcnow()).astimezone(tz)
        for fecha in fechas:
            d = fields.Date.to_date(fecha)
            if not d:
                continue
            if d > ahora.date():
                avisos.append("Datos fechados en el FUTURO (%s)" % d)
                continue
            # Para hoy, el tope es lo transcurrido desde medianoche.
            tope = (ahora - tz.localize(datetime.combine(d, _time(0, 0)))
                    ).total_seconds() / 3600.0 if d == ahora.date() else 24.0
            filas = Usage.search([('computer_id', '=', computer.id),
                                  ('date', '=', d)])
            total = sum((r.fg_active or 0) + (r.fg_idle or 0) for r in filas)
            # 10% de holgura: relojes, DST y redondeos no son manipulacion.
            if total > tope * 1.10 + 0.25:
                avisos.append("Totales imposibles el %s: %.1f h de primer plano "
                              "contra %.1f h transcurridas" % (d, total, tope))
        return avisos

    @http.route('/foco/ingest', type='http', auth='public',
                methods=['POST'], csrf=False)
    def ingest(self, **kw):
        computer = self._auth()
        if not computer:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)

        App = request.env['foco.app'].sudo()
        Site = request.env['foco.site'].sudo()
        Usage = request.env['foco.usage'].sudo()

        info = data.get('computer') or {}
        vals = {'last_seen': fields.Datetime.now()}
        Comp = request.env['foco.computer'].sudo()

        # Todas las anomalias de ESTE envio, en orden de CAUSA a consecuencia.
        # Escribirlas una por una hacia que la ultima pisara a las anteriores, y
        # el caso mas probable es el peor: borrar la base local hace que los
        # totales bajen, asi que el admin veia "totales que bajaron" -la
        # consecuencia- en lugar de "la base se borro", que explica todo lo demas.
        anomalias = []

        # --- INTEGRIDAD 4: la clave, usada desde otro equipo ----------------
        # La api_key vive en claro dentro de la base local del agente, en una
        # carpeta donde el usuario escribe. Quien la saque puede fabricar
        # envios desde cualquier maquina. Atarla al equipo con el que se
        # enrolo no impide el robo -nada en el lado del cliente puede- pero lo
        # vuelve VISIBLE, que hoy no pasaba.
        #
        # Se marca, no se rechaza: un equipo renombrado o un empleado que
        # cambia de sesion de Windows son casos legitimos, y perder su dato
        # seria peor que revisarlo.
        for campo, clave, etiqueta in (('name', 'name', 'el nombre del equipo'),
                                       ('windows_user', 'user', 'el usuario de Windows')):
            actual = computer[campo]
            llega = (info.get(clave) or '').strip()
            if actual and llega and llega != actual:
                anomalias.append("La api_key llego desde otro equipo: %s paso "
                                 "de '%s' a '%s'" % (etiqueta, actual, llega))
        if info.get('name') and not computer.name:
            vals['name'] = info['name']
        if info.get('user') and not computer.windows_user:
            vals['windows_user'] = info['user']

        # --- INTEGRIDAD 5: la base local, recreada -------------------------
        huella = (info.get('db_id') or '').strip()[:64]
        if huella:
            if computer.agent_db_id and huella != computer.agent_db_id:
                anomalias.append("La base local del agente se borro o se "
                                 "reemplazo (huella %s -> %s)"
                                 % (computer.agent_db_id, huella))
            if huella != computer.agent_db_id:
                vals['agent_db_id'] = huella
        if info.get('utc_offset_min') is not None:
            try:
                vals['utc_offset_min'] = int(info['utc_offset_min'])
            except (TypeError, ValueError):
                pass
        computer.sudo().write(vals)

        # --- INTEGRIDAD 1: reloj del equipo ---------------------------------
        # Si el agente dice una hora muy distinta a la del servidor, TODOS sus
        # timestamps (ausencias incluidas) dejan de ser confiables.
        if info.get('client_utc'):
            try:
                ct = datetime.fromisoformat(str(info['client_utc']))
                desfase = abs((datetime.utcnow() - ct).total_seconds())
                if desfase > 600:
                    anomalias.append("Reloj del equipo desfasado %d min "
                                     "respecto al servidor" % (desfase / 60))
            except (TypeError, ValueError):
                pass

        estados = dict(Usage._fields['host_status'].selection)
        fechas = set()
        stored = 0
        # Dentro de un mismo dia los contadores solo SUBEN: son totales
        # acumulados. Una bajada significa que alguien edito la base local o
        # restauro un respaldo. Se recoge aqui y se avisa UNA vez por envio,
        # no una por renglon, para que veinte apps no generen veinte alertas.
        bajadas = []
        ACUMULADOS = ('fg_active', 'fg_idle', 'background', 'call_hours',
                      'injected_hours', 'call_noinput_hours')
        for s in (data.get('samples') or []):
            app = App._get_or_create(s.get('exe'), s.get('name'), s.get('product'))
            if not app:
                continue
            date = s.get('date') or fields.Date.context_today(Usage)
            # El sitio va EN LA LLAVE: sin el, varias pestanas del mismo
            # navegador se escribirian sobre el mismo renglon y ganaria la
            # ultima, perdiendo el desglose.
            host = (s.get('host') or '')[:255]
            # Dentro o fuera de la jornada. Va EN LA LLAVE: si fuera una columna
            # mas, el mismo renglon se reescribiria con el ultimo valor y se
            # perderia justo la particion que se quiere poder reconocer.
            turno = 'off' if s.get('shift') == 'off' else 'in'
            # El ARCHIVO abierto, tambien en la llave y por la misma razon que
            # el sitio: si fuera una columna, todo Excel caeria en un renglon y
            # solo se sabria el ultimo archivo del dia. Solo llega con algo si
            # el admin habilito esa app; para las demas el agente ni lo extrae.
            documento = (s.get('document') or '')[:200]
            status = s.get('host_status')
            if status not in estados:
                status = 'not_browser'
            fga, fgi, bg = (_sec_to_h(s.get('fg_active')),
                            _sec_to_h(s.get('fg_idle')),
                            _sec_to_h(s.get('background')))
            # INTEGRIDAD 2: los subconjuntos no pueden exceder al total del que
            # forman parte. Se acotan aqui: un foco.db manipulado no va a meter
            # numeros imposibles a la base.
            call_h = min(_sec_to_h(s.get('call_secs')), fga)
            iny_h = min(_sec_to_h(s.get('injected_secs')), fga)
            cni_h = min(_sec_to_h(s.get('call_noinput_secs')), call_h)
            # El sitio se cataloga como entidad propia: es lo que permite
            # clasificarlo y que pese distinto que la app que lo muestra.
            site = Site._get_or_create(host) if host else Site.browse()
            usage = Usage.search([
                ('computer_id', '=', computer.id),
                ('app_id', '=', app.id),
                ('date', '=', date),
                ('host', '=', host),
                ('shift', '=', turno),
                ('document', '=', documento)], limit=1)
            campos = {'fg_active': fga, 'fg_idle': fgi, 'background': bg,
                      'host_status': status, 'call_hours': call_h,
                      'injected_hours': iny_h, 'call_noinput_hours': cni_h,
                      'site_id': site.id or False}
            if usage:
                # Se conserva el valor MAYOR. El agente manda totales absolutos
                # del dia, asi que el mas alto es el que de verdad se midio;
                # aceptar la bajada seria dejar que el equipo borre lo que ya
                # habia reportado.
                for campo in ACUMULADOS:
                    previo = usage[campo] or 0.0
                    if campos.get(campo, 0.0) + 0.0001 < previo:
                        bajadas.append((app.display_name, campo,
                                        previo, campos[campo]))
                        campos[campo] = previo
                usage.write(campos)
            else:
                campos.update({'computer_id': computer.id, 'app_id': app.id,
                               'date': date, 'host': host, 'shift': turno,
                               'document': documento})
                Usage.create(campos)
            fechas.add(date)
            stored += 1

        if bajadas:
            peor = max(bajadas, key=lambda b: b[2] - b[3])
            anomalias.append("Totales que BAJARON en %d renglon(es); se "
                             "conservo el mayor. El peor: %s %s de %.4f h a "
                             "%.4f h" % (len(bajadas), peor[0], peor[1],
                                         peor[2], peor[3]))

        # --- INTEGRIDAD 3: totales imposibles -------------------------------
        # Solo UNA app esta en primer plano a la vez, asi que la suma de
        # (fg_active + fg_idle) de un dia no puede exceder el tiempo
        # transcurrido. Si lo excede, el foco.db fue manipulado.
        anomalias += self._check_day_totals(computer, fechas)
        if anomalias:
            Comp.note_integrity(computer, " | ".join(anomalias))

        Absence = request.env['foco.absence'].sudo()
        gaps_stored = Absence.record_gaps(computer, data.get('gaps') or [])

        # --- que le paso al EQUIPO ------------------------------------------
        # Es lo que le pone causa a los huecos: sin esto, "apago y se fue" y
        # "el agente murio a media tarde" son el mismo renglon vacio.
        Event = request.env['foco.event'].sudo()
        events_stored = Event.record_events(computer, data.get('events') or [])

        # --- donde esta la persona AHORA ------------------------------------
        presencia = data.get('presence') or {}
        estados = dict(Comp._fields['presence_state'].selection)
        if presencia.get('state') in estados:
            # since_utc, no since: el agente manda los huecos en hora local y
            # los eventos en UTC, asi que el nombre tiene que decir cual es.
            desde = Event._parse_utc(presencia.get('since_utc'))
            computer.write({
                'presence_state': presencia['state'],
                'presence_since': desde or fields.Datetime.now(),
                'presence_idle_secs': int(presencia.get('idle_secs') or 0),
            })

        # --- la jornada del dia ---------------------------------------------
        # Se rehace aqui y no solo de noche para que la cinta se vea al momento:
        # es barato, una persona y los dias que toco este envio.
        if computer.employee_id:
            dias = {fields.Date.to_date(f) for f in fechas}
            for g in (data.get('gaps') or []):
                d = Event._parse_utc(g.get('start'))
                if d:
                    dias.add(d.date())
            if not dias:
                dias = {fields.Date.context_today(Usage)}
            request.env['foco.workday'].sudo().rebuild(
                computer.employee_id, sorted(d for d in dias if d))

        Command = request.env['foco.command'].sudo()
        cmds = Command.search([('computer_id', '=', computer.id),
                               ('state', '=', 'pending')])
        out = [{'id': c.id, 'type': c.command_type, 'payload': c.payload or ''}
               for c in cmds]
        if cmds:
            cmds.write({'state': 'sent', 'sent_at': fields.Datetime.now()})

        settings = request.env['foco.settings'].sudo().get_settings()
        config = settings.schedule_for(computer.employee_id)
        pendientes = Absence.pending_for_employee(computer.employee_id)
        return request.make_json_response({
            'ok': True,
            'stored': stored,
            'gaps_stored': gaps_stored,
            'events_stored': events_stored,
            'commands': out,
            'config': config,
            # Que apps pueden reportar el archivo abierto. Va en CADA respuesta
            # porque apagarlo tiene que surtir efecto igual de rapido que
            # encenderlo: si viajara solo al cambiar, revocar el permiso
            # dependeria de que el agente no se hubiera perdido ese envio.
            'doc_apps': App.doc_apps(),
            # Apps cuyo microfono NO cuenta como llamada (grabadores,
            # asistentes de voz). Tambien en cada respuesta: quitar la marca
            # tiene que surtir efecto igual de rapido que ponerla.
            'no_call_apps': App.no_call_apps(),
            # Capturas de pantalla. Viaja TODO en cada respuesta -incluido el
            # interruptor- para que apagarlo surta efecto en el siguiente envio
            # sin depender de que el equipo se reinicie.
            'screenshot': {
                'enabled': settings.screenshot_enabled,
                'minutos_sin_clasificar': settings.screenshot_unclassified_minutes,
                'nunca': App.no_screenshot_apps(),
                'clasificadas': App.clasificadas(),
                'ya_capturadas': request.env['foco.capture'].sudo()
                                 .apps_ya_capturadas(computer),
                # Monitoreo periodico de ESTA persona: que apps y cada cuanto.
                'monitor': request.env['foco.watch'].sudo()
                           .monitor_para(computer.employee_id),
            },
            'absences': pendientes.payload(),
        })

    @http.route('/foco/enroll', type='http', auth='public',
                methods=['POST'], csrf=False)
    def enroll(self, **kw):
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)
        inv = request.env['foco.invitation']._resolve(data.get('token'))
        if not inv:
            return request.make_json_response({'error': 'invalid_or_expired'}, status=403)

        info = data.get('computer') or {}
        Computer = request.env['foco.computer'].sudo()
        comp = inv.computer_id
        if not comp and info.get('name'):
            comp = Computer.search([('name', '=', info.get('name')),
                                    ('windows_user', '=', info.get('user'))], limit=1)
        vals = {'employee_id': inv.employee_id.id, 'last_seen': fields.Datetime.now()}
        if info.get('name'):
            vals['name'] = info['name']
        if info.get('user'):
            vals['windows_user'] = info['user']
        if comp:
            comp.write(vals)
        else:
            comp = Computer.create(vals)
        inv.sudo().write({'state': 'enrolled', 'computer_id': comp.id,
                          'enrolled_at': fields.Datetime.now()})
        return request.make_json_response(
            {'ok': True, 'api_key': comp.api_key, 'employee': inv.employee_id.name})

    # ================================================================ MOVIL
    #
    # Mismo patron que el lado Windows: enrolar por codigo -> api_key por
    # dispositivo -> envios con `X-Foco-Key`. La diferencia es lo que trae
    # (ubicacion, uso de apps, llamadas) y que el interruptor general vive en el
    # servidor: apagado, no se guarda NADA aunque el telefono lo mande.
    def _auth_mobile(self):
        key = request.httprequest.headers.get('X-Foco-Key')
        return request.env['foco.mobile.device']._authenticate(key)

    @http.route('/foco/mobile/enroll', type='http', auth='public',
                methods=['POST'], csrf=False)
    def mobile_enroll(self, **kw):
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)
        inv = request.env['foco.invitation']._resolve(data.get('token'))
        if not inv:
            return request.make_json_response({'error': 'invalid_or_expired'}, status=403)
        info = data.get('device') or {}
        Dev = request.env['foco.mobile.device'].sudo()
        dev = inv.mobile_id
        if not dev and info.get('android_id'):
            dev = Dev.search([('android_id', '=', info['android_id'])], limit=1)
        vals = {'employee_id': inv.employee_id.id, 'last_seen': fields.Datetime.now(),
                'removed': False, 'removed_at': False, 'removed_reason': False}
        for k_in, k_field in (('name', 'name'), ('android_id', 'android_id'),
                              ('phone', 'phone_number'), ('os', 'os_version'),
                              ('app_version', 'app_version')):
            if info.get(k_in):
                vals[k_field] = info[k_in]
        if dev:
            dev.write(vals)
        else:
            vals.setdefault('name', info.get('model') or 'Android')
            dev = Dev.create(vals)
        inv.sudo().write({'state': 'enrolled', 'mobile_id': dev.id,
                          'enrolled_at': fields.Datetime.now()})
        s = request.env['foco.settings'].sudo().get_settings()
        return request.make_json_response({
            'ok': True, 'api_key': dev.api_key,
            'employee': inv.employee_id.name, 'config': s.mobile_config()})

    @http.route('/foco/mobile/beacon', type='http', auth='public',
                methods=['POST'], csrf=False)
    def mobile_beacon(self, **kw):
        """Ultimo aliento del telefono: avisa que lo estan retirando (al
        desactivar el administrador para poder desinstalarlo). Marca el equipo
        como RETIRADO al instante; el proximo reporte -si volviera- lo limpia."""
        dev = self._auth_mobile()
        if not dev:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        data = self._body() or {}
        reason = (data.get('reason') or 'removed')[:120]
        dev.sudo().write({
            'removed': True,
            'removed_at': fields.Datetime.now(),
            'removed_reason': reason,
        })
        try:
            dev.sudo().message_post(
                body="El equipo avisó que lo están retirando (%s)." % reason)
        except Exception:
            pass
        return request.make_json_response({'ok': True})

    def _mobile_screenshot_block(self, dev, settings):
        """Lo que el telefono necesita para las capturas: interruptor, minutos,
        paquetes que NUNCA se capturan, los YA capturados (para no repetir el
        disparador de descubrimiento) y las solicitudes on-demand pendientes.
        Marca las pendientes como 'enviadas' aqui: viajan una vez."""
        Cmd = request.env['foco.mobile.command'].sudo()
        Cap = request.env['foco.mobile.capture'].sudo()
        enabled = bool(settings.mobile_enabled and settings.mobile_screenshot_enabled)
        block = {
            'enabled': enabled,
            'minutos': settings.screenshot_unclassified_minutes or 0,
            'nunca': ['com.simdatagroup.foco'],
            'capturadas': Cap.paquetes_capturados(dev) if enabled else [],
            'solicitar': [],
        }
        if enabled:
            pend = Cmd.search([('device_id', '=', dev.id),
                               ('kind', '=', 'screenshot'),
                               ('state', '=', 'pending')])
            block['solicitar'] = [{'id': c.id, 'reason': c.payload or ''} for c in pend]
            if pend:
                pend.write({'state': 'sent', 'sent_at': fields.Datetime.now()})
        return block

    @http.route('/foco/mobile/capture', type='http', auth='public',
                methods=['POST'], csrf=False)
    def mobile_capture(self, **kw):
        """Recibe UN pantallazo del telefono (imagen en base64 + metadatos). El
        interruptor se re-verifica dentro de `registrar`: apagado no guarda."""
        dev = self._auth_mobile()
        if not dev:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)
        cid = request.env['foco.mobile.capture'].sudo().registrar(dev, data)
        return request.make_json_response({'ok': True, 'id': cid})

    @http.route('/foco/mobile/ingest', type='http', auth='public',
                methods=['POST'], csrf=False)
    def mobile_ingest(self, **kw):
        dev = self._auth_mobile()
        if not dev:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)
        s = request.env['foco.settings'].sudo().get_settings()

        # Metadatos del equipo: se guardan aunque el monitoreo este apagado
        # -saber que un telefono sigue vivo y con cuanta bateria no es vigilancia-.
        meta = data.get('device') or {}
        # Reporto: sigue vivo. Si estaba marcado retirado, se limpia (volvio).
        dvals = {'last_seen': fields.Datetime.now(),
                 'removed': False, 'removed_at': False, 'removed_reason': False}
        if meta.get('battery') not in (None, ''):
            dvals['battery'] = _int(meta.get('battery'))
        for k_in, k_field in (('os', 'os_version'), ('app_version', 'app_version'),
                              ('phone', 'phone_number')):
            if meta.get(k_in):
                dvals[k_field] = meta[k_in]

        # El interruptor general MANDA del lado del servidor.
        if not s.mobile_enabled:
            dev.sudo().write(dvals)
            return request.make_json_response(
                {'ok': True, 'stored': False, 'config': s.mobile_config(),
                 'screenshot': self._mobile_screenshot_block(dev, s)})

        Ev = request.env['foco.event']
        Loc = request.env['foco.location'].sudo()
        Usage = request.env['foco.mobile.usage'].sudo()
        Call = request.env['foco.call'].sudo()
        n_loc = n_usg = n_call = 0

        # --- ubicaciones ---
        # Se DEDUPLICAN por (dispositivo, momento): en una red movil un lote que
        # se subio pero cuya respuesta se perdio se reintenta, y sin esto cada
        # reintento duplicaria el recorrido. Dos puntos con el mismo segundo son,
        # para el intervalo que se maneja, el mismo punto.
        locs = data.get('locations') or []
        ats = [a for a in (Ev._parse_utc(l.get('at')) for l in locs) if a]
        vistos = set()
        if ats:
            for r in Loc.search([('device_id', '=', dev.id), ('at', 'in', ats)]):
                vistos.add(fields.Datetime.to_string(r.at))
        ult = None
        for l in locs:
            at = Ev._parse_utc(l.get('at'))
            if not at:
                continue
            clave = fields.Datetime.to_string(at)
            if clave in vistos:
                continue
            vistos.add(clave)
            Loc.create({
                'device_id': dev.id, 'at': at,
                'lat': l.get('lat') or 0.0, 'lon': l.get('lon') or 0.0,
                'accuracy': l.get('accuracy') or 0.0,
                'speed': l.get('speed') or 0.0,
                'battery': _int(l.get('battery')),
                'source': l.get('source') or '',
            })
            n_loc += 1
            if not ult or at > ult['at']:
                ult = {'at': at, 'lat': l.get('lat') or 0.0, 'lon': l.get('lon') or 0.0}
        if ult:
            dvals.update({'last_lat': ult['lat'], 'last_lon': ult['lon'],
                          'last_fix_at': ult['at']})

        # --- uso de apps (upsert idempotente por dia + paquete) ---
        for u in (data.get('usage') or []):
            pkg = (u.get('package') or '').strip()
            d = fields.Date.to_date(u.get('date'))
            if not pkg or not d:
                continue
            vals = {'foreground_seconds': _int(u.get('seconds'))}
            if u.get('label'):
                vals['app_label'] = u['label']
            row = Usage.search([('device_id', '=', dev.id), ('date', '=', d),
                                ('package', '=', pkg)], limit=1)
            if row:
                row.write(vals)
            else:
                vals.update({'device_id': dev.id, 'date': d, 'package': pkg})
                Usage.create(vals)
            n_usg += 1

        # --- llamadas (dedup por el _ID del propio Android) ---
        _DIR = {'in': 'in', 'incoming': 'in', 'out': 'out', 'outgoing': 'out',
                'missed': 'missed', 'rejected': 'rejected', 'blocked': 'blocked'}
        for c in (data.get('calls') or []):
            at = Ev._parse_utc(c.get('at'))
            if not at:
                continue
            ext = str(c.get('id') or '').strip()
            if ext and Call.search_count([('device_id', '=', dev.id),
                                          ('external_id', '=', ext)]):
                continue
            Call.create({
                'device_id': dev.id, 'at': at,
                'number': c.get('number') or '',
                'contact': c.get('contact') or '',
                'direction': _DIR.get((c.get('direction') or '').lower(), 'other'),
                'duration_seconds': _int(c.get('duration')),
                'external_id': ext or False,
            })
            n_call += 1

        dev.sudo().write(dvals)
        return request.make_json_response({
            'ok': True, 'stored': True,
            'counts': {'locations': n_loc, 'usage': n_usg, 'calls': n_call},
            'config': s.mobile_config(),
            'screenshot': self._mobile_screenshot_block(dev, s)})

    @http.route('/foco/download', type='http', auth='public', methods=['GET'])
    def download(self, **kw):
        s = request.env['foco.settings'].sudo().get_settings()
        if not s.installer:
            return request.not_found()
        content = base64.b64decode(s.installer)
        fn = s.installer_name or 'FERBA-Foco-Setup.exe'
        return request.make_response(content, headers=[
            ('Content-Type', 'application/octet-stream'),
            ('Content-Disposition', 'attachment; filename="%s"' % fn)])

    @http.route('/foco/absence_answer', type='http', auth='public',
                methods=['POST'], csrf=False)
    def absence_answer(self, **kw):
        """Respuesta desde la VENTANA DEL AGENTE."""
        computer = self._auth()
        if not computer:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)
        try:
            absence_id = int(data.get('absence_id') or 0)
        except (TypeError, ValueError):
            absence_id = 0
        rec = request.env['foco.absence'].sudo().search([
            ('id', '=', absence_id),
            ('employee_id', '=', computer.employee_id.id)], limit=1)
        if not rec:
            return request.make_json_response({'error': 'not_found'}, status=404)
        return request.make_json_response(
            rec.answer(data.get('reason'), data.get('note'), via='agente'))

    @http.route('/foco/justificar/<string:token>', type='http', auth='public',
                methods=['GET', 'POST'], csrf=False, website=False)
    def justify(self, token, **post):
        """Pagina publica: el empleado justifica sin usuario de Odoo."""
        employee = request.env['hr.employee']._foco_by_token(token)
        if not employee:
            return request.render('foco_monitor.justify_invalid', {})

        Absence = request.env['foco.absence'].sudo()
        if request.httprequest.method == 'POST':
            guardadas = 0
            for key, reason in post.items():
                if not key.startswith('reason_') or not reason:
                    continue
                try:
                    rid = int(key[len('reason_'):])
                except ValueError:
                    continue
                rec = Absence.search([('id', '=', rid),
                                      ('employee_id', '=', employee.id)], limit=1)
                if not rec:
                    continue
                if rec.answer(reason, post.get('note_%s' % rid), via='web').get('ok'):
                    guardadas += 1
            # POST-Redirect-GET. Sin esto, recargar REENVIA el formulario y el
            # navegador pregunta "reenviar?". Paso de verdad durante las pruebas.
            return request.redirect('/foco/justificar/%s?guardados=%d'
                                    % (token, guardadas))

        try:
            saved = int(post.get('guardados') or 0)
        except (TypeError, ValueError):
            saved = 0
        pendientes = Absence.pending_for_employee(employee)
        return request.render('foco_monitor.justify_page', {
            'employee': employee,
            'items': pendientes.payload(),
            'reasons': request.env['foco.absence']._fields['reason'].selection,
            'saved': saved,
        })

    @http.route('/foco/command_result', type='http', auth='public',
                methods=['POST'], csrf=False)
    def command_result(self, **kw):
        computer = self._auth()
        if not computer:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)
        Command = request.env['foco.command'].sudo()
        cmd = Command.search([
            ('id', '=', int(data.get('command_id') or 0)),
            ('computer_id', '=', computer.id)], limit=1)
        if not cmd:
            return request.make_json_response({'error': 'not_found'}, status=404)
        cmd.write({
            'state': 'error' if data.get('state') == 'error' else 'done',
            'result': data.get('result') or '',
            'done_at': fields.Datetime.now()})
        # Avisar a quien la pidio, con la campanita de Odoo. Se hace DESPUES del
        # write -sobre el estado ya guardado- y sin dejar que un fallo del aviso
        # tumbe la confirmacion: el agente necesita su 200 para dar la orden por
        # cerrada, aunque la notificacion no saliera.
        try:
            cmd.notificar_resultado()
        except Exception:
            _logger.exception('Foco: no se pudo notificar el resultado de la orden %s', cmd.id)
        return request.make_json_response({'ok': True})

    # --------------------------------------------------------------- ordenes
    @http.route('/foco/commands', type='http', auth='public',
                methods=['POST'], csrf=False)
    def commands(self, **kw):
        """«Hay ordenes para mi?» y nada mas.

        POR QUE UN ENDPOINT APARTE
            El envio de datos (`/foco/ingest`) es pesado y corre cada 5
            minutos. Colgar de el las ordenes de control remoto significaba que
            un boton pulsado en Odoo tardaba hasta cinco minutos en llegar, y
            ese retraso hacia inutil justamente lo que tiene que ser inmediato:
            bloquear un equipo, o pedir una captura de lo que esta pasando
            AHORA -que en cinco minutos ya no esta pasando-.

            Este responde unos cientos de bytes y lo llama el agente cada ~25 s.
            Con treinta equipos son 1.2 peticiones por segundo; el envio pesado
            sigue siendo cada 5 minutos.

        POR QUE NO UN WEBSOCKET
            Odoo 19 trae bus y seria empuje de verdad, pero mete una conexion
            persistente y una maquina de reconexion a un agente que hoy no
            guarda estado entre envios. Su modo de falla es peor que el de un
            sondeo: se desconecta en silencio y nadie se entera hasta que una
            orden no llega. Queda como camino de mejora si 25 s no alcanzan.
        """
        computer = self._auth()
        if not computer:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        Command = request.env['foco.command'].sudo()
        cmds = Command.search([('computer_id', '=', computer.id),
                               ('state', '=', 'pending')])
        salida = [{'id': c.id, 'type': c.command_type, 'payload': c.payload or ''}
                  for c in cmds]
        if cmds:
            cmds.write({'state': 'sent', 'sent_at': fields.Datetime.now()})
        # El latido va aqui tambien: si el agente pregunta cada 25 s, el equipo
        # deja de verse «sin senal» por esperar al envio pesado.
        computer.write({'last_seen': fields.Datetime.now()})

        # LO QUE HACE FALTA PARA OBEDECER VIAJA CON LA ORDEN.
        #
        # Esto nace de un defecto medido: la orden de captura llegaba por aqui
        # en 19 segundos y el agente la rechazaba con «las capturas estan
        # apagadas en Odoo» cuando estaban encendidas. La razon es que el
        # interruptor solo viajaba en el envio pesado de 5 minutos, asi que un
        # agente recien arrancado obedecia una configuracion que nunca habia
        # recibido.
        #
        # Se manda lo que GOBIERNA o PROTEGE, no todo: el interruptor, el umbral
        # y la lista de apps que nunca se fotografian -que es una garantia y no
        # puede ir con retraso-. Las listas grandes (las clasificadas, las ya
        # capturadas) se quedan en el envio de 5 minutos: solo alimentan el
        # disparador automatico, que por definicion necesita media hora de
        # permanencia, asi que un retraso de cinco minutos ahi no cambia nada.
        ajustes = request.env['foco.settings'].sudo().get_settings()
        return request.make_json_response({
            'ok': True,
            'commands': salida,
            'screenshot': {
                'enabled': ajustes.screenshot_enabled,
                'minutos_sin_clasificar': ajustes.screenshot_unclassified_minutes,
                'nunca': request.env['foco.app'].sudo().no_screenshot_apps(),
                # El monitoreo periodico tambien viaja en el sondeo ligero: su
                # intervalo puede ser corto (5-15 min), asi que un agente recien
                # arrancado no debe esperar al envio pesado para empezar.
                'monitor': request.env['foco.watch'].sudo()
                           .monitor_para(computer.employee_id),
            },
        })

    # ------------------------------------------------------------- capturas
    @http.route('/foco/capture', type='http', auth='public',
                methods=['POST'], csrf=False)
    def capture(self, **kw):
        """Recibe una captura de pantalla del equipo.

        La comprobacion del interruptor general vive en el modelo, no aqui: un
        agente viejo -o alguien llamando a mano- no puede saltarsela. «Apagado
        significa que no se guarda nada» tiene que sostenerlo el servidor, que
        es la unica pieza que no se puede reemplazar por una version parcheada.
        """
        computer = self._auth()
        if not computer:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)
        cid = request.env['foco.capture'].sudo().registrar(computer, data)
        if not cid:
            return request.make_json_response({'ok': False, 'stored': 0,
                                               'reason': 'apagado'})
        return request.make_json_response({'ok': True, 'stored': 1, 'id': cid})

    # ------------------------------------------------------------- reportes
    @http.route('/foco/reporte.csv', type='http', auth='user', methods=['GET'])
    def reporte_csv(self, desde=None, hasta=None, empleado=None, grano='dia', **kw):
        """El detalle medido, para analizarlo fuera.

        Va por una ruta de descarga y no por un boton que arme el archivo en el
        navegador por dos razones que se notan al usarlo: el archivo sale con
        NOMBRE propio -«foco-Desarrollo-2026-09-14_2026-09-20.csv»-, y el
        periodo no lo limita la memoria del navegador.

        `grano` agrupa por dia, semana o mes. Un mes de detalle diario por
        aplicacion son miles de renglones; quien quiere la tendencia no los
        quiere, y quien quiere auditar un dia si.

        Permisos: `auth='user'` deja pasar a cualquier usuario con sesion, asi
        que el grupo se comprueba aqui. Esto expone actividad de PERSONAS: que
        el filtro dependiera solo de que nadie adivine la URL no seria un
        permiso.
        """
        env = request.env
        if not env.user.has_group('foco_monitor.group_foco_user'):
            return request.not_found()

        hoy = fields.Date.context_today(env['foco.usage'])
        d1 = fields.Date.to_date(desde) or hoy
        d2 = fields.Date.to_date(hasta) or hoy
        if d2 < d1:
            d1, d2 = d2, d1

        dominio = [('date', '>=', d1), ('date', '<=', d2)]
        emp_ids = [int(x) for x in (empleado or '').split(',') if x.strip().isdigit()]
        if emp_ids:
            dominio.append(('employee_id', 'in', emp_ids))
        # Las reglas de registro del modelo siguen aplicando: quien solo ve su
        # departamento exporta su departamento, no la empresa.
        filas = env['foco.usage'].search(dominio, order='date, employee_id')

        def cubeta(fecha):
            if grano == 'mes':
                return fecha.strftime('%Y-%m')
            if grano == 'semana':
                ini = fecha - timedelta(days=fecha.weekday())
                return '%s (sem)' % ini.isoformat()
            return fecha.isoformat()

        # Se agrega por periodo + persona + app + sitio/archivo. Mas fino que
        # esto seria volcar la tabla; mas grueso perderia el "en que".
        acumulado = {}
        for f in filas:
            llave = (cubeta(f.date), f.employee_id.id, f.app_id.id,
                     f.document or f.host or '')
            a = acumulado.setdefault(llave, {
                'periodo': cubeta(f.date),
                'empleado': f.employee_id.display_name or '',
                'departamento': f.department_id.display_name or '',
                'aplicacion': f.app_id.display_name or '',
                'ejecutable': f.app_id.exe or '',
                'sitio_o_archivo': f.document or f.host or '',
                'categoria': f.category_id.name or 'Sin clasificar',
                'activo_h': 0.0, 'sin_input_h': 0.0, 'segundo_plano_h': 0.0,
                'llamada_h': 0.0, 'productivas_h': 0.0,
            })
            a['activo_h'] += f.fg_active
            a['sin_input_h'] += f.fg_idle
            a['segundo_plano_h'] += f.background
            a['llamada_h'] += f.call_hours
            a['productivas_h'] += f.productive_hours

        cols = ['periodo', 'empleado', 'departamento', 'aplicacion', 'ejecutable',
                'sitio_o_archivo', 'categoria', 'activo_h', 'sin_input_h',
                'segundo_plano_h', 'llamada_h', 'productivas_h']
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction='ignore',
                           lineterminator='\n')
        w.writeheader()
        for a in sorted(acumulado.values(),
                        key=lambda x: (x['periodo'], x['empleado'], -x['activo_h'])):
            for k in ('activo_h', 'sin_input_h', 'segundo_plano_h',
                      'llamada_h', 'productivas_h'):
                a[k] = round(a[k], 4)
            w.writerow(a)

        quien = 'equipo'
        if len(emp_ids) == 1:
            quien = (env['hr.employee'].browse(emp_ids[0]).name or 'empleado')
        # Sin espacios ni acentos en el nombre: viaja por una cabecera HTTP y
        # acaba en el explorador de archivos de alguien mas.
        quien = ''.join(c if c.isalnum() else '-' for c in quien).strip('-')[:40]
        nombre = 'foco-%s-%s_%s.csv' % (quien, d1.isoformat(), d2.isoformat())

        # BOM: sin el, Excel en Windows abre el UTF-8 con los acentos rotos y
        # el usuario concluye que el sistema exporta basura.
        datos = ('﻿' + buf.getvalue()).encode('utf-8')
        return request.make_response(datos, headers=[
            ('Content-Type', 'text/csv; charset=utf-8'),
            ('Content-Length', len(datos)),
            ('Content-Disposition', content_disposition(nombre)),
        ])

    @http.route('/foco/policy', type='http', auth='public',
                methods=['POST'], csrf=False)
    def policy(self, **kw):
        """Que sitios puede abrir este equipo, y que dice el equipo tener puesto.

        Endpoint APARTE de /foco/ingest a proposito. Quien lo llama no es el
        agente sino el servicio del equipo, que corre como SYSTEM: es el unico
        que puede escribir la politica del navegador. Si la lista viajara dentro
        de la respuesta del agente habria que dejarla en un archivo intermedio
        que el propio usuario vigilado puede editar, y el bloqueo se desactivaria
        solo. Aqui el componente privilegiado habla directo con el servidor.

        La misma llamada REPORTA lo aplicado y RECOGE lo vigente. Que las dos
        cosas vayan juntas es lo que permite distinguir "lo bloquee en Odoo" de
        "esta bloqueado en la maquina", que sin esto se ven igual.
        """
        computer = self._auth()
        if not computer:
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        data = self._body()
        if data is None:
            return request.make_json_response({'error': 'bad_json'}, status=400)

        aplicada = (data.get('applied') or '').strip()[:64]
        if aplicada:
            detalle = (data.get('detail') or '').strip()[:250]
            # La notificacion se decide ANTES del write: compara la version que
            # llega contra la que el equipo tenia, y eso solo se puede leer
            # mientras `policy_version` aun es la anterior. El aviso no puede
            # tumbar el reporte del equipo, asi que va en su propio try.
            try:
                computer.sudo().notificar_politica(aplicada, detalle)
            except Exception:
                _logger.exception('Foco: no se pudo notificar la politica de %s',
                                  computer.id)
            vals = {'policy_version': aplicada}
            # `policy_applied_at` SOLO cuando la version cambia: el servicio ahora
            # late en cada ciclo, y sin esto "aplicada" se moveria cada pocos
            # minutos aunque no hubiera cambiado nada.
            if aplicada != (computer.policy_version or ''):
                vals['policy_applied_at'] = fields.Datetime.now()
            # El detalle solo se pisa si viene: el servicio reporta en cada
            # ciclo, y un reporte de rutina sin detalle borraria el mensaje del
            # error que hay que leer.
            if detalle:
                vals['policy_detail'] = detalle
            computer.sudo().write(vals)

        # Latido de verificacion: el equipo confirma (o no) que lo PUESTO en su
        # registro coincide con lo vigente. Es lo que permite ALERTAR cuando un
        # equipo deja de confirmarlo -watchdog caido, tarea desactivada, red
        # cortada- en vez de suponer que sigue bloqueado.
        verificado = data.get('verified')
        if verificado is not None:
            if verificado:
                computer.sudo().write({'policy_verified_at': fields.Datetime.now(),
                                       'policy_drift': False})
            else:
                computer.sudo().write({'policy_drift': True})

        return request.make_json_response(
            request.env['foco.policy'].sudo().payload_for(computer.sudo()))
