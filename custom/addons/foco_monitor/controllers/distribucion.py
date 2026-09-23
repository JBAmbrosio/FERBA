import base64

from odoo import http
from odoo.http import request


class FocoDistribucion(http.Controller):
    """Reparto del APK por QR con enlace efimero. /foco/instalar muestra una
    pagina donde, al completar el codigo de teclas, aparece un QR; ese QR lleva
    un token de un solo uso y corta vida a /foco/app/apk. La Play Store no admite
    apps de monitoreo, asi que esta es la via de sideload."""

    @http.route('/foco/instalar', type='http', auth='public', website=False)
    def instalar(self, **kw):
        s = request.env['foco.settings'].sudo().get_settings()
        return request.render('foco_monitor.instalar_page', {
            'hay_app': s.mobile_apk_ready(),
            'version': s.mobile_apk_version_name or '',
        })

    @http.route('/foco/app/token', type='http', auth='public',
                methods=['POST'], csrf=False)
    def app_token(self, **kw):
        """Acuña un token efimero y devuelve el QR (PNG en data URL) que apunta a
        la descarga. Lo llama el codigo de teclas de /foco/instalar."""
        s = request.env['foco.settings'].sudo().get_settings()
        if not s.mobile_apk_ready():
            return request.make_json_response({'ok': False})
        tok = request.env['foco.app.token'].sudo().emitir(s.mobile_apk_token_minutes)
        base = request.httprequest.host_url.rstrip('/')
        url = '%s/foco/app/apk?t=%s' % (base, tok.token)
        return request.make_json_response({
            'ok': True,
            'qr': 'data:image/png;base64,%s' % self._qr_png(url),
            'version': s.mobile_apk_version_name or '',
        })

    @http.route('/foco/app/apk', type='http', auth='public', methods=['GET'])
    def app_apk(self, t=None, **kw):
        """Sirve el APK publicado si el token es valido. Cuenta la descarga."""
        tok = request.env['foco.app.token'].sudo().consumir(t)
        if not tok:
            return request.render('foco_monitor.instalar_caducado', {})
        s = request.env['foco.settings'].sudo().get_settings()
        if not s.mobile_apk:
            return request.not_found()
        content = base64.b64decode(s.mobile_apk)
        fn = s.mobile_apk_name or 'foco.apk'
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.android.package-archive'),
            ('Content-Disposition', 'attachment; filename="%s"' % fn),
        ])

    def _qr_png(self, value):
        """QR como PNG en base64, con reportlab (dependencia de Odoo), el mismo
        motor del widget de codigo de barras."""
        from reportlab.graphics.barcode import createBarcodeDrawing
        drawing = createBarcodeDrawing(
            'QR', value=value, format='png', width=300, height=300)
        return base64.b64encode(drawing.asString('png')).decode()

    # ---- auto-update: el propio telefono (ya enrolado) revisa y se actualiza --
    # Aqui NO hay token efimero: el equipo se autentica con su X-Foco-Key, la
    # misma llave del envio. El token/QR es para humanos en la web; esto es para
    # los telefonos de la flota, que ya tienen llave.

    def _auth_movil(self):
        key = request.httprequest.headers.get('X-Foco-Key')
        return request.env['foco.mobile.device']._authenticate(key)

    @http.route('/foco/app/latest', type='http', auth='public',
                methods=['GET', 'POST'], csrf=False)
    def app_latest(self, **kw):
        """Le dice al telefono cual es la ultima version publicada. El equipo
        compara con su versionCode y decide si se actualiza."""
        if not self._auth_movil():
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        s = request.env['foco.settings'].sudo().get_settings()
        if not s.mobile_apk:
            return request.make_json_response({'ok': True, 'version_code': 0})
        return request.make_json_response({
            'ok': True,
            'version_code': s.mobile_apk_version_code or 0,
            'version_name': s.mobile_apk_version_name or '',
        })

    @http.route('/foco/app/binario', type='http', auth='public', methods=['GET'])
    def app_binario(self, **kw):
        """Sirve el APK al propio telefono para el auto-update (sin token: se
        autentica con su llave)."""
        if not self._auth_movil():
            return request.make_json_response({'error': 'unauthorized'}, status=401)
        s = request.env['foco.settings'].sudo().get_settings()
        if not s.mobile_apk:
            return request.not_found()
        content = base64.b64decode(s.mobile_apk)
        fn = s.mobile_apk_name or 'foco.apk'
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.android.package-archive'),
            ('Content-Disposition', 'attachment; filename="%s"' % fn),
        ])
