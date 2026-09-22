"""Monitoreo PERIODICO de pantalla, por empleado y por aplicacion.

Es el TERCER disparador de captura, ademas de los dos de `foco.capture`:

    manual         un administrador la pide, con causa escrita.
    sin_clasificar  UNA captura para identificar una app desconocida.
    monitoreo      ESTE: mientras cierta app tenga una ventana ABIERTA en el
                   equipo de cierta persona -este o no al frente-, se toma una
                   captura de pantalla completa cada N minutos.

A diferencia de los otros dos, este es una decision POR PERSONA: se elige a
quien se le monitorea que aplicacion, cada cuanto, y cuanto vive cada imagen.
Solo se ofrecen las apps que ESE empleado ya ha usado (lo demas seria pedir a
ciegas), y el interruptor general de capturas de `foco.settings` sigue mandando:
si esta apagado, esto no toma ni una.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FocoWatch(models.Model):
    _name = 'foco.watch'
    _description = 'Monitoreo periodico de pantalla por app y empleado'
    _order = 'employee_id, app_id'

    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        ondelete='cascade', index=True,
        help='La persona a la que se le monitorea esta aplicacion.')
    app_id = fields.Many2one(
        'foco.app', string='Aplicacion', required=True, ondelete='cascade',
        domain="[('id', 'in', app_ids_permitidas)]",
        help='Solo se listan las aplicaciones que este empleado ya ha usado.')
    interval_minutes = fields.Integer(
        string='Captura cada (min)', required=True, default=15,
        help='Cada cuantos minutos se toma una captura mientras esta aplicacion '
             'tenga una ventana abierta en el equipo de esta persona, este o no '
             'al frente. La captura es de pantalla completa (todos los monitores).')
    retention_days = fields.Integer(
        string='Vida de las capturas (dias)', required=True,
        default=lambda self: self._default_retention(),
        help='Cuanto se conservan las capturas de esta regla antes de borrarse '
             'solas. Es un plazo PROPIO: una imagen de la pantalla de alguien '
             'envejece mal, y puede querer conservarse menos que el resto.')
    active = fields.Boolean(default=True)

    # Solo para el dominio de `app_id`: las apps que ESTE empleado ha usado.
    # No se almacena; se recalcula al elegir empleado. Sin esto el selector
    # ofreceria el catalogo global entero y se pediria monitorear apps que la
    # persona nunca abre.
    app_ids_permitidas = fields.Many2many(
        'foco.app', string='Apps del empleado',
        compute='_compute_app_ids_permitidas')

    @api.constrains('interval_minutes', 'retention_days')
    def _check_positivos(self):
        # En Odoo 19 los CHECK por `_sql_constraints` estan deprecados; el
        # invariante -numeros mayores que cero- se hace en Python, que ademas da
        # un mensaje claro en vez de un error de base de datos.
        for rec in self:
            if rec.interval_minutes <= 0:
                raise ValidationError(
                    'El intervalo de captura tiene que ser mayor que cero.')
            if rec.retention_days <= 0:
                raise ValidationError(
                    'La vida de las capturas tiene que ser mayor que cero.')

    @api.model
    def _default_retention(self):
        # Arranca del plazo global de capturas para no reinventar un numero;
        # si el global esta en "no borrar" (0), la regla igual necesita un
        # plazo, asi que cae en 30.
        dias = self.env['foco.settings'].sudo().get_settings().screenshot_retention_days
        return dias or 30

    @api.depends('employee_id')
    def _compute_app_ids_permitidas(self):
        Usage = self.env['foco.usage'].sudo()
        for rec in self:
            if rec.employee_id:
                grupos = Usage._read_group(
                    [('employee_id', '=', rec.employee_id.id),
                     ('app_id', '!=', False)],
                    ['app_id'])
                rec.app_ids_permitidas = [(6, 0, [g[0].id for g in grupos if g[0]])]
            else:
                rec.app_ids_permitidas = [(6, 0, [])]

    @api.constrains('employee_id', 'app_id', 'active')
    def _check_sin_duplicado(self):
        # Una sola regla ACTIVA por (empleado, app). Se valida en Python y no con
        # un UNIQUE para no estorbar al archivar: se puede archivar una regla y
        # crear otra para la misma app sin chocar con una fila muerta.
        for rec in self.filtered('active'):
            otras = self.search([
                ('employee_id', '=', rec.employee_id.id),
                ('app_id', '=', rec.app_id.id),
                ('active', '=', True),
                ('id', '!=', rec.id),
            ], limit=1)
            if otras:
                raise ValidationError(
                    'Ya hay una regla activa de monitoreo para %s en el equipo '
                    'de %s.' % (rec.app_id.display_name, rec.employee_id.display_name))

    @api.constrains('app_id', 'employee_id')
    def _check_app_del_empleado(self):
        # La app tiene que ser una que la persona haya usado. El dominio del
        # formulario ya lo restringe, pero alguien puede crear el registro por
        # RPC o import: la garantia va tambien en el servidor.
        for rec in self:
            if rec.app_id and rec.app_id not in rec.app_ids_permitidas:
                raise ValidationError(
                    '%s no aparece en el uso de %s; solo se pueden monitorear '
                    'aplicaciones que la persona ya haya usado.'
                    % (rec.app_id.display_name, rec.employee_id.display_name))

    @api.model
    def monitor_para(self, employee):
        """Reglas activas de una persona, en la forma que entiende el agente.

        Devuelve [{'exe': 'code.exe', 'minutos': 15}, ...]. Vacio si no hay
        empleado o no tiene reglas: el agente lo interpreta como "no monitorear
        nada", que es justo lo que debe pasar.
        """
        if not employee:
            return []
        salida = []
        for regla in self.sudo().search([('employee_id', '=', employee.id)]):
            exe = (regla.app_id.exe or '').strip().lower()
            if exe and regla.interval_minutes > 0:
                salida.append({'exe': exe, 'minutos': regla.interval_minutes})
        return salida

    @api.model
    def retention_para(self, employee, exe):
        """Dias de vida de las capturas de monitoreo de (empleado, exe).

        Se usa al GUARDAR la captura para fijarle su fecha de caducidad. Si la
        regla ya no existe (se borro entre la captura y el guardado), cae en el
        plazo global de capturas.
        """
        exe = (exe or '').strip().lower()
        if employee and exe:
            regla = self.sudo().search([
                ('employee_id', '=', employee.id),
                ('app_id.exe', '=', exe),
            ], limit=1)
            if regla:
                return regla.retention_days
        return self.env['foco.settings'].sudo().get_settings().screenshot_retention_days or 0
