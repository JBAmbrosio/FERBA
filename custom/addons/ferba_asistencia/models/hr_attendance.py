# -*- coding: utf-8 -*-
"""Control de asistencia para nomina.

Marca las asistencias que NECESITAN REVISION (una marca incompleta o con horas
imposibles = seguro falto una entrada/salida), con el motivo, para que RH las
corrija en linea y las valide. No toca la nomina: solo agrega el control de
calidad del dato encima del modulo nativo de Asistencias.
"""

from odoo import api, fields, models

# Jornada mas larga que esto = casi seguro falto una marca (no un turno real).
UMBRAL_HORAS = 12.0


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    necesita_revision = fields.Boolean(
        string='Necesita revision', compute='_compute_revision', store=True, index=True,
        help='La marca esta incompleta o con horas imposibles: hay que corregirla.')
    # tipo = categoria limpia para AGRUPAR y FILTRAR (sin el numero de horas).
    tipo_revision = fields.Selection(
        selection=[
            ('sin_salida', 'Sin salida'),
            ('jornada_larga', 'Jornada muy larga'),
            ('sin_horas', 'Sin horas'),
        ],
        string='Tipo de revision', compute='_compute_revision', store=True, index=True)
    # motivo = texto para leer en la fila, con el numero de horas cuando aplica.
    motivo_revision = fields.Char(
        string='Motivo de revision', compute='_compute_revision', store=True)
    validada = fields.Boolean(
        string='Validada', default=False, index=True, copy=False,
        help='RH reviso esta asistencia y queda lista para la nomina.')
    validada_por = fields.Many2one('res.users', string='Validada por',
                                   readonly=True, copy=False)
    validada_el = fields.Datetime(string='Validada el', readonly=True, copy=False)

    @api.depends('check_in', 'check_out', 'worked_hours')
    def _compute_revision(self):
        for a in self:
            tipo = False
            motivo = ''
            if a.check_in and not a.check_out:
                tipo, motivo = 'sin_salida', 'Sin salida'
            elif a.check_out:
                wh = a.worked_hours or 0.0
                if wh > UMBRAL_HORAS:
                    tipo = 'jornada_larga'
                    motivo = 'Jornada muy larga (%.1f h)' % wh
                elif wh <= 0.0:
                    tipo, motivo = 'sin_horas', 'Sin horas'
            a.tipo_revision = tipo
            a.motivo_revision = motivo
            a.necesita_revision = bool(tipo)

    def action_validar(self):
        """Marca como validadas las asistencias que YA estan bien. Las que aun
        necesitan correccion se saltan (primero se corrigen)."""
        listas = self.filtered(lambda a: not a.necesita_revision and not a.validada)
        listas.write({
            'validada': True,
            'validada_por': self.env.user.id,
            'validada_el': fields.Datetime.now(),
        })

    def action_desvalidar(self):
        self.write({'validada': False, 'validada_por': False, 'validada_el': False})
