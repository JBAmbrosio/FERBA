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

    # Resumen del periodo (se calcula de ferba.asistencia.dia, sin almacenar).
    resumen_laborables = fields.Integer(string='Dias laborables',
                                        compute='_compute_resumen')
    resumen_ausencias = fields.Integer(string='Ausencias', compute='_compute_resumen')
    resumen_incompletos = fields.Integer(string='Incompletos', compute='_compute_resumen')
    resumen_cumplimiento = fields.Float(string='Cumplimiento %',
                                        compute='_compute_resumen')

    def _dias_del_periodo(self):
        self.ensure_one()
        return self.env['ferba.asistencia.dia'].search([
            ('fecha', '>=', self.fecha_inicio), ('fecha', '<=', self.fecha_fin)])

    def _compute_resumen(self):
        for p in self:
            dias = p._dias_del_periodo() if (p.fecha_inicio and p.fecha_fin) \
                else self.env['ferba.asistencia.dia']
            lab = aus = inc = 0
            trab = esp = 0.0
            for d in dias:
                if d.estado not in ('descanso', 'festivo'):
                    lab += 1
                if d.estado == 'ausente':
                    aus += 1
                elif d.estado == 'incompleto':
                    inc += 1
                trab += d.horas_trabajadas or 0.0
                esp += d.horas_esperadas or 0.0
            p.resumen_laborables = lab
            p.resumen_ausencias = aus
            p.resumen_incompletos = inc
            p.resumen_cumplimiento = round((trab / esp) * 100, 1) if esp else 0.0

    def _resumen_empleados(self):
        """Filas del reporte de cierre: un renglon por empleado con sus totales."""
        self.ensure_one()
        por_emp = {}
        for d in self._dias_del_periodo():
            v = por_emp.setdefault(d.employee_id.id, {
                'empleado': d.employee_id, 'laborables': 0, 'trabajados': 0,
                'ausentes': 0, 'incompletos': 0, 'cortas': 0, 'permisos': 0,
                'h_trab': 0.0, 'h_esp': 0.0})
            if d.estado not in ('descanso', 'festivo'):
                v['laborables'] += 1
            if d.estado == 'presente':
                v['trabajados'] += 1
            elif d.estado == 'corta':
                v['trabajados'] += 1
                v['cortas'] += 1
            elif d.estado == 'ausente':
                v['ausentes'] += 1
            elif d.estado == 'incompleto':
                v['incompletos'] += 1
            elif d.estado == 'permiso':
                v['permisos'] += 1
            v['h_trab'] += d.horas_trabajadas or 0.0
            v['h_esp'] += d.horas_esperadas or 0.0
        filas = list(por_emp.values())
        for v in filas:
            v['extra'] = max(0.0, v['h_trab'] - v['h_esp'])
        filas.sort(key=lambda r: (r['empleado'].name or '').lower())
        return filas

    def action_generar_asistencia(self):
        """Reconstruye la lista de asistencia del rango del periodo."""
        self.ensure_one()
        self.env['ferba.asistencia.dia'].generar_periodo(
            self.fecha_inicio, self.fecha_fin)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Asistencia generada',
                'message': 'Se reconstruyo la lista del periodo.',
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_ver_work_entries(self):
        """Puente a nomina: entradas de trabajo nativas del rango del periodo."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Entradas de trabajo del periodo',
            'res_model': 'hr.work.entry',
            'view_mode': 'list,form',
            'domain': [('date', '>=', self.fecha_inicio), ('date', '<=', self.fecha_fin)],
        }

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
