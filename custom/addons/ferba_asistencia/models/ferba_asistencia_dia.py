# -*- coding: utf-8 -*-
"""Lista de asistencia por periodo (una fila por empleado y dia).

El checador NO crea fila cuando alguien no marca, asi que un dia faltante es
invisible en hr.attendance. Este modelo reconstruye el periodo COMPLETO: por
cada empleado y cada dia cruza su HORARIO (horas esperadas) contra sus MARCAS
(horas trabajadas) y sus PERMISOS/FESTIVOS, y clasifica el dia. Asi RH ve de un
vistazo quien NO marco (ausente) y quien marco a medias (incompleto), que es lo
que falta para una lista de nomina correcta.

Se llena con el asistente "Generar periodo" (o se puede automatizar). Las filas
se reescriben por (empleado, dia): regenerar no duplica.
"""

from datetime import datetime, timedelta

import pytz

from odoo import api, fields, models

# Trabajo menos que esta fraccion de lo esperado (y marco algo) = jornada corta.
FRACCION_CORTA = 0.75


class FerbaAsistenciaDia(models.Model):
    _name = 'ferba.asistencia.dia'
    _description = 'Asistencia por dia (lista de periodo)'
    _order = 'fecha desc, employee_id'
    _rec_name = 'employee_id'

    fecha = fields.Date(string='Dia', required=True, index=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado',
                                  required=True, index=True, ondelete='cascade')
    department_id = fields.Many2one('hr.department', string='Departamento',
                                    related='employee_id.department_id', store=True)
    company_id = fields.Many2one('res.company', string='Empresa',
                                 related='employee_id.company_id', store=True)

    horas_esperadas = fields.Float(string='Esperadas', digits=(16, 2))
    horas_trabajadas = fields.Float(string='Trabajadas', digits=(16, 2))
    horas_permiso = fields.Float(string='Permiso', digits=(16, 2))
    num_marcas = fields.Integer(string='Marcas')
    abierta = fields.Boolean(string='Marca abierta',
                             help='Alguna marca del dia quedo sin salida.')
    diferencia = fields.Float(string='Diferencia', digits=(16, 2),
                              compute='_compute_diferencia', store=True,
                              help='Trabajadas + permiso - esperadas.')

    estado = fields.Selection(
        selection=[
            ('presente', 'Presente'),
            ('corta', 'Jornada corta'),
            ('incompleto', 'Incompleto (sin salida)'),
            ('ausente', 'Ausente (no marco)'),
            ('permiso', 'Permiso'),
            ('festivo', 'Festivo'),
            ('descanso', 'Descanso'),
        ],
        string='Estado', index=True)

    _sql_constraints = [
        ('empleado_dia_unico', 'unique(employee_id, fecha)',
         'Ya existe el renglon de ese empleado en ese dia.'),
    ]

    @api.depends('horas_trabajadas', 'horas_permiso', 'horas_esperadas')
    def _compute_diferencia(self):
        for r in self:
            r.diferencia = (r.horas_trabajadas or 0.0) + (r.horas_permiso or 0.0) \
                - (r.horas_esperadas or 0.0)

    def action_ver_marcas(self):
        """Abre las marcas (hr.attendance) de ese empleado y dia."""
        self.ensure_one()
        tz = pytz.timezone(self.employee_id.tz or 'America/Mazatlan')
        ini = tz.localize(datetime.combine(self.fecha, datetime.min.time()))
        fin = ini + timedelta(days=1)
        ini_utc = ini.astimezone(pytz.UTC).replace(tzinfo=None)
        fin_utc = fin.astimezone(pytz.UTC).replace(tzinfo=None)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Marcas del dia',
            'res_model': 'hr.attendance',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.employee_id.id),
                       ('check_in', '>=', fields.Datetime.to_string(ini_utc)),
                       ('check_in', '<', fields.Datetime.to_string(fin_utc))],
            'context': {'default_employee_id': self.employee_id.id},
        }

    # ------------------------------------------------------------------
    # Generacion del periodo
    # ------------------------------------------------------------------
    @api.model
    def generar_periodo(self, fecha_inicio, fecha_fin, employee_ids=None):
        """Reconstruye las filas (empleado, dia) del rango [inicio, fin].

        Devuelve cuantas filas quedaron. Es idempotente: reescribe por llave.
        """
        fecha_inicio = fields.Date.to_date(fecha_inicio)
        fecha_fin = fields.Date.to_date(fecha_fin)
        if fecha_fin < fecha_inicio:
            fecha_inicio, fecha_fin = fecha_fin, fecha_inicio

        empleados = self.env['hr.employee'].browse(employee_ids) if employee_ids \
            else self.env['hr.employee'].search([
                ('active', '=', True), ('ferba_controla_asistencia', '=', True)])
        empleados = empleados.filtered('resource_calendar_id')
        if not empleados:
            return 0

        dias = []
        d = fecha_inicio
        while d <= fecha_fin:
            dias.append(d)
            d += timedelta(days=1)

        # --- Marcas del rango, agrupadas por (empleado, dia local) ---
        pad_ini = datetime.combine(fecha_inicio, datetime.min.time()) - timedelta(days=1)
        pad_fin = datetime.combine(fecha_fin, datetime.min.time()) + timedelta(days=2)
        marcas = self.env['hr.attendance'].search([
            ('employee_id', 'in', empleados.ids),
            ('check_in', '>=', fields.Datetime.to_string(pad_ini)),
            ('check_in', '<', fields.Datetime.to_string(pad_fin)),
        ])
        por_emp_dia = {}  # (emp_id, date) -> {'horas':.., 'n':.., 'abierta':bool}
        for m in marcas:
            emp = m.employee_id
            tz = pytz.timezone(emp.tz or 'America/Mazatlan')
            local = pytz.UTC.localize(m.check_in).astimezone(tz)
            clave = (emp.id, local.date())
            agg = por_emp_dia.setdefault(clave, {'horas': 0.0, 'n': 0, 'abierta': False})
            agg['horas'] += m.worked_hours or 0.0
            agg['n'] += 1
            if not m.check_out:
                agg['abierta'] = True

        # --- Permisos aprobados del rango, por (empleado, dia) ---
        permiso_dia = set()  # (emp_id, date) con permiso aprobado que cubre el dia
        Leave = self.env.get('hr.leave')
        if Leave is not None:
            permisos = Leave.search([
                ('employee_id', 'in', empleados.ids),
                ('state', '=', 'validate'),
                ('date_from', '<=', fields.Datetime.to_string(
                    datetime.combine(fecha_fin, datetime.max.time()))),
                ('date_to', '>=', fields.Datetime.to_string(
                    datetime.combine(fecha_inicio, datetime.min.time()))),
            ])
            for lv in permisos:
                emp = lv.employee_id
                tz = pytz.timezone(emp.tz or 'America/Mazatlan')
                ini = pytz.UTC.localize(lv.date_from).astimezone(tz).date()
                fin = pytz.UTC.localize(lv.date_to).astimezone(tz).date()
                dd = max(ini, fecha_inicio)
                while dd <= min(fin, fecha_fin):
                    permiso_dia.add((emp.id, dd))
                    dd += timedelta(days=1)

        # --- Horas esperadas por (calendario, dia), cacheadas ---
        cache = {}

        def esperadas_de(cal, dia):
            clave = (cal.id, dia)
            if clave in cache:
                return cache[clave]
            tz = pytz.timezone(cal.tz or 'America/Mazatlan')
            ini = tz.localize(datetime.combine(dia, datetime.min.time()))
            fin = ini + timedelta(days=1)
            base = cal.get_work_hours_count(ini, fin, compute_leaves=False)
            real = cal.get_work_hours_count(ini, fin, compute_leaves=True)
            cache[clave] = (base, real)
            return base, real

        # --- Arma/filtra las filas existentes para reescribir ---
        existentes = {}
        for r in self.search([('employee_id', 'in', empleados.ids),
                              ('fecha', '>=', fecha_inicio),
                              ('fecha', '<=', fecha_fin)]):
            existentes[(r.employee_id.id, r.fecha)] = r

        total = 0
        for emp in empleados:
            cal = emp.resource_calendar_id
            for dia in dias:
                base, esperadas = esperadas_de(cal, dia)
                agg = por_emp_dia.get((emp.id, dia))
                trabajadas = agg['horas'] if agg else 0.0
                n = agg['n'] if agg else 0
                abierta = agg['abierta'] if agg else False
                tiene_permiso = (emp.id, dia) in permiso_dia

                if base <= 0.0:
                    estado = 'descanso'
                    permiso_h = 0.0
                elif esperadas <= 0.0:
                    estado = 'festivo'
                    permiso_h = 0.0
                elif tiene_permiso and trabajadas <= 0.0:
                    estado = 'permiso'
                    permiso_h = esperadas
                elif abierta:
                    estado = 'incompleto'
                    permiso_h = esperadas if tiene_permiso else 0.0
                elif trabajadas <= 0.0:
                    estado = 'ausente'
                    permiso_h = 0.0
                elif trabajadas < esperadas * FRACCION_CORTA:
                    estado = 'corta'
                    permiso_h = esperadas if tiene_permiso else 0.0
                else:
                    estado = 'presente'
                    permiso_h = 0.0

                vals = {
                    'fecha': dia,
                    'employee_id': emp.id,
                    'horas_esperadas': esperadas,
                    'horas_trabajadas': trabajadas,
                    'horas_permiso': permiso_h,
                    'num_marcas': n,
                    'abierta': abierta,
                    'estado': estado,
                }
                reg = existentes.get((emp.id, dia))
                if reg:
                    reg.write(vals)
                else:
                    self.create(vals)
                total += 1
        return total
