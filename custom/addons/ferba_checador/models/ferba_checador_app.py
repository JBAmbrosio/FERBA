# -*- coding: utf-8 -*-
"""Versiones de la app del checador que las tabletas instalan solas.

IT sube el APK aqui. Cada tableta lo ve en su siguiente sincronizacion, lo
descarga, comprueba que es el archivo que publico Odoo (huella SHA-256) y que es
la misma app con una version mayor, y lo instala cuando nadie la esta usando.
Android, ademas, rechaza cualquier APK que no venga firmado con la misma llave.
"""
import base64
import hashlib

from odoo import api, fields, models
from odoo.exceptions import UserError


class FerbaChecadorApp(models.Model):
    _name = 'ferba.checador.app'
    _description = 'Version de la app del checador'
    _order = 'version_code desc'

    name = fields.Char(string='Version', required=True, help='La que ve la gente, por ejemplo 1.2.0.')
    version_code = fields.Integer(
        string='Numero de version', required=True,
        help='El versionCode con el que se compilo el APK. La tableta solo instala un numero '
             'mayor al que ya tiene, y lo comprueba contra el propio archivo.')
    apk = fields.Binary(string='APK', required=True, attachment=True)
    apk_nombre = fields.Char(string='Nombre del archivo')
    sha256 = fields.Char(string='Huella SHA-256', compute='_compute_huella', store=True)
    tamano = fields.Integer(string='Tamano (bytes)', compute='_compute_huella', store=True)
    activa = fields.Boolean(
        string='Publicada', default=True,
        help='Apagala para detener el despliegue: las tabletas que aun no la instalan ya no la reciben.')
    checador_ids = fields.Many2many(
        'ferba.checador', string='Solo en estas tabletas',
        help='Vacio = todas. Para probar una version nueva, publicala primero en una sola tableta.')
    notas = fields.Text(string='Que cambia')
    instaladas = fields.Integer(string='Tabletas que ya la tienen', compute='_compute_instaladas')

    _version_unica = models.Constraint(
        'unique(version_code)', 'Ya hay un APK con ese numero de version.')

    @api.depends('apk')
    def _compute_huella(self):
        for v in self:
            crudo = base64.b64decode(v.with_context(bin_size=False).apk or b'')
            v.sha256 = hashlib.sha256(crudo).hexdigest() if crudo else False
            v.tamano = len(crudo)

    def _compute_instaladas(self):
        Checador = self.env['ferba.checador'].sudo()
        for v in self:
            v.instaladas = Checador.search_count(
                [('estado', '=', 'activo'), ('version_app', '=', v.name)])

    @api.constrains('apk', 'version_code')
    def _check_apk(self):
        for v in self:
            if v.version_code < 1:
                raise UserError('El numero de version debe ser 1 o mayor.')
            crudo = base64.b64decode(v.with_context(bin_size=False).apk or b'')
            if crudo[:2] != b'PK':
                raise UserError('Ese archivo no es un APK.')

    @api.model
    def _para(self, checador):
        """La version mas nueva publicada para esa tableta, o vacio."""
        return self.sudo().search([
            ('activa', '=', True),
            '|', ('checador_ids', '=', False), ('checador_ids', 'in', checador.ids),
        ], order='version_code desc', limit=1)