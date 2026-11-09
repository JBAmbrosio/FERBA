from odoo import api, fields, models


class FocoSoporte(models.Model):
    """Una sesion de soporte TI (mantenimiento) sobre un equipo.

    Infraestructura entra por TeamViewer a la sesion del empleado y ACTIVA el
    modo soporte con su contrasena: mientras dure, el equipo NO bloquea sitios y
    NO abre la ventana de inactividad. Infra lo DESACTIVA al terminar; si lo
    olvida, un respaldo lo apaga al cierre de la jornada. Cada tramo
    activo->cerrado queda aqui como registro de mantenimiento (ni cuenta contra
    el empleado ni se marca como evasion). Este modelo es el "apartado Soporte
    TI".
    """
    _name = 'foco.soporte'
    _description = 'Sesion de soporte TI (mantenimiento)'
    _order = 'inicio desc'

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', string='Empleado', store=True)
    company_id = fields.Many2one(
        related='computer_id.employee_id.company_id', string='Empresa', store=True)
    inicio = fields.Datetime(
        string='Inicio', required=True, default=fields.Datetime.now, readonly=True)
    fin = fields.Datetime(string='Fin', readonly=True)
    estado = fields.Selection(
        [('activa', 'Activa'),
         ('cerrada', 'Cerrada por infra'),
         ('cerrada_respaldo', 'Cerrada por respaldo (olvidada)')],
        string='Estado', default='activa', required=True, index=True, readonly=True)
    activada_por = fields.Char(
        string='Activada por', readonly=True,
        help='Como se activo (infra, con la contrasena de soporte).')
    windows_user = fields.Char(string='Sesion de Windows', readonly=True)
    nota = fields.Char(string='Nota')
    duracion_min = fields.Float(
        string='Duracion (min)', compute='_compute_duracion', store=True)
    activa_horas = fields.Float(
        string='Lleva activa (h)', compute='_compute_activa_horas',
        help='Horas que lleva encendida una sesion que sigue ACTIVA. Sirve para '
             'detectar las que infra olvido desactivar.')

    @api.depends('inicio', 'fin')
    def _compute_duracion(self):
        for r in self:
            if r.inicio and r.fin:
                r.duracion_min = (r.fin - r.inicio).total_seconds() / 60.0
            else:
                r.duracion_min = 0.0

    def _compute_activa_horas(self):
        ahora = fields.Datetime.now()
        for r in self:
            if r.estado == 'activa' and r.inicio:
                r.activa_horas = (ahora - r.inicio).total_seconds() / 3600.0
            else:
                r.activa_horas = 0.0

    def action_cerrar(self):
        """Cierra manualmente (desde Odoo) una sesion activa: restituye la
        proteccion del equipo. Util para apagar una que infra olvido, sin
        esperar al respaldo del cierre de jornada."""
        for r in self:
            if r.estado == 'activa':
                r.computer_id.cerrar_soporte(motivo='infra')
        return True
