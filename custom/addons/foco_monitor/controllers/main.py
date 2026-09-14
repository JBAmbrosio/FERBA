import base64
import json
import logging
from datetime import datetime, time as _time, timedelta

import pytz

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _sec_to_h(v):
    try:
        return float(v) / 3600.0
    except (TypeError, ValueError):
        return 0.0


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
        """Un dia no puede tener mas primer plano que tiempo transcurrido."""
        if not fechas:
            return
        Usage = request.env['foco.usage'].sudo()
        Settings = request.env['foco.settings'].sudo()
        tz = pytz.timezone(Settings._tz_for(computer.employee_id))
        ahora = pytz.UTC.localize(datetime.utcnow()).astimezone(tz)
        for fecha in fechas:
            d = fields.Date.to_date(fecha)
            if not d:
                continue
            if d > ahora.date():
                request.env['foco.computer'].sudo().note_integrity(
                    computer, "Datos fechados en el FUTURO (%s)" % d)
                continue
            # Para hoy, el tope es lo transcurrido desde medianoche.
            tope = (ahora - tz.localize(datetime.combine(d, _time(0, 0)))
                    ).total_seconds() / 3600.0 if d == ahora.date() else 24.0
            filas = Usage.search([('computer_id', '=', computer.id),
                                  ('date', '=', d)])
            total = sum((r.fg_active or 0) + (r.fg_idle or 0) for r in filas)
            # 10% de holgura: relojes, DST y redondeos no son manipulacion.
            if total > tope * 1.10 + 0.25:
                request.env['foco.computer'].sudo().note_integrity(
                    computer, "Totales imposibles el %s: %.1f h de primer plano "
                              "contra %.1f h transcurridas" % (d, total, tope))

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
        if info.get('name') and not computer.name:
            vals['name'] = info['name']
        if info.get('user') and not computer.windows_user:
            vals['windows_user'] = info['user']
        computer.sudo().write(vals)

        Comp = request.env['foco.computer'].sudo()
        # --- INTEGRIDAD 1: reloj del equipo ---------------------------------
        # Si el agente dice una hora muy distinta a la del servidor, TODOS sus
        # timestamps (ausencias incluidas) dejan de ser confiables.
        if info.get('client_utc'):
            try:
                ct = datetime.fromisoformat(str(info['client_utc']))
                desfase = abs((datetime.utcnow() - ct).total_seconds())
                if desfase > 600:
                    Comp.note_integrity(
                        computer, "Reloj del equipo desfasado %d min respecto "
                                  "al servidor" % (desfase / 60))
            except (TypeError, ValueError):
                pass

        estados = dict(Usage._fields['host_status'].selection)
        fechas = set()
        stored = 0
        for s in (data.get('samples') or []):
            app = App._get_or_create(s.get('exe'), s.get('name'))
            if not app:
                continue
            date = s.get('date') or fields.Date.context_today(Usage)
            # El sitio va EN LA LLAVE: sin el, varias pestanas del mismo
            # navegador se escribirian sobre el mismo renglon y ganaria la
            # ultima, perdiendo el desglose.
            host = (s.get('host') or '')[:255]
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
                ('host', '=', host)], limit=1)
            campos = {'fg_active': fga, 'fg_idle': fgi, 'background': bg,
                      'host_status': status, 'call_hours': call_h,
                      'injected_hours': iny_h, 'call_noinput_hours': cni_h,
                      'site_id': site.id or False}
            if usage:
                usage.write(campos)
            else:
                campos.update({'computer_id': computer.id, 'app_id': app.id,
                               'date': date, 'host': host})
                Usage.create(campos)
            fechas.add(date)
            stored += 1

        # --- INTEGRIDAD 3: totales imposibles -------------------------------
        # Solo UNA app esta en primer plano a la vez, asi que la suma de
        # (fg_active + fg_idle) de un dia no puede exceder el tiempo
        # transcurrido. Si lo excede, el foco.db fue manipulado.
        self._check_day_totals(computer, fechas)

        Absence = request.env['foco.absence'].sudo()
        gaps_stored = Absence.record_gaps(computer, data.get('gaps') or [])

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
            'commands': out,
            'config': config,
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
        return request.make_json_response({'ok': True})
