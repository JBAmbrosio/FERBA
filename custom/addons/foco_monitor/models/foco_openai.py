import json
import logging

import requests

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

API = 'https://api.openai.com/v1'

# Lo que el clasificador tiene que saber para decidir. El contexto del negocio
# viene de Configuracion (call_review_context): "trabajo" se juzga respecto a lo
# que hace ESTA empresa, no en abstracto.
SISTEMA = (
    "Eres el clasificador de llamadas de {contexto} Recibes la transcripcion de una "
    "llamada de WhatsApp hecha desde la laptop de un empleado, en dos pistas: EMPLEADO (su "
    "microfono) e INTERLOCUTOR (lo que se oyo por las bocinas). Decide si la llamada fue de "
    "TRABAJO (clientes, proveedores, colegas, cotizaciones, logistica, cobranza, soporte, "
    "proyectos), PERSONAL (familia, pareja, amistades, asuntos privados), MIXTA (las dos "
    "cosas) o INDETERMINADA (vacia, ininteligible o demasiado corta para saberlo). Reglas: "
    "(1) 'motivo' tiene maximo 12 palabras; (2) si la llamada es personal o mixta, 'motivo' "
    "debe ser exactamente 'asunto personal', sin ningun detalle; (3) nunca incluyas nombres "
    "de personas, numeros ni datos privados en ningun campo; (4) 'con_quien' describe el "
    "rol del interlocutor, no su identidad.")

ESQUEMA = {
    'name': 'clasificacion_llamada',
    'strict': True,
    'schema': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'clasificacion': {'type': 'string',
                              'enum': ['trabajo', 'personal', 'mixta', 'indeterminada']},
            'confianza': {'type': 'number'},
            'con_quien': {'type': 'string',
                          'enum': ['cliente', 'proveedor', 'colega', 'familiar_o_amigo',
                                   'otro', 'desconocido']},
            'motivo': {'type': 'string'},
        },
        'required': ['clasificacion', 'confianza', 'con_quien', 'motivo'],
    },
}


class FocoOpenAI(models.AbstractModel):
    """Cliente minimo de OpenAI: transcribe una pista y clasifica una llamada.

    La llave vive en un parametro del sistema (foco.openai_api_key), nunca en
    el codigo ni en los equipos: revocarla es cambiar un parametro. Los modelos
    tambien son parametros, para poder cambiarlos sin desplegar.
    """
    _name = 'foco.openai'
    _description = 'Cliente de OpenAI para Foco'

    @api.model
    def _param(self, clave, default=''):
        return (self.env['ir.config_parameter'].sudo().get_param(clave) or default).strip()

    @api.model
    def api_key(self):
        return self._param('foco.openai_api_key')

    @api.model
    def configurado(self):
        return bool(self.api_key())

    @api.model
    def transcribir(self, wav_bytes, nombre='pista.wav', idioma='es'):
        """Texto de un WAV (16 kHz mono es suficiente). Levanta UserError si la
        API falla: quien llama decide si reintenta."""
        key = self.api_key()
        if not key:
            raise UserError('Falta la API key de OpenAI (Foco > Configuracion > Analisis de llamadas).')
        modelo = self._param('foco.openai_transcribe_model', 'gpt-4o-mini-transcribe')
        try:
            r = requests.post(
                API + '/audio/transcriptions', headers={'Authorization': 'Bearer ' + key},
                files={'file': (nombre, wav_bytes, 'audio/wav')},
                data={'model': modelo, 'language': idioma, 'response_format': 'json'},
                timeout=90)
        except requests.RequestException as e:
            raise UserError('OpenAI transcripcion: sin respuesta (%s)' % e)
        if r.status_code != 200:
            raise UserError('OpenAI transcripcion %s: %s' % (r.status_code, r.text[:300]))
        return (r.json().get('text') or '').strip()

    @api.model
    def clasificar(self, contexto, empleado, otro, duracion_s):
        """dict(clasificacion, confianza, con_quien, motivo, tokens, modelo)."""
        key = self.api_key()
        if not key:
            raise UserError('Falta la API key de OpenAI (Foco > Configuracion > Analisis de llamadas).')
        modelo = self._param('foco.openai_classify_model', 'gpt-4o-mini')
        contexto = (contexto or '').strip()
        if contexto and not contexto.endswith('.'):
            contexto += '.'
        cuerpo = {
            'model': modelo, 'temperature': 0,
            'response_format': {'type': 'json_schema', 'json_schema': ESQUEMA},
            'messages': [
                {'role': 'system', 'content': SISTEMA.format(contexto=contexto or 'la empresa.')},
                {'role': 'user', 'content': 'Duracion: %d s.\n\nEMPLEADO:\n%s\n\nINTERLOCUTOR:\n%s'
                 % (int(duracion_s or 0), (empleado or '').strip() or '(sin voz)',
                    (otro or '').strip() or '(sin voz)')},
            ],
        }
        try:
            r = requests.post(API + '/chat/completions', headers={'Authorization': 'Bearer ' + key},
                              json=cuerpo, timeout=90)
        except requests.RequestException as e:
            raise UserError('OpenAI clasificacion: sin respuesta (%s)' % e)
        if r.status_code != 200:
            raise UserError('OpenAI clasificacion %s: %s' % (r.status_code, r.text[:300]))
        j = r.json()
        try:
            res = json.loads(j['choices'][0]['message']['content'])
        except (KeyError, IndexError, ValueError, TypeError):
            raise UserError('OpenAI clasificacion: respuesta sin JSON valido')
        res['tokens'] = int((j.get('usage') or {}).get('total_tokens') or 0)
        res['modelo'] = modelo
        return res
