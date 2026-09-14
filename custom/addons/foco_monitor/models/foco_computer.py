import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# El agente empuja cada 5 min. 15 min de silencio = 3 envios perdidos: ya no es
# un tropiezo de red, es un agente caido.
HEALTH_OK_MINUTES = 15


class FocoComputer(models.Model):
    _name = 'foco.computer'
    _description = 'Computadora monitoreada'
    _order = 'last_seen desc'

    _api_key_uniq = models.Constraint('unique(api_key)',
                                      'La API key debe ser unica.')

    name = fields.Char(string='Equipo (hostname)', required=True)
    windows_user = fields.Char(string='Usuario de Windows')
    employee_id = fields.Many2one('hr.employee', string='Empleado')
    department_id = fields.Many2one(
        related='employee_id.department_id', store=True, string='Departamento')
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

    integrity_alert = fields.Char(
        string='Anomalia de integridad', readonly=True,
        help='Ultima inconsistencia detectada al recibir datos de este equipo '
             '(totales imposibles o reloj desfasado). Es un INDICIO para revisar '
             'con la persona, nunca una acusacion automatica.')
    integrity_alert_at = fields.Datetime(string='Detectada', readonly=True)

    def action_clear_integrity_alert(self):
        self.write({'integrity_alert': False, 'integrity_alert_at': False})

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
        """Estado de los agentes para el tablero y las alertas."""
        equipos = self.sudo().search([('employee_id', '!=', False)])
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
