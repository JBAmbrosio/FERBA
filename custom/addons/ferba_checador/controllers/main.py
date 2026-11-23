# -*- coding: utf-8 -*-
"""API de la app del checador.

Todas las rutas (menos el alta) exigen la credencial de la tableta en el
encabezado X-Checador-Token. Sin ella, o con una revocada, responden 401 sin
decir nada mas. No hay direccion publica que abra el checador.
"""
import base64
import binascii
import json
import logging

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

MAX_FOTO_B64 = 600_000   # ~450 KB de JPEG


def _json(datos, status=200):
    return request.make_json_response(datos, status=status)


def _cuerpo():
    try:
        return json.loads(request.httprequest.get_data(as_text=True) or '{}')
    except ValueError:
        return {}


class FerbaChecadorAPI(http.Controller):

    def _checador(self):
        token = request.httprequest.headers.get('X-Checador-Token', '')
        return request.env['ferba.checador'].sudo()._por_token(token)

    def _info(self, datos):
        info = dict(datos.get('equipo') or {})
        info['ip'] = request.httprequest.remote_addr
        return info

    # ------------------------------------------------------------------
    @http.route('/ferba/checador/v1/alta', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def alta(self, **kw):
        datos = _cuerpo()
        checador, token = request.env['ferba.checador'].sudo()._dar_de_alta(
            datos.get('codigo'), self._info(datos))
        if not checador:
            _logger.warning('Checador: alta rechazada desde %s', request.httprequest.remote_addr)
            return _json({'ok': False, 'mensaje': 'Codigo incorrecto o vencido.'}, 403)
        return _json({'ok': True, 'token': token, 'checador': checador.name,
                      'empresa': checador.company_id.name})

    # ------------------------------------------------------------------
    @http.route('/ferba/checador/v1/sync', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def sync(self, **kw):
        checador = self._checador()
        if not checador:
            return _json({'ok': False}, 401)
        checador._registrar_senal(self._info(_cuerpo()))
        empleados = request.env['hr.employee'].sudo().search(
            [('company_id', '=', checador.company_id.id)], order='name, id')
        ahora = fields.Datetime.now()
        version = request.env['ferba.checador.app']._para(checador)
        return _json({
            'ok': True,
            # La app compara este numero con el suyo; si es mayor, descarga e instala.
            'app': {
                'version': version.name, 'version_code': version.version_code,
                'sha256': version.sha256, 'tamano': version.tamano,
            } if version else None,
            'servidor_utc': fields.Datetime.to_string(ahora),
            'config': {
                'checador': checador.name,
                'empresa': checador.company_id.name,
                'exige_foto': checador.exige_foto,
                'segundos_regreso': checador.segundos_regreso,
                'pin_mantenimiento': checador.pin_mantenimiento or '',
                'offline': checador.permite_offline,
                'max_fallos': checador.max_fallos,
                'minutos_bloqueo': checador.minutos_bloqueo,
                'segundos_doble_marca': checador.segundos_doble_marca,
            },
            'empleados': [{
                'id': e.id,
                'nombre': e.name,
                'puesto': e.job_id.name or '',
                'depto': e.department_id.name or '',
                'dentro': e.attendance_state == 'checked_in',
                'tiene_nip': bool(e.pin),
                'bloqueado': bool(e.checador_bloqueado_hasta and e.checador_bloqueado_hasta > ahora),
                'foto_v': int(e.write_date.timestamp()) if e.write_date else 0,
                # Huella del NIP para validarlo sin red. Nunca viaja el NIP.
                'nip_v': (e.checador_nip_huella or '') if checador.permite_offline else '',
            } for e in empleados],
        })

    # ------------------------------------------------------------------
    @http.route('/ferba/checador/v1/foto/<int:employee_id>', type='http', auth='public',
                methods=['GET'], csrf=False, save_session=False)
    def foto(self, employee_id, **kw):
        checador = self._checador()
        if not checador:
            return request.make_response('', status=401)
        emp = request.env['hr.employee'].sudo().browse(employee_id).exists()
        if not emp or emp.company_id != checador.company_id:
            return request.not_found()
        datos = emp.avatar_256
        if not datos:
            return request.not_found()
        crudo = base64.b64decode(datos)
        tipo = 'image/svg+xml' if crudo.lstrip()[:1] == b'<' else 'image/png'
        if crudo[:3] == b'\xff\xd8\xff':
            tipo = 'image/jpeg'
        return request.make_response(crudo, headers=[
            ('Content-Type', tipo), ('Cache-Control', 'private, max-age=86400')])

    # ------------------------------------------------------------------
    @http.route('/ferba/checador/v1/app/<int:version_code>', type='http', auth='public',
                methods=['GET'], csrf=False, save_session=False)
    def app(self, version_code, **kw):
        checador = self._checador()
        if not checador:
            return request.make_response('', status=401)
        version = request.env['ferba.checador.app']._para(checador)
        # Solo se entrega la version que le toca a ESA tableta.
        if not version or version.version_code != version_code:
            return request.not_found()
        crudo = base64.b64decode(version.with_context(bin_size=False).apk)
        return request.make_response(crudo, headers=[
            ('Content-Type', 'application/vnd.android.package-archive'),
            ('Content-Length', str(len(crudo))), ('Cache-Control', 'no-store')])

    # ------------------------------------------------------------------
    @http.route('/ferba/checador/v1/marcar', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def marcar(self, **kw):
        checador = self._checador()
        if not checador:
            return _json({'ok': False}, 401)
        datos = _cuerpo()
        foto = datos.get('foto') or None
        if foto:
            if len(foto) > MAX_FOTO_B64:
                foto = None
            else:
                try:
                    base64.b64decode(foto, validate=True)
                except (binascii.Error, ValueError):
                    foto = None
        try:
            emp_id = int(datos.get('empleado') or 0)
        except (TypeError, ValueError):
            emp_id = 0
        offline = None
        if datos.get('offline'):
            offline = {'hace_ms': datos.get('hace_ms'), 'fecha': datos.get('fecha_dispositivo')}
        r = request.env['ferba.checador.evento']._marcar(
            checador, emp_id, datos.get('nip'), datos.get('uuid'), foto, offline=offline)
        r['servidor_utc'] = fields.Datetime.to_string(fields.Datetime.now())
        return _json(r)
