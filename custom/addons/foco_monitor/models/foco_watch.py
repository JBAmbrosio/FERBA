"""Monitoreo PERIODICO de pantalla, por empleado y por aplicacion O SITIO.

Es el TERCER disparador de captura, ademas de los dos de `foco.capture`:

    manual         un administrador la pide, con causa escrita.
    sin_clasificar  UNA captura para identificar una app desconocida.
    monitoreo      ESTE: mientras cierta app -o la pestana de cierto sitio-
                   este AL FRENTE en el equipo de cierta persona, se toma una
                   captura de pantalla completa cada N minutos.

A diferencia de los otros dos, este es una decision POR PERSONA: se elige a
quien se le monitorea que, cada cuanto, y cuanto vive cada imagen. El
interruptor general de capturas de `foco.settings` sigue mandando: si esta
apagado, esto no toma ni una.

POR QUE «AL FRENTE» Y NO «ABIERTA» (cambio del 24-sep-2026)
    La primera version disparaba mientras la app tuviera una ventana abierta,
    estuviera o no al frente. Medido en produccion: las capturas de la regla
    de Excel de una persona no mostraban Excel -estaba detras de otra ventana-
    y ademas quedaban firmadas «Excel», porque el agente les ponia el nombre
    de la regla y no el de la app que se veia. Una captura de pantalla solo
    dice algo de X si X esta en la pantalla. Ahora el agente dispara con el
    objetivo al frente, y la captura lleva la app REAL al frente, el sitio de
    la pestana activa y, aparte, la regla que la disparo.

SITIOS
    El agente ya lee el sitio de la pestana activa del navegador que esta al
    frente (`browser_url`). Es la unica pestana que se puede conocer sin
    recorrer el navegador entero, y es la unica que aparece en la captura, asi
    que basta. Una regla de sitio aplica en cualquier navegador.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FocoWatch(models.Model):
    _name = 'foco.watch'
    _description = 'Monitoreo periodico de pantalla por app o sitio y empleado'
    _order = 'employee_id, target, app_id, site_id'
    _rec_name = 'display_name'

    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        ondelete='cascade', index=True,
        help='La persona a la que se le monitorea esto.')
    target = fields.Selection(
        [('app', 'Aplicación'), ('site', 'Sitio web')],
        string='Qué vigilar', required=True, default='app',
        help='Una aplicación del equipo, o la pestaña de un sitio en cualquier '
             'navegador (WhatsApp Web, por ejemplo).')
    app_id = fields.Many2one(
        'foco.app', string='Aplicación', ondelete='cascade',
        domain="[('id', 'in', app_ids_permitidas)]",
        help='Solo se listan las aplicaciones que este empleado ya ha usado.')
    site_id = fields.Many2one(
        'foco.site', string='Sitio web', ondelete='cascade',
        help='El dominio tal como aparece en la barra del navegador '
             '(web.whatsapp.com). Si no está en el catálogo, se crea al '
             'escribirlo. Aplica en cualquier navegador.')
    interval_minutes = fields.Integer(
        string='Captura cada (min)', required=True, default=15,
        help='Cada cuántos minutos se toma una captura mientras la aplicación, '
             'o la pestaña del sitio, esté AL FRENTE en el equipo de esta '
             'persona. La captura es de pantalla completa (todos los monitores).')
    retention_days = fields.Integer(
        string='Vida de las capturas (días)', required=True,
        default=lambda self: self._default_retention(),
        help='Cuánto se conservan las capturas de esta regla antes de borrarse '
             'solas. Es un plazo PROPIO: una imagen de la pantalla de alguien '
             'envejece mal, y puede querer conservarse menos que el resto.')
    active = fields.Boolean(default=True)
    display_name = fields.Char(compute='_compute_display_name')

    # Solo para el dominio de `app_id`: las apps que ESTE empleado ha usado.
    # No se almacena; se recalcula al elegir empleado. Sin esto el selector
    # ofreceria el catalogo global entero y se pediria monitorear apps que la
    # persona nunca abre.
    app_ids_permitidas = fields.Many2many(
        'foco.app', string='Apps del empleado',
        compute='_compute_app_ids_permitidas')

    @api.depends('target', 'app_id', 'site_id', 'interval_minutes')
    def _compute_display_name(self):
        for rec in self:
            que = rec.site_id.host if rec.target == 'site' else rec.app_id.display_name
            rec.display_name = '%s · cada %d min' % (que or '?', rec.interval_minutes or 0)

    @api.onchange('target')
    def _onchange_target(self):
        # Una regla vigila UNA cosa: al cambiar de tipo se suelta la otra, para
        # que no quede una app guardada debajo de una regla de sitio.
        if self.target == 'app':
            self.site_id = False
        else:
            self.app_id = False

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

    @api.constrains('target', 'app_id', 'site_id')
    def _check_objetivo(self):
        # El objetivo va en Python y no como `required` del campo: son dos
        # campos y solo uno aplica segun el tipo.
        for rec in self:
            if rec.target == 'app' and not rec.app_id:
                raise ValidationError('Elige la aplicación a vigilar.')
            if rec.target == 'site' and not rec.site_id:
                raise ValidationError(
                    'Escribe el sitio a vigilar (por ejemplo web.whatsapp.com).')

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

    @api.constrains('employee_id', 'target', 'app_id', 'site_id', 'active')
    def _check_sin_duplicado(self):
        # Una sola regla ACTIVA por (empleado, objetivo). Se valida en Python y
        # no con un UNIQUE para no estorbar al archivar: se puede archivar una
        # regla y crear otra para el mismo objetivo sin chocar con una fila
        # muerta.
        for rec in self.filtered('active'):
            dominio = [('employee_id', '=', rec.employee_id.id),
                       ('target', '=', rec.target),
                       ('active', '=', True), ('id', '!=', rec.id)]
            if rec.target == 'site':
                dominio.append(('site_id', '=', rec.site_id.id))
                que = rec.site_id.host
            else:
                dominio.append(('app_id', '=', rec.app_id.id))
                que = rec.app_id.display_name
            if self.search(dominio, limit=1):
                raise ValidationError(
                    'Ya hay una regla activa de monitoreo para %s en el equipo '
                    'de %s.' % (que, rec.employee_id.display_name))

    @api.constrains('target', 'app_id', 'employee_id')
    def _check_app_del_empleado(self):
        # La app tiene que ser una que la persona haya usado. El dominio del
        # formulario ya lo restringe, pero alguien puede crear el registro por
        # RPC o import: la garantia va tambien en el servidor.
        #
        # El sitio NO lleva esta guarda a proposito: la pregunta tipica es
        # «avisame cuando abra WhatsApp Web», y eso se decide antes de que lo
        # abra. El catalogo de sitios ya es real -solo tiene lo que alguien
        # visito- y crear uno por su dominio no es pedir a ciegas.
        for rec in self:
            if rec.target == 'app' and rec.app_id \
                    and rec.app_id not in rec.app_ids_permitidas:
                raise ValidationError(
                    '%s no aparece en el uso de %s; solo se pueden monitorear '
                    'aplicaciones que la persona ya haya usado.'
                    % (rec.app_id.display_name, rec.employee_id.display_name))

    @api.model
    def monitor_para(self, employee):
        """Reglas de APLICACION activas de una persona, como las entiende el agente.

        Devuelve [{'exe': 'code.exe', 'minutos': 15}, ...]. Vacio si no hay
        empleado o no tiene reglas: el agente lo interpreta como "no monitorear
        nada", que es justo lo que debe pasar. Las de sitio van aparte
        (`monitor_sitios_para`): un agente anterior al 24-sep solo conoce esta
        lista y no debe tropezar con renglones sin `exe`.
        """
        if not employee:
            return []
        salida = []
        for regla in self.sudo().search([('employee_id', '=', employee.id),
                                         ('target', '=', 'app')]):
            exe = (regla.app_id.exe or '').strip().lower()
            if exe and regla.interval_minutes > 0:
                salida.append({'exe': exe, 'minutos': regla.interval_minutes})
        return salida

    @api.model
    def monitor_sitios_para(self, employee):
        """Reglas de SITIO activas de una persona: [{'host', 'minutos'}, ...]."""
        if not employee:
            return []
        salida = []
        for regla in self.sudo().search([('employee_id', '=', employee.id),
                                         ('target', '=', 'site')]):
            host = (regla.site_id.host or '').strip().lower()
            if host and regla.interval_minutes > 0:
                salida.append({'host': host, 'minutos': regla.interval_minutes})
        return salida

    @api.model
    def retention_para(self, employee, exe=None, host=None):
        """Dias de vida de una captura de monitoreo, por la regla que la disparo.

        Se usa al GUARDAR la captura para fijarle su fecha de caducidad. Con
        `host` se busca la regla de sitio; con `exe`, la de aplicacion. Si la
        regla ya no existe (se borro entre la captura y el guardado), cae en el
        plazo global de capturas.
        """
        if employee:
            host = (host or '').strip().lower()
            exe = (exe or '').strip().lower()
            if host:
                regla = self.sudo().search([
                    ('employee_id', '=', employee.id), ('target', '=', 'site'),
                    ('site_id.host', '=', host)], limit=1)
                if regla:
                    return regla.retention_days
            if exe:
                regla = self.sudo().search([
                    ('employee_id', '=', employee.id), ('target', '=', 'app'),
                    ('app_id.exe', 'in', self.env['foco.app']._variantes_exe(exe)),
                ], limit=1)
                if regla:
                    return regla.retention_days
        return self.env['foco.settings'].sudo().get_settings().screenshot_retention_days or 0
