# -*- coding: utf-8 -*-
"""Visita de un vendedor a un cliente (check-in en sitio).

Es el registro que en la Fase 2 crea el boton "Llegue" del telefono: manda las
coordenadas y la hora (o las guarda y reintenta cuando no hay internet). Aqui
vive el modelo, el calculo de la distancia al pin del cliente -la señal
"estuvo / no estuvo"- y queda listo para que el telefono lo alimente.

La verificacion de sitio NO BLOQUEA: un empaque agricola es grande, el GPS trae
error y un prospecto nuevo todavia no tiene pin (su check-in ES el que lo crea).
Se marca la distancia y ya; dirección decide que hacer con las que salen lejos.
"""

from math import radians, sin, cos, asin, sqrt

from odoo import api, fields, models

# Dentro de este radio la visita se da por "en sitio".
RADIO_EN_SITIO_M = 300.0


def _haversine_m(lat1, lon1, lat2, lon2):
    """Metros entre dos coordenadas (formula del haversine)."""
    r = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (sin(dlat / 2) ** 2
         + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2)
    return 2 * r * asin(sqrt(a))


class FocoVisita(models.Model):
    _name = 'foco.visita'
    _description = 'Visita de un vendedor a un cliente'
    _inherit = ['mail.thread']
    _order = 'check_in desc'

    name = fields.Char(string='Visita', compute='_compute_name', store=True)
    # El vendedor puede estar vacio: desde el telefono el actor seguro es el
    # EMPLEADO del equipo; su usuario Odoo (user_id) solo se llena si existe.
    user_id = fields.Many2one('res.users', string='Vendedor',
                              default=lambda s: s.env.user, index=True,
                              tracking=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado', index=True)
    device_id = fields.Many2one('foco.mobile.device', string='Telefono',
                                ondelete='set null')
    device_uuid = fields.Char(string='UUID del telefono', index=True, copy=False,
                              help='Id que genera el telefono para no duplicar '
                                   'una visita al reenviarla sin internet.')
    partner_id = fields.Many2one('res.partner', string='Cliente', required=True,
                                 index=True, tracking=True,
                                 domain=[('foco_cliente_ventas', '=', True)])
    lead_id = fields.Many2one('crm.lead', string='Oportunidad',
                              domain="[('partner_id', '=', partner_id)]")
    check_in = fields.Datetime(string='Hora de llegada', required=True,
                               default=fields.Datetime.now, index=True,
                               tracking=True)
    latitude = fields.Float(string='Latitud', digits=(10, 7))
    longitude = fields.Float(string='Longitud', digits=(10, 7))
    precision_m = fields.Float(string='Precision (m)')
    origen = fields.Selection([
        ('gps', 'GPS del telefono'),
        ('manual', 'Capturada a mano'),
    ], string='Origen', default='manual')
    distancia_m = fields.Float(string='Distancia al cliente (m)',
                               compute='_compute_distancia', store=True)
    en_sitio = fields.Selection([
        ('si', 'En sitio'),
        ('lejos', 'Lejos del cliente'),
        ('sin_pin', 'Cliente sin ubicacion'),
    ], string='Verificacion', compute='_compute_distancia', store=True)
    resultado = fields.Selection([
        ('interesado', 'Interesado'),
        ('cotizar', 'Pidio cotizacion'),
        ('cerrado', 'Venta cerrada'),
        ('seguimiento', 'Requiere seguimiento'),
        ('no', 'No interesado'),
    ], string='Resultado', tracking=True)
    nota = fields.Text(string='Nota')
    proxima_fecha = fields.Date(string='Proximo seguimiento')

    @api.depends('partner_id', 'check_in')
    def _compute_name(self):
        for v in self:
            if v.partner_id and v.check_in:
                v.name = '%s · %s' % (
                    v.partner_id.display_name,
                    fields.Datetime.to_string(v.check_in))
            else:
                v.name = v.partner_id.display_name or 'Visita'

    @api.depends('latitude', 'longitude',
                 'partner_id.partner_latitude', 'partner_id.partner_longitude')
    def _compute_distancia(self):
        for v in self:
            plat = v.partner_id.partner_latitude
            plon = v.partner_id.partner_longitude
            if not (plat or plon) or not (v.latitude or v.longitude):
                v.distancia_m = 0.0
                v.en_sitio = 'sin_pin'
            else:
                d = _haversine_m(v.latitude, v.longitude, plat, plon)
                v.distancia_m = round(d, 1)
                v.en_sitio = 'si' if d <= RADIO_EN_SITIO_M else 'lejos'
