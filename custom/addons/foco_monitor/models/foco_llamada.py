# -*- coding: utf-8 -*-
"""Llamada de WhatsApp de un vendedor, con el MISMO coaching que las visitas.

El telefono (LlamadaWhatsApp) graba el lado del VENDEDOR de una llamada de
WhatsApp (sin altavoz Android silencia el micro normal para una app de fuera,
pero por VOICE_RECOGNITION si entra la voz del vendedor de cerca) y sube el WAV
aqui al colgar. Se transcribe, se reparte en turnos para leerla como chat, y se
evalua al vendedor con la MISMA rubrica de gerente senior de las visitas
(SISTEMA_VISITA): resumen, fortalezas, debilidades, mejoras y un puntaje 0-100.

El coaching se ve en Odoo (pestana Conversacion + pestana Coaching) y se le
REGRESA al telefono para que el vendedor lo lea (mis_llamadas_coaching). Como
solo se capta al vendedor, la parte del cliente se infiere por sus respuestas;
se le dice a la IA para que no invente.

Grabacion CON aviso (queda en el aviso de privacidad que firma el vendedor),
nunca a escondidas.
"""

import base64
import json

from markupsafe import Markup, escape

from odoo import api, fields, models

# Se REUSA tal cual el coaching de las visitas: misma rubrica y mismo esquema.
from .foco_visita import (
    SISTEMA_VISITA, ESQUEMA_VISITA, SISTEMA_DIALOGO, ESQUEMA_DIALOGO,
)

# Contexto que se antepone a la transcripcion para que la IA sepa que es una
# llamada (no una visita en sitio) y que solo se escucha al vendedor.
NOTA_LLAMADA = (
    "CONTEXTO: esto es una LLAMADA TELEFONICA (por WhatsApp o celular), no una "
    "visita en sitio. Solo se capto el audio del VENDEDOR (su microfono); la parte del "
    "CLIENTE no se grabo, asi que se infiere por las respuestas del vendedor. "
    "No inventes lo que dijo el cliente: deducelo de lo que el vendedor "
    "contesta o pregunta."
)


