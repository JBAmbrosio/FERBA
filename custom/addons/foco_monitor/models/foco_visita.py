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

import base64
import json
from math import radians, sin, cos, asin, sqrt

from odoo import api, fields, models

# Rubrica del "gerente senior": evalua al VENDEDOR con venta consultiva (el
# playbook de FERBA: preparacion, descubrimiento de cultivo/barreras/estatus,
# propuesta de valor, manejo de objeciones y cierre con proximo paso).
SISTEMA_VISITA = (
    "Eres un GERENTE DE VENTAS SENIOR de FERBA, empresa del noroeste de Mexico "
    "que vende insumos y material de empaque a empresas agricolas. Recibes la "
    "TRANSCRIPCION de una visita de uno de tus vendedores a un cliente. Evalua el "
    "desempeno del VENDEDOR con criterios de venta consultiva: preparacion, "
    "descubrimiento de necesidades (cultivo, volumen, barreras de compra, estatus), "
    "propuesta de valor, manejo de objeciones y cierre con un proximo paso claro. "
    "Da fortalezas, debilidades y mejoras CONCRETAS y accionables (nada generico: "
    "cita lo que paso en la conversacion), y un puntaje de 0 a 100. Habla directo y "
    "util, como un coach que quiere que el vendedor venda mas. No incluyas datos "
    "personales sensibles ni nombres de personas en los textos."
)

ESQUEMA_VISITA = {
    'name': 'analisis_visita', 'strict': True,
    'schema': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'resumen': {'type': 'string'},
            'fortalezas': {'type': 'array', 'items': {'type': 'string'}},
            'debilidades': {'type': 'array', 'items': {'type': 'string'}},
            'mejoras': {'type': 'array', 'items': {'type': 'string'}},
            'puntaje': {'type': 'integer'},
        },
        'required': ['resumen', 'fortalezas', 'debilidades', 'mejoras', 'puntaje'],
    },
}

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

    # El vendedor le leyo el aviso al cliente y este acepto que se grabe. Sin
    # esto, el telefono NO graba: la grabacion es transparente y consentida.
    grabacion_consentida = fields.Boolean(string='Grabacion consentida por el cliente')

    # --- Grabacion de la visita + coaching de IA (Fase 4) ---
    audio = fields.Binary(string='Audio de la visita', attachment=True)
    audio_name = fields.Char(string='Archivo de audio')
    audio_duracion_s = fields.Integer(string='Duracion del audio (s)')
    audio_estado = fields.Selection([
        ('sin_audio', 'Sin audio'),
        ('pendiente', 'Pendiente de analizar'),
        ('procesando', 'Analizando'),
        ('listo', 'Analizada'),
        ('error', 'Error'),
    ], string='Audio', default='sin_audio', index=True)
    transcripcion = fields.Text(string='Transcripcion')
    analisis_resumen = fields.Text(string='Resumen de la IA')
    analisis_fortalezas = fields.Text(string='Fortalezas')
    analisis_debilidades = fields.Text(string='Debilidades')
    analisis_mejoras = fields.Text(string='Mejoras')
    analisis_puntaje = fields.Integer(string='Puntaje (0-100)')
    analizado_el = fields.Datetime(string='Analizada el')

    def action_analizar(self):
        self._analizar_audio()

    def _analizar_audio(self):
        """Transcribe el audio de la visita y lo evalua como gerente senior.
        Guarda fortalezas/debilidades/mejoras y un puntaje. Blindado por visita:
        si una truena, las demas siguen."""
        OpenAI = self.env['foco.openai']
        if not OpenAI.configurado():
            return
        estatus_lbl = dict(self.env['res.partner']._fields['foco_estatus'].selection)
        for v in self:
            if not v.audio:
                continue
            v.audio_estado = 'procesando'
            try:
                raw = base64.b64decode(v.audio)
                texto = OpenAI.transcribir(
                    raw, nombre=v.audio_name or 'visita.m4a', idioma='es', mime='audio/mp4')
                v.transcripcion = texto or ''
                if not (texto or '').strip():
                    v.audio_estado = 'listo'
                    v.analizado_el = fields.Datetime.now()
                    continue
                p = v.partner_id
                ctx = ('Cliente: %s. Cultivo: %s. Zona: %s. Estatus: %s.'
                       % (p.display_name, p.foco_cultivo or '-', p.foco_zona or '-',
                          estatus_lbl.get(p.foco_estatus, '-')))
                resp = OpenAI.chat(
                    [{'role': 'system', 'content': SISTEMA_VISITA},
                     {'role': 'user', 'content': ctx + '\n\nTRANSCRIPCION DE LA VISITA:\n' + texto[:12000]}],
                    response_format={'type': 'json_schema', 'json_schema': ESQUEMA_VISITA},
                    max_tokens=1300)
                data = json.loads(resp['message'].get('content') or '{}')
                v.analisis_resumen = (data.get('resumen') or '').strip()
                v.analisis_fortalezas = '\n'.join('- ' + x for x in (data.get('fortalezas') or []))
                v.analisis_debilidades = '\n'.join('- ' + x for x in (data.get('debilidades') or []))
                v.analisis_mejoras = '\n'.join('- ' + x for x in (data.get('mejoras') or []))
                v.analisis_puntaje = int(data.get('puntaje') or 0)
                v.analizado_el = fields.Datetime.now()
                v.audio_estado = 'listo'
            except Exception as e:
                v.audio_estado = 'error'
                try:
                    v.message_post(body='No se pudo analizar la visita: %s' % e)
                except Exception:
                    pass

    @api.model
    def _cron_analizar_visitas(self):
        """Procesa las visitas con audio pendiente (lo encola el telefono)."""
        pend = self.search([('audio_estado', '=', 'pendiente'), ('audio', '!=', False)], limit=10)
        for v in pend:
            v._analizar_audio()

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
