import json
import logging
from datetime import datetime, timedelta

import pytz

from odoo import SUPERUSER_ID, api, fields, models

_logger = logging.getLogger(__name__)

# USD por millon de tokens (entrada, salida), para la ESTIMACION de costo. El
# gasto real lo dice la consola de Anthropic; aqui el conteo de tokens es exacto
# (lo devuelve la API en cada llamada) y el precio es el de lista conocido al
# 11-oct-2026. Se corrige sin desplegar en el parametro del sistema
# foco.ai_precios_usd_millon: {"claude-sonnet-5-5": [3, 15], ...}.
PRECIOS_DEFAULT = {
    'claude-sonnet-5-5': [3.0, 15.0],
    'claude-opus-5-5': [15.0, 75.0],
    'claude-haiku-5-5': [1.0, 5.0],
}

SERVICIOS = [
    ('tomy', 'Tomy (chat del tablero)'),
    ('vision', 'Veredicto por vision'),
    ('justificacion', 'Justificaciones'),
    ('pestanas', 'Pestanas por revisar'),
    ('whatsapp', 'WhatsApp (grupos y encabezados)'),
    ('llamadas', 'Llamadas'),
    ('visitas', 'Visitas de campo'),
    ('movil', 'Movil (apps)'),
    ('transcripcion', 'Transcripcion de audio (OpenAI)'),
    ('chat', 'Chat sin herramientas'),
    ('gpt', 'Tommy GPT (chat libre)'),
    ('busqueda', 'Buscar en internet'),
    ('imagen', 'Generar imagenes (OpenAI)'),
    ('codigo', 'Ejecutar codigo (sandbox)'),
    ('otro', 'Otro'),
]


