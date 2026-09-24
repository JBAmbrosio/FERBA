"""Capturas de pantalla: la pieza mas invasiva del sistema.

POR QUE EXISTE Y CON QUE LIMITES
    El cliente pidio «que cada cinco minutos se grabe la pantalla». Eso NO es lo
    que hace este modelo, y la diferencia esta razonada en
    `docs/propuesta-capturas-de-pantalla.md`: 96 capturas diarias por persona
    son 2,880 imagenes al dia en un equipo de 30, que nadie va a revisar. El
    valor real acaba siendo el de una captura pedida cuando hay una duda
    concreta, con todo el costo de la otra.

    Aqui hay DOS disparadores y ninguno es el reloj:

      manual        un administrador la pide, con causa escrita.
      sin_clasificar  la persona lleva mucho tiempo en una aplicacion que el
                    catalogo no conoce. Se toma UNA, para identificar la app, y
                    esa app no vuelve a disparar nunca.

    El segundo se agota solo: conforme el catalogo madura, deja de dispararse.
    No es un vigilante, es un mecanismo de descubrimiento que se apaga.

LO QUE NO SE HACE, Y POR QUE
    No se captura «al detectar distraccion». Es la idea que mas se propone y la
    peor: el disparador lo decide una clasificacion que puede estar mal, y la
    persona descubre que la fotografiaron justo cuando el sistema se equivoco.
    Eso no rompe la confianza en las capturas, la rompe en el sistema entero.

APAGADO DE FABRICA
    El interruptor general nace en False y ahi se queda hasta que exista un
    aviso de privacidad firmado que contemple capturas. El aviso vigente promete
    EN NEGRITAS que no se toman; encenderlo sobre ese texto seria romper una
    promesa escrita, no un descuido de configuracion.
"""

from datetime import timedelta

from odoo import api, fields, models


class FocoCapture(models.Model):
    _name = 'foco.capture'
    _description = 'Captura de pantalla'
    _order = 'at desc'

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True,
        ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', store=True, string='Empleado')
    department_id = fields.Many2one(
        related='computer_id.employee_id.department_id', store=True,
        string='Departamento')
    at = fields.Datetime(string='Momento', required=True, index=True)

    trigger = fields.Selection([
        ('manual', 'La pidio un administrador'),
        ('sin_clasificar', 'Aplicacion sin clasificar'),
        ('monitoreo', 'Monitoreo periodico de la app'),
    ], string='Por que se tomo', required=True, index=True)

    # La causa es OBLIGATORIA en las manuales. Una captura sin motivo escrito no
    # se puede pedir: es lo que convierte el registro en algo que sirve el dia
    # que alguien pregunte por que existe esta imagen.
    reason = fields.Char(string='Motivo', help='Por que se pidio esta captura.')
    requested_by = fields.Many2one('res.users', string='La pidio', readonly=True)

    app_id = fields.Many2one('foco.app', string='Aplicacion al frente',
                             ondelete='set null')
    image = fields.Binary(string='Imagen', attachment=True)
    width = fields.Integer(readonly=True)
    height = fields.Integer(readonly=True)
    bytes = fields.Integer(string='Peso (bytes)', readonly=True)

    # Fecha de caducidad PROPIA de cada imagen. Se fija al guardar, no se
    # recalcula, y es lo que borra la purga. Se hace por fila -y no con un solo
    # corte global- porque el monitoreo periodico deja que cada empleado/app
    # tenga su propia vida (foco.watch.retention_days): con una fecha por
    # imagen, capturas con plazos distintos se purgan en una sola consulta.
    # Vacia = no caduca (solo para capturas viejas anteriores a este campo).
    expires_at = fields.Datetime(string='Caduca', readonly=True, index=True)

    # Que la persona lo sepa no es cortesia: es la diferencia entre una
    # herramienta de gestion y una de vigilancia. Se guarda si el aviso llego a
    # mostrarse para poder responder «se le notifico» con un dato, no con una
    # intencion.
    notified = fields.Boolean(
        string='Se le aviso a la persona', readonly=True,
        help='El equipo mostro el aviso en pantalla al tomar la captura.')

    @api.model
    def registrar(self, computer, datos):
        """Guarda una captura que manda el agente. Devuelve el id, o 0.

        Se comprueba el interruptor general AQUI ademas de en el agente. El
        agente puede ser viejo, o alguien puede llamar al endpoint a mano: la
        promesa de «apagado significa que no se guarda nada» tiene que
        sostenerse del lado del servidor, que es el unico que no se puede
        reemplazar por una version parcheada.
        """
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if not ajustes.screenshot_enabled:
            return 0
        imagen = datos.get('image')
        if not imagen:
            return 0
        exe = (datos.get('exe') or '').strip().lower()
        app = self.env['foco.app'].sudo()._por_exe(exe)
        disparador = datos.get('trigger')
        if disparador not in ('manual', 'sin_clasificar', 'monitoreo'):
            disparador = 'sin_clasificar'
        at = self.env['foco.event']._parse_utc(datos.get('at')) or fields.Datetime.now()
        # Vida de la imagen. El monitoreo periodico la toma de SU regla
        # (foco.watch, por empleado y app); los otros dos disparadores, del
        # plazo global de capturas. 0 = no caduca -> sin fecha de caducidad.
        if disparador == 'monitoreo':
            dias = self.env['foco.watch'].sudo().retention_para(
                computer.employee_id, exe)
        else:
            dias = ajustes.screenshot_retention_days or 0
        vals = {
            'computer_id': computer.id,
            'at': at,
            'trigger': disparador,
            'image': imagen,
            'width': int(datos.get('width') or 0),
            'height': int(datos.get('height') or 0),
            'bytes': int(datos.get('bytes') or 0),
            'notified': bool(datos.get('notified')),
            'app_id': app.id if app else False,
            'expires_at': (at + timedelta(days=dias)) if dias > 0 else False,
        }
        if datos.get('command_id'):
            orden = self.env['foco.command'].sudo().browse(
                int(datos['command_id'])).exists()
            if orden and orden.computer_id == computer:
                vals['reason'] = orden.payload or ''
                vals['requested_by'] = orden.create_uid.id
        return self.sudo().create(vals).id

    @api.model
    def apps_ya_capturadas(self, computer):
        """Ejecutables de los que ya se tomo una captura en este equipo.

        El agente los recibe para no repetir: el disparador de aplicacion sin
        clasificar existe para IDENTIFICAR una app, y una vez que hay una imagen
        de ella, volver a fotografiarla no agrega nada y si agrega intrusion.
        """
        capturas = self.sudo().search([
            ('computer_id', '=', computer.id),
            ('trigger', '=', 'sin_clasificar'),
            ('app_id', '!=', False)])
        return [e for e in capturas.mapped('app_id.exe') if e]
