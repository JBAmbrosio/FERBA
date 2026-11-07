# -*- coding: utf-8 -*-
"""La conversacion de una justificacion, mensaje por mensaje.

Antes de esto el periodo solo conservaba el ULTIMO texto rechazado y la ULTIMA
pregunta de la IA: cada vuelta pisaba a la anterior. Cuando un periodo agotaba
los rebotes y caia en manos del administrador, no habia con que decidir -el caso
real del 12-oct-2026 fueron cinco "almacen" seguidos y un "SURTIDO", de los que
solo sobrevivian los dos ultimos textos y un contador que decia 5-.

Aqui queda cada intervencion en su orden: lo que escribio el empleado, lo que le
contesto la IA, y lo que el administrador le devolvio con sus propias palabras.
Es un registro de lectura: nadie lo edita a mano.
"""
from odoo import fields, models

QUIEN = [
    ('empleado', 'Empleado'),
    ('ia', 'IA'),
    ('admin', 'Administrador'),
]


class FocoAbsenceMsg(models.Model):
    _name = 'foco.absence.msg'
    _description = 'Mensaje de una justificacion'
    # Por id y no por fecha: dos mensajes de la misma vuelta pueden caer en el
    # mismo segundo (el empleado contesta y la IA juzga al instante por el
    # disparo), y entonces la fecha sola no define el orden de la conversacion.
    _order = 'absence_id, id'

    absence_id = fields.Many2one(
        'foco.absence', string='Periodo', required=True,
        ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='absence_id.employee_id', string='Empleado', store=True, index=True)
    vuelta = fields.Integer(
        string='Vuelta', default=0,
        help='Numero de devolucion al que pertenece el mensaje. 0 es el primer '
             'intento, antes de que nadie le devolviera nada.')
    quien = fields.Selection(QUIEN, string='Quien', required=True, index=True)
    texto = fields.Text(string='Texto', required=True)
    at = fields.Datetime(string='Cuando', required=True, default=fields.Datetime.now)

    # ---- contexto segun quien habla (vacio en los demas) -------------------
    reason = fields.Char(
        string='Motivo elegido',
        help='El motivo del catalogo que marco el empleado junto con su texto.')
    via = fields.Char(
        string='Desde', help='Ventana del agente, liga por correo u Odoo.')
    veredicto = fields.Char(
        string='Veredicto', help='Lo que decidio la IA sobre el texto anterior.')
    confianza = fields.Float(string='Confianza', digits=(3, 2))
    user_id = fields.Many2one('res.users', string='Administrador', readonly=True)


class FocoAbsenceDevolver(models.TransientModel):
    """Devolverle el periodo al empleado con el mensaje del administrador.

    Es el tercer camino que faltaba: hoy el admin solo puede Aceptar o Rechazar,
    y cuando la IA no logro sacarle la informacion en sus vueltas, lo unico que
    queda es escribirle uno mismo. El texto viaja a la ventana del agente por el
    mismo canal que la pregunta de la IA (`ai_pregunta`), asi que el .exe no
    necesita ningun cambio.
    """
    _name = 'foco.absence.devolver'
    _description = 'Devolver la justificacion con un mensaje del administrador'

    absence_id = fields.Many2one(
        'foco.absence', string='Periodo', required=True, readonly=True)
    employee_id = fields.Many2one(
        related='absence_id.employee_id', string='Empleado', readonly=True)
    nota_actual = fields.Char(
        related='absence_id.note', string='Lo que escribio', readonly=True)
    mensaje = fields.Text(
        string='Tu mensaje', required=True,
        help='Lo que le va a aparecer en la ventana, con tus palabras. Se '
             'especifico: "dime el numero de pedido que surtiste" saca mas que '
             '"se mas claro".')

    def action_enviar(self):
        self.ensure_one()
        texto = (self.mensaje or '').strip()
        if not texto:
            return False
        self.absence_id.devolver_con_mensaje(texto)
        return {'type': 'ir.actions.act_window_closed'}


class FocoAbsenceDecision(models.TransientModel):
    """Aprobar o rechazar, con una nota, lo que alguien SIN ventana no pudo
    justificar (14-oct-2026). Sirve para uno o para varios periodos a la vez:
    se elige en la lista y se decide de una vez."""
    _name = 'foco.absence.decision'
    _description = 'Aprobar o rechazar periodos de quien no tiene ventana'

    absence_ids = fields.Many2many(
        'foco.absence', string='Periodos', required=True, readonly=True)
    decision = fields.Selection(
        [('aprobar', 'Aprobar'), ('rechazar', 'Rechazar')],
        string='Decision', required=True, readonly=True)
    nota = fields.Text(
        string='Nota',
        help='Por que lo apruebas o lo rechazas. Queda en el periodo y en su '
             'conversacion, para quien lo revise despues.')
    resumen = fields.Char(string='Periodos', compute='_compute_resumen')

    def _compute_resumen(self):
        for w in self:
            # Con sudo: solo son nombres y minutos que quien decide ya ve en su
            # lista; sin el, a un usuario que no es de RR.HH. en Odoo leer el
            # empleado le truena por los campos privados de hr.employee.
            recs = w.absence_ids.sudo()
            n = len(recs)
            minutos = int(round(sum(recs.mapped('expected_seconds')) / 60.0))
            gente = ', '.join(sorted(set(recs.mapped('employee_id.name'))))
            w.resumen = '%d %s · %d min de jornada · %s' % (
                n, 'periodo' if n == 1 else 'periodos', minutos, gente)

    def action_confirmar(self):
        self.ensure_one()
        self.absence_ids.decidir_sin_ventana(self.decision, self.nota)
        return {'type': 'ir.actions.act_window_close'}
