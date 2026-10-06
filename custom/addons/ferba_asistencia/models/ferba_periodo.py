# -*- coding: utf-8 -*-
"""Periodo de nomina con candado.

Cuando RH termina de revisar y validar un periodo, lo CIERRA. A partir de ahi
las marcas (hr.attendance) de ese rango quedan congeladas: nadie las puede
cambiar, crear ni borrar, para que lo que se llevo a nomina no se altere luego.
El candado se salta con contexto 'bypass_periodo_lock' (procesos del sistema).
"""

from odoo import api, fields, models
from odoo.exceptions import UserError


class FerbaPeriodo(models.Model):
    _name = 'ferba.periodo'
    _description = 'Periodo de nomina (candado de asistencia)'
    _order = 'fecha_inicio desc'

    name = fields.Char(string='Nombre', required=True)
    fecha_inicio = fields.Date(string='Desde', required=True)
    fecha_fin = fields.Date(string='Hasta', required=True)
    company_id = fields.Many2one('res.company', string='Empresa',
                                 help='Vacio = aplica a todas las empresas.')
    estado = fields.Selection([('abierto', 'Abierto'), ('cerrado', 'Cerrado')],
                              string='Estado', default='abierto', required=True, index=True)
    cerrado_por = fields.Many2one('res.users', string='Cerrado por', readonly=True)
    cerrado_el = fields.Datetime(string='Cerrado el', readonly=True)
    nota = fields.Text(string='Nota')

    @api.constrains('fecha_inicio', 'fecha_fin')
    def _check_rango(self):
        for p in self:
            if p.fecha_fin < p.fecha_inicio:
                raise UserError('El "Hasta" no puede ser anterior al "Desde".')

    def action_cerrar(self):
        self.write({
            'estado': 'cerrado',
            'cerrado_por': self.env.user.id,
            'cerrado_el': fields.Datetime.now(),
        })

    def action_reabrir(self):
        self.write({'estado': 'abierto', 'cerrado_por': False, 'cerrado_el': False})

    def action_ver_asistencia(self):
        """Abre la lista de asistencia del periodo (ferba.asistencia.dia)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Asistencia del periodo',
            'res_model': 'ferba.asistencia.dia',
            'view_mode': 'list,pivot',
            'domain': [('fecha', '>=', self.fecha_inicio), ('fecha', '<=', self.fecha_fin)],
            'context': {'search_default_g_estado': 1},
        }

    @api.model
    def periodos_que_cubren(self, fecha, company=None):
        """Periodos CERRADOS que cubren esa fecha (y empresa, si aplica)."""
        dom = [('estado', '=', 'cerrado'),
               ('fecha_inicio', '<=', fecha), ('fecha_fin', '>=', fecha)]
        periodos = self.search(dom)
        if company is not None:
            periodos = periodos.filtered(
                lambda p: not p.company_id or p.company_id.id == company.id)
        return periodos