class FocoLlamada(models.Model):
    _name = 'foco.llamada'
    _description = 'Llamada de WhatsApp de un vendedor (coaching)'
    _inherit = ['mail.thread']
    _order = 'started_at desc, id desc'

    _uniq = models.Constraint('unique(device_id, uuid)',
                              'Esa llamada ya estaba registrada.')

    name = fields.Char(string='Llamada', compute='_compute_name', store=True)
    device_id = fields.Many2one('foco.mobile.device', string='Telefono',
                                required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='device_id.employee_id', store=True,
                                  string='Vendedor', index=True)
    department_id = fields.Many2one(related='employee_id.department_id', store=True,
                                    string='Departamento')
    # El id que el telefono le pone al archivo (wa_<epoch>): dedup al reenviar.
    uuid = fields.Char(string='UUID del telefono', index=True, copy=False,
                       help='Id del WAV que genera el telefono; evita duplicar '
                            'la llamada si la reenvia sin internet.')
    started_at = fields.Datetime(string='Inicio', index=True)
    duration_s = fields.Integer(string='Duracion (s)')
    duration_text = fields.Char(string='Duracion', compute='_compute_dur')
    # El vendedor puede marcar de que cliente fue la llamada (opcional).
    partner_id = fields.Many2one('res.partner', string='Cliente',
                                 domain=[('foco_cliente_ventas', '=', True)])
    grabacion_consentida = fields.Boolean(string='Grabacion con aviso')
    origen = fields.Selection([
        ('whatsapp', 'WhatsApp'),
        ('celular', 'Llamada celular'),
        ('otro', 'Otra'),
    ], string='Origen', default='whatsapp', index=True,
       help='De donde salio la llamada. En celular Android solo deja captar al '
            'vendedor por VOICE_RECOGNITION; en WhatsApp, igual. Los dos lados solo '
            'los daria la grabadora del sistema, cuyo archivo es privado.')

    # --- audio + coaching (calcado de foco.visita) ---
    audio = fields.Binary(string='Audio de la llamada', attachment=True)
    audio_name = fields.Char(string='Archivo de audio')
    audio_estado = fields.Selection([
        ('sin_audio', 'Sin audio'),
        ('pendiente', 'Pendiente de analizar'),
        ('procesando', 'Analizando'),
        ('listo', 'Analizada'),
        ('error', 'Error'),
    ], string='Audio', default='sin_audio', index=True)
    transcripcion = fields.Text(string='Transcripcion')
    transcripcion_dialogo = fields.Text(string='Dialogo (JSON)')
    dialogo_html = fields.Html(string='Conversacion', compute='_compute_dialogo_html',
                               sanitize=False)
    analisis_resumen = fields.Text(string='Resumen de la IA')
    analisis_fortalezas = fields.Text(string='Fortalezas')
    analisis_debilidades = fields.Text(string='Debilidades')
    analisis_mejoras = fields.Text(string='Mejoras')
    analisis_puntaje = fields.Integer(string='Puntaje (0-100)')
    analizado_el = fields.Datetime(string='Analizada el')

    @api.depends('duration_s')
    def _compute_dur(self):
        for rec in self:
            s = rec.duration_s or 0
            rec.duration_text = '%d:%02d' % (s // 60, s % 60)

    @api.depends('employee_id', 'started_at')
    def _compute_name(self):
        for rec in self:
            quien = rec.employee_id.name or 'Vendedor'
            cuando = fields.Datetime.to_string(rec.started_at) if rec.started_at else ''
            rec.name = ('%s · %s' % (quien, cuando)).strip(' ·')

    @api.depends('transcripcion_dialogo')
    def _compute_dialogo_html(self):
        """Burbujas tipo chat: el VENDEDOR a la derecha (verde), el CLIENTE a la
        izquierda (blanco). Mismo render que la visita."""
        for rec in self:
            try:
                turnos = json.loads(rec.transcripcion_dialogo or '[]')
            except Exception:
                turnos = []
            if not turnos:
                rec.dialogo_html = False
                continue
            filas = []
            for t in turnos:
                texto = (t.get('texto') or '').strip()
                if not texto:
                    continue
                es_vend = (t.get('rol') == 'vendedor')
                align = 'flex-end' if es_vend else 'flex-start'
                bg = '#d9fdd3' if es_vend else '#ffffff'
                etq_color = '#1a8a5a' if es_vend else '#8a6d1a'
                quien = 'Vendedor' if es_vend else 'Cliente'
                filas.append(
                    '<div style="display:flex;justify-content:%s;margin:3px 0;">'
                    '<div style="max-width:80%%;background:%s;border:1px solid #e4e4e4;'
                    'border-radius:12px;padding:7px 11px;box-shadow:0 1px 1px rgba(0,0,0,.08);">'
                    '<div style="font-size:11px;font-weight:600;color:%s;margin-bottom:2px;">%s</div>'
                    '<div style="white-space:pre-wrap;color:#111;line-height:1.35;">%s</div>'
                    '</div></div>' % (align, bg, etq_color, quien, escape(texto)))
            rec.dialogo_html = Markup(
                '<div style="background:#efeae2;padding:12px;border-radius:10px;'
                'max-height:540px;overflow:auto;">%s</div>' % ''.join(filas))

    def action_analizar(self):
        self._analizar_audio()

    def _analizar_audio(self):
        """Transcribe el WAV de la llamada y evalua al vendedor con la rubrica de
        las visitas. Blindado por llamada: si una truena, las demas siguen."""
        OpenAI = self.env['foco.openai']
        if not OpenAI.configurado():
            return
        for rec in self:
            if not rec.audio:
                continue
            rec.audio_estado = 'procesando'
            try:
                raw = base64.b64decode(rec.audio)
                texto = OpenAI.transcribir(
                    raw, nombre=rec.audio_name or 'llamada.wav', idioma='es',
                    mime='audio/wav')
                rec.transcripcion = texto or ''
                if not (texto or '').strip():
                    rec.audio_estado = 'listo'
                    rec.analizado_el = fields.Datetime.now()
                    continue
                # Reparto en turnos (burbujas): best-effort, su fallo no tumba el
                # coaching.
                try:
                    rd = OpenAI.chat(
                        [{'role': 'system', 'content': SISTEMA_DIALOGO},
                         {'role': 'user', 'content': NOTA_LLAMADA + '\n\n' + texto[:12000]}],
                        response_format={'type': 'json_schema', 'json_schema': ESQUEMA_DIALOGO},
                        max_tokens=2500)
                    turnos = json.loads(rd['message'].get('content') or '{}').get('turnos') or []
                    rec.transcripcion_dialogo = json.dumps(turnos, ensure_ascii=False)
                except Exception:
                    pass
                ctx = NOTA_LLAMADA
                if rec.partner_id:
                    p = rec.partner_id
                    ctx += ('\nCliente: %s. Cultivo: %s. Zona: %s.'
                            % (p.display_name, p.foco_cultivo or '-', p.foco_zona or '-'))
                resp = OpenAI.chat(
                    [{'role': 'system', 'content': SISTEMA_VISITA},
                     {'role': 'user', 'content': ctx + '\n\nTRANSCRIPCION DE LA LLAMADA:\n' + texto[:12000]}],
                    response_format={'type': 'json_schema', 'json_schema': ESQUEMA_VISITA},
                    max_tokens=1300)
                data = json.loads(resp['message'].get('content') or '{}')
                rec.analisis_resumen = (data.get('resumen') or '').strip()
                rec.analisis_fortalezas = '\n'.join('- ' + x for x in (data.get('fortalezas') or []))
                rec.analisis_debilidades = '\n'.join('- ' + x for x in (data.get('debilidades') or []))
                rec.analisis_mejoras = '\n'.join('- ' + x for x in (data.get('mejoras') or []))
                rec.analisis_puntaje = int(data.get('puntaje') or 0)
                rec.analizado_el = fields.Datetime.now()
                rec.audio_estado = 'listo'
            except Exception as e:
                rec.audio_estado = 'error'
                try:
                    rec.message_post(body='No se pudo analizar la llamada: %s' % e)
                except Exception:
                    pass

    @api.model
    def _cron_analizar_llamadas(self):
        """Procesa las llamadas con audio pendiente (lo encola el telefono)."""
        pend = self.search([('audio_estado', '=', 'pendiente'),
                            ('audio', '!=', False)], limit=10)
        for rec in pend:
            rec._analizar_audio()

    def coaching_para_telefono(self):
        """Lo que se le regresa al telefono para que el vendedor lo lea."""
        self.ensure_one()
        try:
            turnos = json.loads(self.transcripcion_dialogo or '[]')
        except Exception:
            turnos = []
        def _lista(txt):
            return [l[2:] if l.startswith('- ') else l
                    for l in (txt or '').splitlines() if l.strip()]
        return {
            'id': self.id,
            'fecha': fields.Datetime.to_string(self.started_at or self.create_date) or '',
            'duracion_s': self.duration_s or 0,
            'origen': self.origen or 'whatsapp',
            'estado': self.audio_estado,
            'puntaje': self.analisis_puntaje or 0,
            'resumen': self.analisis_resumen or '',
            'fortalezas': _lista(self.analisis_fortalezas),
            'debilidades': _lista(self.analisis_debilidades),
            'mejoras': _lista(self.analisis_mejoras),
            'dialogo': [{'rol': t.get('rol'), 'texto': t.get('texto')} for t in turnos],
        }
