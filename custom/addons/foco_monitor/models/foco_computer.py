import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# El agente empuja cada 5 min. 15 min de silencio = 3 envios perdidos: ya no es
# un tropiezo de red, es un agente caido.
HEALTH_OK_MINUTES = 15

# El servicio verifica la politica cada 5 min (FOCO_POLICY_SECS). Sin una
# confirmacion en el TRIPLE de eso, el equipo dejo de latir "sigo bloqueado":
# el watchdog puede estar caido o la tarea desactivada. No es un numero
# inventado, es multiplo de la cadencia real.
POLICY_STALE_MINUTES = 15


class FocoComputer(models.Model):
    _name = 'foco.computer'
    _description = 'Computadora monitoreada'
    # mail.thread para las notificaciones (la campanita) cuando un equipo aplica
    # o falla el bloqueo de navegacion. Ver notificar_politica().
    _inherit = ['mail.thread']
    _order = 'last_seen desc'

    _api_key_uniq = models.Constraint('unique(api_key)',
                                      'La API key debe ser unica.')

    name = fields.Char(string='Equipo (hostname)', required=True)
    windows_user = fields.Char(string='Usuario de Windows')
    employee_id = fields.Many2one('hr.employee', string='Empleado')
    department_id = fields.Many2one(
        related='employee_id.department_id', store=True, string='Departamento')
    agent_db_id = fields.Char(
        string='Huella de la base del agente', readonly=True,
        help='Identificador que el agente crea dentro de su base local. Si '
             'cambia, esa base se borro o se reemplazo: no lo impide -el '
             'archivo vive donde el usuario escribe- pero deja de pasar '
             'inadvertido.')

    utc_offset_min = fields.Integer(
        string='Desfase del equipo (min)', readonly=True,
        help='Lo que reporta el propio equipo respecto a UTC. Se usa como '
             'ULTIMO recurso para fechar su jornada, cuando ni el empleado ni '
             'el calendario de la empresa tienen zona horaria configurada. Sin '
             'esta red, esa falta de configuracion movia un dia entero de '
             'actividad sin que nadie lo notara.')

    api_key = fields.Char(
        string='API key', required=True, copy=False, readonly=True,
        default=lambda self: secrets.token_urlsafe(24),
        help='Clave que usa el agente para autenticarse. Una por equipo.')
    last_seen = fields.Datetime(string='Ultima señal', readonly=True)
    active = fields.Boolean(default=True)
    usage_ids = fields.One2many('foco.usage', 'computer_id')
    command_ids = fields.One2many('foco.command', 'computer_id')
    command_count = fields.Integer(compute='_compute_counts')
    pending_command_count = fields.Integer(compute='_compute_counts')

    # ------------------------------------------------------- navegacion
    policy_id = fields.Many2one(
        'foco.policy', string='Perfil de navegacion', ondelete='set null',
        help='Que sitios puede abrir este equipo. Si se deja vacio toma el '
             'perfil por omision de la configuracion.')
    policy_version = fields.Char(
        string='Politica aplicada', readonly=True,
        help='La version que el equipo dice tener PUESTA, no la que se le '
             'mando. Que las dos coincidan es la unica prueba de que el bloqueo '
             'llego; sin este campo, "lo bloquee en Odoo" y "esta bloqueado en '
             'la maquina" se ven igual.')
    policy_applied_at = fields.Datetime(string='Aplicada', readonly=True)
    policy_detail = fields.Char(
        string='Detalle de la politica', readonly=True,
        help='Que navegadores se escribieron, o el error si alguno fallo.')
    policy_verified_at = fields.Datetime(
        string='Bloqueo verificado', readonly=True,
        help='Ultima vez que el equipo CONFIRMO, leyendo su propio registro, '
             'que el bloqueo puesto coincide con el vigente. Es la prueba de '
             'que sigue aplicado; si envejece, el equipo dejo de confirmarlo.')
    policy_drift = fields.Boolean(
        string='Bloqueo alterado', readonly=True,
        help='El ultimo reporte del equipo dijo que lo PUESTO no coincidia con '
             'lo vigente (algo o alguien lo quito). Se reconcilia solo en el '
             'siguiente ciclo; si persiste, el equipo no lo esta aplicando.')
    # Lo que el SERVICIO del equipo dice de su ultimo ciclo, traido por el
    # agente. Existe porque el servicio habla con Odoo por su cuenta y, si esa
    # conexion falla, "Nunca aplicada" no decia por que (24-sep: un equipo dos
    # dias sin bloqueo y sin una sola linea en su log). Es DIAGNOSTICO: el
    # archivo de donde sale lo puede editar la persona vigilada, asi que NO
    # interviene en `policy_sync`; la prueba del bloqueo sigue siendo el latido
    # `policy_verified_at`, que el servicio manda directo.
    service_state = fields.Char(
        string='Servicio: ultimo ciclo', readonly=True,
        help='sin_conexion, sin_llave, http_401, verificado, aplicado... '
             '"sin_archivo" = servicio viejo, no instalado o que nunca corrio.')
    service_detail = fields.Char(string='Servicio: detalle', readonly=True)
    service_at = fields.Datetime(
        string='Servicio: hora del ciclo', readonly=True,
        help='Cuando corrio ese ciclo segun el equipo. Si envejece mientras el '
             'agente sigue enviando, el servicio dejo de correr.')
    service_same_folder = fields.Boolean(
        string='Servicio y agente comparten carpeta', readonly=True,
        help='Si no, el servicio no encuentra la llave del agente y no puede '
             'pedir la politica.')
    service_reported_at = fields.Datetime(
        string='Servicio: recibido', readonly=True,
        help='Ultimo envio del agente que trajo el estado del servicio. Vacio = '
             'el agente es anterior a este reporte.')

    policy_sync = fields.Selection(
        [('off', 'Bloqueo apagado'), ('sin_perfil', 'Sin perfil'),
         ('al_dia', 'Al dia'), ('pendiente', 'Pendiente'),
         ('drift', 'Alterado'), ('sin_verificar', 'Sin verificar'),
         ('nunca', 'Nunca aplicada')],
        string='Estado del bloqueo', compute='_compute_policy_sync')

    @api.depends('policy_id', 'policy_id.version', 'policy_id.active',
                 'policy_version', 'policy_verified_at', 'policy_drift')
    def _compute_policy_sync(self):
        ajustes = self.env['foco.settings'].sudo().get_settings()
        Policy = self.env['foco.policy'].sudo()
        ahora = fields.Datetime.now()
        for c in self:
            # La MISMA decision que publica la lista al equipo, no una copia:
            # un perfil archivado ya no se aplica, y aqui tiene que verse igual.
            perfil = Policy._perfil_vigente(c)
            if not ajustes.block_enabled:
                c.policy_sync = 'off'
            elif not perfil:
                c.policy_sync = 'sin_perfil'
            elif not c.policy_version:
                c.policy_sync = 'nunca'
            elif c.policy_drift:
                # El equipo reporto que le quitaron el bloqueo. Se reconcilia
                # solo, pero mientras tanto NO esta puesto: hay que verlo.
                c.policy_sync = 'drift'
            else:
                esperada = Policy.payload_for(c).get('version')
                if c.policy_version != esperada:
                    c.policy_sync = 'pendiente'
                elif (not c.policy_verified_at
                      or (ahora - c.policy_verified_at).total_seconds()
                      > POLICY_STALE_MINUTES * 60):
                    # La version cuadra pero el equipo no lo confirma hace rato:
                    # no se puede AFIRMAR que sigue puesto (agente viejo que no
                    # late, watchdog caido, tarea desactivada). No es 'al dia'.
                    c.policy_sync = 'sin_verificar'
                else:
                    c.policy_sync = 'al_dia'

    integrity_alert = fields.Char(
        string='Anomalia de integridad', readonly=True,
        help='Ultima inconsistencia detectada al recibir datos de este equipo '
             '(totales imposibles o reloj desfasado). Es un INDICIO para revisar '
             'con la persona, nunca una acusacion automatica.')
    integrity_alert_at = fields.Datetime(string='Detectada', readonly=True)

    # --- como se ve y como se busca un equipo -------------------------------
    # Por omision un equipo se identifica por su hostname (FBT308DDF), que al
    # elegirlo en una orden o en cualquier desplegable no le dice nada a nadie.
    # Se muestra la PERSONA primero -que es como la gente lo reconoce- y se deja
    # la clave entre parentesis para no perder de vista DE QUE maquina se trata
    # cuando alguien tiene mas de una, o cuando el equipo aun no tiene empleado.
    @api.depends('name', 'employee_id', 'employee_id.name')
    def _compute_display_name(self):
        for rec in self:
            clave = rec.name or ''
            persona = rec.employee_id.name if rec.employee_id else ''
            if persona and clave:
                rec.display_name = '%s (%s)' % (persona, clave)
            else:
                rec.display_name = persona or clave or 'Equipo sin nombre'

    @api.model
    def _search_display_name(self, operator, value):
        # Que al teclear en el desplegable se encuentre por el nombre de la
        # PERSONA ademas de por la clave. Solo para las busquedas "positivas"
        # (contiene / igual); las negativas se dejan al ORM, donde un OR daria
        # justo el resultado contrario al que se pide.
        if value and operator in ('ilike', 'like', '=', '=ilike', '=like'):
            return ['|', ('name', operator, value),
                    ('employee_id.name', operator, value)]
        return super()._search_display_name(operator, value)

    def action_clear_integrity_alert(self):
        self.write({'integrity_alert': False, 'integrity_alert_at': False})

    def notificar_politica(self, aplicada, detalle):
        """Avisa, con la campanita de Odoo, que un equipo aplico -o no pudo
        aplicar- el bloqueo de navegacion.

        El bloqueo NO es una orden puntual: el servicio reporta lo aplicado en
        CADA ciclo, cada pocos minutos. Notificar en cada reporte seria ruido
        puro. Por eso solo se avisa cuando:

          - la version aplicada CAMBIO respecto de la anterior -o sea el equipo
            acaba de recibir un cambio que alguien hizo en Odoo-; o
          - el detalle trae un FALLO -un equipo que no pudo aplicar el bloqueo,
            que es justo lo que hay que atender-.

        Se avisa a quien EDITO la politica por ultima vez: hizo el cambio y es
        quien quiere saber que llego a los equipos. Sin politica asignada -un
        equipo que quedo sin perfil- no hay a quien avisar del exito, pero un
        fallo se avisa a los administradores de Foco, porque un bloqueo que no
        se aplica no puede quedar sin doliente.
        """
        self.ensure_one()
        hay_fallo = 'FALLO' in (detalle or '')
        cambio = bool(aplicada) and aplicada != (self.policy_version or '')
        if not hay_fallo and not cambio:
            return

        perfil = self.policy_id
        destinatarios = perfil.write_uid.partner_id if perfil else self.env['res.partner']
        if hay_fallo and not destinatarios:
            grupo = self.env.ref('foco_monitor.group_foco_manager',
                                 raise_if_not_found=False)
            destinatarios = grupo.users.partner_id if grupo else destinatarios
        if not destinatarios:
            return

        nombre = perfil.name if perfil else 'sin perfil'
        if hay_fallo:
            tipo, titulo = 'danger', 'Foco · bloqueo NO aplicado'
            cuerpo = ('%s no pudo aplicar el bloqueo de navegacion (%s). %s'
                      % (self.display_name, nombre, detalle))
        else:
            tipo, titulo = 'success', 'Foco · bloqueo aplicado'
            cuerpo = ('%s aplico el bloqueo de navegacion: %s.'
                      % (self.display_name, nombre))
        # Toast inmediato por el bus + constancia en el chatter del equipo.
        # Mismo motivo que en las ordenes: el toast no depende de que el usuario
        # tenga la preferencia en 'inbox', y el chatter deja historia.
        for p in destinatarios:
            p._bus_send('simple_notification', {
                'type': tipo, 'title': titulo, 'message': cuerpo, 'sticky': False,
            })
        self.message_post(body=cuerpo, subject=titulo,
                          message_type='comment', subtype_xmlid='mail.mt_note')

    @api.model
    def note_integrity(self, computer, motivo):
        """Registra una anomalia sin tumbar la ingesta: el dato entra igual y
        queda marcado. Rechazar el envio perderia informacion legitima."""
        if not computer:
            return
        computer.sudo().write({'integrity_alert': motivo[:250],
                               'integrity_alert_at': fields.Datetime.now()})
        _logger.warning("Foco: integridad en %s (%s): %s", computer.name,
                        computer.employee_id.name or '-', motivo)

    health = fields.Selection(
        [('ok', 'Reportando'),
         ('stale', 'SIN SEÑAL en horario'),
         ('resting', 'Sin señal (fuera de horario)'),
         ('never', 'Nunca reporto')],
        string='Estado del agente', compute='_compute_health',
        help='Distingue "no trabajo" de "no reporto". Un agente caido muestra '
             'cero horas igual que alguien que no hizo nada: sin esta senal, se '
             'tomarian decisiones sobre datos rotos.')
    minutes_since_seen = fields.Integer(
        string='Minutos sin reportar', compute='_compute_health')

    # --- presencia: donde esta la persona AHORA -----------------------------
    # Es un eje distinto de la salud del agente. Hoy los dos viven en un punto
    # verde o gris, y por eso ese punto grita lo mismo a las 3 de la manana que
    # a media jornada: "desconectado" cubre por igual "apago y se fue a su casa"
    # y "alguien mato el agente". Separarlos es lo que lo vuelve confiable.
    presence_state = fields.Selection(
        [('activo', 'Activo'),
         ('en_llamada', 'En llamada'),
         ('ausente', 'Ausente'),
         ('bloqueado', 'Bloqueado'),
         ('desconocido', 'Desconocido')],
        string='Ultimo estado reportado', readonly=True, default='desconocido')
    presence_since = fields.Datetime(
        string='En ese estado desde', readonly=True)
    presence_idle_secs = fields.Integer(
        string='Segundos sin input', readonly=True,
        help='El dato crudo que acompana al estado. El umbral decide que '
             'palabra se muestra; este numero deja que quien mire no tenga que '
             'confiar en el umbral.')
    presence = fields.Selection(
        [('activo', 'Activo'),
         ('en_llamada', 'En llamada'),
         ('ausente', 'Ausente'),
         ('bloqueado', 'Bloqueado'),
         ('suspendido', 'Suspendido'),
         ('apagado', 'Apagado'),
         ('sin_senal', 'Sin senal'),
         ('nunca', 'Nunca reporto')],
        string='Presencia', compute='_compute_presence',
        help='El estado EFECTIVO. Cuando el equipo deja de reportar, se busca '
             'en sus eventos si hay algo que lo explique: apagado y suspendido '
             'son normalidad, y solo lo que no tiene explicacion queda como '
             '"sin senal".')
    presence_label = fields.Char(
        string='Presencia (con el dato)', compute='_compute_presence')

    def _compute_presence(self):
        ahora = fields.Datetime.now()
        etiquetas = dict(self._fields['presence'].selection)
        Event = self.env['foco.event'].sudo()
        for rec in self:
            if not rec.last_seen:
                rec.presence = 'nunca'
                rec.presence_label = etiquetas['nunca']
                continue
            minutos = max(0, int((ahora - rec.last_seen).total_seconds() // 60))
            if minutos <= HEALTH_OK_MINUTES:
                estado = rec.presence_state or 'activo'
                rec.presence = estado if estado != 'desconocido' else 'activo'
                rec.presence_label = rec._frase_presencia(
                    etiquetas.get(rec.presence, rec.presence), ahora)
                continue
            # Dejo de reportar: que lo explique un evento del equipo, si lo hay.
            ultimo = Event.search(
                [('computer_id', '=', rec.id),
                 ('at', '>=', rec.last_seen),
                 ('kind', 'in', ('apagado', 'apagado_inesperado', 'suspendido',
                                 'agente_fin', 'encendido', 'reanudado'))],
                order='at desc', limit=1)
            if ultimo and ultimo.kind in ('apagado', 'apagado_inesperado'):
                rec.presence = 'apagado'
            elif ultimo and ultimo.kind == 'suspendido':
                rec.presence = 'suspendido'
            else:
                rec.presence = 'sin_senal'
            hora = (fields.Datetime.context_timestamp(rec, ultimo.at).strftime('%H:%M')
                    if ultimo else
                    fields.Datetime.context_timestamp(rec, rec.last_seen).strftime('%H:%M'))
            rec.presence_label = '%s %s' % (etiquetas.get(rec.presence), hora)

    def _frase_presencia(self, palabra, ahora):
        """El estado NUNCA viaja solo: siempre lleva el hecho al lado.

        'Ausente' a secas obliga a confiar en un umbral que eligio el sistema.
        'Ausente - 12 min' deja que quien mire decida por su cuenta.
        """
        self.ensure_one()
        if self.presence in ('ausente', 'en_llamada', 'bloqueado') and self.presence_since:
            minutos = max(0, int((ahora - self.presence_since).total_seconds() // 60))
            if minutos >= 60:
                return '%s %d h %02d min' % (palabra, minutos // 60, minutos % 60)
            return '%s %d min' % (palabra, minutos)
        return palabra

    def _compute_health(self):
        now = fields.Datetime.now()
        settings = self.env['foco.settings'].sudo().get_settings()
        desde = now - timedelta(minutes=HEALTH_OK_MINUTES)
        for rec in self:
            if not rec.last_seen:
                rec.health = 'never'
                rec.minutes_since_seen = 0
                continue
            rec.minutes_since_seen = max(
                0, int((now - rec.last_seen).total_seconds() // 60))
            if rec.minutes_since_seen <= HEALTH_OK_MINUTES:
                rec.health = 'ok'
                continue
            # Silencio a las 3 am es NORMAL; a las 11 am es un problema. Por eso
            # se pregunta al calendario si deberia estar trabajando ahora.
            esperado = settings.expected_seconds(rec.employee_id, desde, now)
            rec.health = 'stale' if esperado > 0 else 'resting'

    @api.model
    def health_summary(self):
        """Estado de los agentes para el tablero y las alertas.

        SIN sudo a proposito: quien consulta Foco con un alcance por
        departamento no debe recibir el estado -ni el nombre del equipo- de
        gente de otras areas. El tablero no los pintaba, pero venian en la
        respuesta, y en un sistema cuyo objeto es controlar quien ve que, eso
        cuenta como filtracion.
        """
        equipos = self.search([('employee_id', '!=', False)])
        out = {}
        for rec in equipos:
            # Un empleado puede tener varios equipos: gana el mas sano.
            eid = str(rec.employee_id.id)
            orden = {'ok': 0, 'resting': 1, 'stale': 2, 'never': 3}
            previo = out.get(eid)
            if previo and orden[previo['health']] <= orden[rec.health]:
                continue
            out[eid] = {'health': rec.health,
                        'minutes': rec.minutes_since_seen,
                        'computer': rec.name,
                        # Presencia y salud son ejes distintos y viajan juntos a
                        # proposito: "apagado 18:14" y "sin senal desde 15:20"
                        # se ven igual en un punto gris, y no son lo mismo.
                        'presence': rec.presence,
                        'presence_label': rec.presence_label or '',
                        'integrity': rec.integrity_alert or ''}
        # La anomalia de integridad NO se pierde aunque ese equipo sea el "mas
        # sano": es evidencia, y ocultarla derrotaria el proposito.
        for rec in equipos.filtered('integrity_alert'):
            eid = str(rec.employee_id.id)
            if eid in out and not out[eid]['integrity']:
                out[eid]['integrity'] = rec.integrity_alert
        return out

    @api.model
    def _cron_check_health(self):
        """Avisa de agentes callados EN HORARIO. Sin esto, un agente caido pasa
        por empleado improductivo."""
        caidos = self.sudo().search([('employee_id', '!=', False)]).filtered(
            lambda c: c.health in ('stale', 'never'))
        if caidos:
            _logger.warning(
                "Foco: %s equipo(s) sin señal en horario: %s", len(caidos),
                ", ".join("%s (%s, %s min)" % (c.name, c.employee_id.name,
                                               c.minutes_since_seen)
                          for c in caidos))
        return len(caidos)

    def _compute_counts(self):
        for rec in self:
            rec.command_count = len(rec.command_ids)
            rec.pending_command_count = len(
                rec.command_ids.filtered(lambda c: c.state == 'pending'))

    def action_reset_key(self):
        for rec in self:
            rec.api_key = secrets.token_urlsafe(24)

    @api.model
    def _authenticate(self, key):
        if not key:
            return self.browse()
        return self.sudo().search(
            [('api_key', '=', key), ('active', '=', True)], limit=1)
