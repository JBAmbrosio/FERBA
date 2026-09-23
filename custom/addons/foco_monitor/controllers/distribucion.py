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
