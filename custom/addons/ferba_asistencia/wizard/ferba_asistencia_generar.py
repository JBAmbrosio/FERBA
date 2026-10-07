# -*- coding: utf-8 -*-
"""Asistente para (re)generar la lista de asistencia de un periodo."""

from datetime import date

from odoo import api, fields, models


class FerbaAsistenciaGenerar(models.TransientModel):
    _name = 'ferba.asistencia.generar'
    _description = 'Generar lista de asistencia por periodo'

    fecha_inicio = fields.Date(string='Desde', required=True,
                               default=lambda s: date.today().replace(day=1))
    fecha_fin = fields.Date(string='Hasta', required=True, default=fields.Date.context_today)
    todos = fields.Boolean(string='Todos los empleados activos', default=True)
    employee_ids = fields.Many2many('hr.employee', string='Empleados')

    @api.onchange('todos')
    def _onchange_todos(self):
        if self.todos:
            self.employee_ids = False

    def action_generar(self):
        self.ensure_one()
        emp_ids = False if self.todos else self.employee_ids.ids
        self.env['ferba.asistencia.dia'].generar_periodo(
            self.fecha_inicio, self.fecha_fin, emp_ids)
        # Abre la lista del periodo recien generado, agrupada por estado.
        accion = self.env['ir.actions.act_window']._for_xml_id(
            'ferba_asistencia.action_ferba_asistencia_dia')
        accion['domain'] = [('fecha', '>=', self.fecha_inicio),
                            ('fecha', '<=', self.fecha_fin)]
        accion['context'] = {'search_default_g_estado': 1}
        return accion
