import secrets

from odoo import api, fields, models


class FocoAppToken(models.Model):
    """Enlace efimero para bajar el APK. Se acuña cuando alguien completa el
    codigo de teclas en /foco/instalar y caduca a los pocos minutos. La
    seguridad real es esta caducidad (no el codigo de teclas, que es solo una
    puerta discreta): aunque alguien de con la ruta, cada token vive poco y
    admite pocas descargas. Se limpia solo al acuñar el siguiente."""
    _name = 'foco.app.token'
    _description = 'Enlace efimero de descarga del APK'
    _order = 'create_date desc'

    _uniq = models.Constraint('unique(token)', 'Ese token ya existe.')

    token = fields.Char(required=True, index=True, readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True, index=True)
    downloads = fields.Integer(default=0, readonly=True)
    max_downloads = fields.Integer(default=5, readonly=True)

    # Tope duro para que un token no se pueda descargar sin fin dentro de su
    # ventana; generoso para tolerar el reintento del gestor de descargas.
    _TOPE_DESCARGAS = 5

    @api.model
    def _purgar_vencidos(self):
        """Borra los que ya caducaron. Barato y evita que la tabla crezca."""
        self.sudo().search([('expires_at', '<', fields.Datetime.now())]).unlink()

    @api.model
    def emitir(self, minutos):
        """Crea un token nuevo y de paso limpia los vencidos. Devuelve el
        registro. `minutos` = cuanto vive."""
        self._purgar_vencidos()
        minutos = minutos if minutos and minutos > 0 else 10
        return self.sudo().create({
            'token': secrets.token_urlsafe(24),
            'expires_at': fields.Datetime.add(fields.Datetime.now(), minutes=minutos),
            'max_downloads': self._TOPE_DESCARGAS,
        })

    @api.model
    def consumir(self, token):
        """Valida un token (existe, no caduco, no rebaso el tope) y cuenta la
        descarga. Devuelve el registro o un recordset vacio."""
        if not token:
            return self.browse()
        rec = self.sudo().search([('token', '=', token)], limit=1)
        if not rec:
            return self.browse()
        if rec.expires_at < fields.Datetime.now() or rec.downloads >= rec.max_downloads:
            return self.browse()
        rec.downloads += 1
        return rec