class FocoAiUsage(models.Model):
    """Una fila por llamada al modelo: el contador diario de tokens que pidio el
    dueno (9-oct-2026: "no me asusta, pero quiero verlo"). Lo escribe el cliente
    de IA (foco.openai) en cada llamada, en un cursor aparte, para que el gasto
    quede registrado aunque la transaccion de quien llamo se deshaga: la API ya
    cobro. Solo lo ven los administradores."""
    _name = 'foco.ai.usage'
    _description = 'Consumo de IA (una fila por llamada al modelo)'
    _order = 'at desc, id desc'

    at = fields.Datetime(string='Cuando', required=True, default=fields.Datetime.now, index=True)
    dia = fields.Date(string='Dia', required=True, index=True,
                      help='Dia en la zona de la empresa, para sumar por dia.')
    servicio = fields.Selection(SERVICIOS, string='Servicio', required=True, index=True, default='otro')
    modelo = fields.Char(string='Modelo')
    tokens_in = fields.Integer(string='Tokens de entrada')
    tokens_out = fields.Integer(string='Tokens de salida')
    tokens = fields.Integer(string='Tokens', index=True)
    costo_usd = fields.Float(string='Costo estimado (USD)', digits=(12, 5),
                             help='Estimacion con los precios de lista configurados; el real lo dice la consola.')
    ref = fields.Char(string='Referencia')
    nota = fields.Char(string='Nota')

    # ------------------------------------------------------------ precios
    @api.model
    def _zona(self):
        nombre = (self.env.company.resource_calendar_id.tz or self.env.company.partner_id.tz
                  or 'America/Mazatlan')
        try:
            return pytz.timezone(nombre)
        except Exception:
            return pytz.timezone('America/Mazatlan')

    @api.model
    def precios(self):
        tabla = {k: list(v) for k, v in PRECIOS_DEFAULT.items()}
        raw = self.env['ir.config_parameter'].sudo().get_param('foco.ai_precios_usd_millon')
        if raw:
            try:
                for k, v in (json.loads(raw) or {}).items():
                    tabla[str(k)] = [float(v[0]), float(v[1])]
            except Exception as e:
                _logger.warning('foco.ai_precios_usd_millon invalido (%s)', e)
        return tabla

    @api.model
    def costo(self, modelo, tokens_in, tokens_out):
        tabla = self.precios()
        p = tabla.get(modelo or '')
        if not p and modelo:
            # Variantes con fecha (claude-sonnet-5-5-20260301) caen en su familia.
            for k, v in tabla.items():
                if modelo.startswith(k):
                    p = v
                    break
        if not p:
            return 0.0
        return (tokens_in or 0) * p[0] / 1e6 + (tokens_out or 0) * p[1] / 1e6

    # ------------------------------------------------------------ registro
    @api.model
    def registrar(self, servicio, modelo, tokens_in=0, tokens_out=0, ref=None, nota=None):
        """Nunca tumba la llamada que lo invoca. Cursor aparte: el gasto queda
        aunque quien llamo deshaga su transaccion (la API ya cobro)."""
        try:
            claves = dict(SERVICIOS)
            ahora = datetime.utcnow().replace(microsecond=0)
            vals = {
                'at': ahora,
                'dia': pytz.UTC.localize(ahora).astimezone(self._zona()).date(),
                'servicio': servicio if servicio in claves else 'otro',
                'modelo': (modelo or '')[:80] or False,
                'tokens_in': int(tokens_in or 0), 'tokens_out': int(tokens_out or 0),
                'tokens': int(tokens_in or 0) + int(tokens_out or 0),
                'costo_usd': self.costo(modelo, tokens_in, tokens_out),
                'ref': (ref or '')[:120] or False, 'nota': (nota or '')[:200] or False,
            }
            # En pruebas (cursor de prueba) se escribe en la misma transaccion;
            # en produccion en un cursor aparte que se confirma al salir del with.
            # (Odoo 19 ya no tiene registry.in_test_mode().)
            if getattr(self.env.registry, 'test_cr', None) is not None:
                self.sudo().create(vals)
                return True
            with self.env.registry.cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})['foco.ai.usage'].create(vals)
            return True
        except Exception as e:
            _logger.warning('foco.ai.usage: no se registro el consumo (%s)', e)
            return False

    # ------------------------------------------------------------ tablero
    @api.model
    def umbral(self):
        # El tope de tokens es de la plataforma: la IA se paga una vez para todas.
        return int(self.env['foco.settings'].sudo().get_settings_global().ia_tokens_umbral_dia or 0)

    @api.model
    def resumen(self, dias=7):
        """Para el tablero: hoy (tokens, costo estimado, llamadas, por servicio),
        el umbral y la serie de los ultimos `dias` dias. ACL: solo administradores."""
        hoy = datetime.now(self._zona()).date()
        desde = hoy - timedelta(days=max(1, int(dias or 7)) - 1)
        etiquetas = dict(SERVICIOS)
        por_dia, hoy_serv = {}, {}
        for d, serv, tok, cos, n in self._read_group(
                [('dia', '>=', desde), ('dia', '<=', hoy)], ['dia:day', 'servicio'],
                ['tokens:sum', 'costo_usd:sum', '__count']):
            d = fields.Date.to_date(d)
            k = fields.Date.to_string(d)
            t = por_dia.setdefault(k, {'tokens': 0, 'costo': 0.0, 'llamadas': 0})
            t['tokens'] += int(tok or 0)
            t['costo'] += float(cos or 0.0)
            t['llamadas'] += int(n or 0)
            if d == hoy:
                hoy_serv[serv] = {'clave': serv, 'servicio': etiquetas.get(serv, serv), 'tokens': int(tok or 0),
                                  'costo': round(float(cos or 0.0), 4), 'llamadas': int(n or 0)}
        h = por_dia.get(fields.Date.to_string(hoy), {'tokens': 0, 'costo': 0.0, 'llamadas': 0})
        umbral = self.umbral()
        serie = []
        d = desde
        while d <= hoy:
            k = fields.Date.to_string(d)
            v = por_dia.get(k, {'tokens': 0, 'costo': 0.0, 'llamadas': 0})
            serie.append({'dia': k, 'tokens': v['tokens'], 'costo': round(v['costo'], 4), 'llamadas': v['llamadas']})
            d += timedelta(days=1)
        return {
            'dia': fields.Date.to_string(hoy), 'tokens': h['tokens'], 'costo': round(h['costo'], 4),
            'llamadas': h['llamadas'], 'umbral': umbral,
            'pct': int(round(100.0 * h['tokens'] / umbral)) if umbral else 0,
            'excedido': bool(umbral and h['tokens'] > umbral),
            'por_servicio': sorted(hoy_serv.values(), key=lambda x: -x['tokens']),
            'dias': serie, 'estimado': True,
        }
