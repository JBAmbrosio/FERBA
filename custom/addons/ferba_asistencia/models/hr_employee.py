# -*- coding: utf-8 -*-
"""Marca que empleados deben checar (entran en la lista de asistencia)."""

from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    ferba_controla_asistencia = fields.Boolean(
        string='Controla asistencia (checador)', default=True, index=True,
        help='Si esta activo, el empleado debe marcar entrada/salida y entra en '
             'la lista de asistencia del periodo (un dia laborable sin marca = '
             'ausente). Apagalo para quien no checa: direccion, sistemas, etc.')
