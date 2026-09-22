from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FocoCommand(models.Model):
    _name = 'foco.command'
    _description = 'Orden de control remoto'
    # mail.thread: para avisar a quien pidio la orden cuando el equipo la
    # ejecuta o falla, usando las notificaciones propias de Odoo (la campanita).
    # Se hereda en el modelo -y no se usa message_notify suelto- para que el
    # aviso quede LIGADO a la orden: al hacer clic en la campanita, el
    # administrador aterriza en este registro, con el resultado y el motivo a la
    # vista, en vez de en un texto sin a donde ir.
    _inherit = ['mail.thread']
    _order = 'create_date desc'

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', store=True, string='Empleado')
    command_type = fields.Selection([
        ('message', 'Mostrar mensaje'),
        ('lock', 'Bloquear equipo'),
        ('kill_app', 'Cerrar aplicacion'),
        ('logoff', 'Cerrar sesion'),
        ('screenshot', 'Tomar captura de pantalla'),
    ], string='Orden', required=True, default='message')
    payload = fields.Char(
        string='Parametro',
        help='Mensaje a mostrar, ejecutable a cerrar (p.ej. spotify.exe), o '
             'EL MOTIVO de la captura.')
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('sent', 'Enviada'),
        ('done', 'Ejecutada'),
        ('error', 'Error'),
    ], string='Estado', default='pending', required=True, index=True)
    result = fields.Text(readonly=True)
    sent_at = fields.Datetime(string='Enviada', readonly=True)
    done_at = fields.Datetime(string='Ejecutada', readonly=True)

    @api.constrains('command_type', 'payload')
    def _check_motivo_captura(self):
        """Una captura sin motivo escrito no se puede pedir.

        No es burocracia: es lo unico que permite contestar, meses despues, por
        que existe la fotografia de la pantalla de una persona. Un registro que
        dice quien y cuando pero no por que no defiende a nadie -ni a la
        empresa ni al empleado-.
        """
        for cmd in self:
            if cmd.command_type == 'screenshot' and not (cmd.payload or '').strip():
                raise ValidationError(
                    'Para pedir una captura de pantalla hay que escribir el '
                    'motivo. Queda registrado junto con la imagen y con quien '
                    'la pidio.')

    @api.model_create_multi
    def create(self, vals_list):
        """Una captura solo se puede pedir con el interruptor general encendido.

        Se corta al crear la orden y no al ejecutarla, para que el rechazo se
        vea AQUI -con su explicacion- en vez de que la orden se quede pendiente
        para siempre y nadie sepa por que nunca llego la imagen.
        """
        if any(v.get('command_type') == 'screenshot' for v in vals_list):
            if not self.env['foco.settings'].sudo().get_settings().screenshot_enabled:
                raise ValidationError(
                    'Las capturas de pantalla estan APAGADAS en la '
                    'configuracion de Foco. Se encienden cuando exista un '
                    'aviso de privacidad firmado que las contemple: el aviso '
                    'vigente dice expresamente que no se toman.')
        return super().create(vals_list)

    # Como se lee cada orden en el aviso. La captura se nombra aparte porque es
    # la que el administrador mas espera ver confirmada.
    _ETIQUETA = {
        'message': 'Mensaje en pantalla',
        'lock': 'Bloqueo del equipo',
        'kill_app': 'Cierre de una aplicacion',
        'logoff': 'Cierre de sesion',
        'screenshot': 'Captura de pantalla',
    }

    def notificar_resultado(self):
        """Avisa a quien pidio la orden que el equipo ya la ejecuto -o fallo-.

        Se dispara desde el endpoint que recibe el resultado del agente, o sea
        cuando la accion REALMENTE ocurrio en el equipo, no cuando se creo la
        orden. Es la diferencia entre 'lo pedi' y 'ya paso', que es justo lo que
        el administrador quiere saber.

        Notifica al SOLICITANTE (`create_uid`), no a todos los administradores:
        el ruido de que a cada quien le lleguen las ordenes de los demas es la
        forma mas rapida de que una notificacion util se vuelva una que se
        ignora.

        El aviso va por DOS canales, a proposito:

          - un TOAST por el bus (`_bus_send('simple_notification', ...)`), que
            aparece al instante en la pantalla de quien la pidio. Es lo que se
            ve "cuando se aplico". No depende de la preferencia de correo del
            usuario -que por omision es 'email', y por eso `message_notify`
            mandaba un correo invisible en vez de encender la campanita: ese fue
            el defecto que se corrigio aqui-.

          - un mensaje en el CHATTER del registro, para que quede constancia
            permanente ligada a la orden aunque nadie estuviera conectado en ese
            segundo. El toast es inmediato pero efimero; el chatter es la
            historia.
        """
        for cmd in self:
            quien = cmd.create_uid.partner_id
            if not quien:
                continue
            accion = cmd._ETIQUETA.get(cmd.command_type, cmd.command_type)
            equipo = cmd.computer_id.display_name
            if cmd.state == 'done':
                tipo, titulo = 'success', 'Foco · aplicado'
                cuerpo = 'Se aplico en %s: %s.' % (equipo, accion)
            elif cmd.state == 'error':
                tipo, titulo = 'danger', 'Foco · no se pudo aplicar'
                cuerpo = ('No se pudo aplicar en %s: %s. %s'
                          % (equipo, accion, cmd.result or ''))
            else:
                continue
            quien._bus_send('simple_notification', {
                'type': tipo, 'title': titulo, 'message': cuerpo, 'sticky': False,
            })
            # Constancia permanente, sin correo (subtype nota, sin seguidores
            # nuevos): vive en el historial de la orden.
            cmd.message_post(body=cuerpo, subject=titulo,
                             message_type='comment',
                             subtype_xmlid='mail.mt_note')

    def action_resend(self):
        self.write({'state': 'pending', 'result': False,
                    'sent_at': False, 'done_at': False})
