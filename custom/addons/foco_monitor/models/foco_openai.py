import json
import logging

import requests

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

API = 'https://api.openai.com/v1'


def _alinea_pestanas(items, lote, modelo):
    """Empareja los items que devolvio el modelo con `lote` por el INDICE 'i'
    (1-based) que cada item trae, no por posicion.

    Por que: emparejar por posicion se rompe si el modelo devuelve menos items,
    los reordena o se salta uno (medido: con 12 titulos un salto corrio los
    veredictos y un Bitbucket quedo como 'Facebook'). Aqui cada item dice a que
    numero responde, asi un item de menos solo deja ESE titulo en 'revisar', sin
    correr a los demas.

    Robusto a: items de menos o de mas, 'i' fuera de rango, 'i' repetido (gana el
    ultimo), 'i' como texto ('3'), 'i' booleano (se ignora), e items que no son
    dict. Funcion a nivel de modulo para poder probarla sin Odoo.
    """
    porindice = {}
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        i = it.get('i')
        if isinstance(i, bool):          # bool es subclase de int: no cuenta como indice
            continue
        if not isinstance(i, int):
            try:
                i = int(str(i).strip())
            except (TypeError, ValueError):
                continue
        porindice[i] = it
    salida = []
    for pos, t in enumerate(lote, start=1):
        it = porindice.get(pos, {})
        sug = it.get('sugerencia')
        salida.append({
            'titulo': t,
            'va_con_rol': bool(it.get('va_con_rol', True)),
            'sugerencia': sug if sug in ('permitir', 'bloquear', 'revisar') else 'revisar',
            'motivo': (it.get('motivo') or '')[:200],
            'dominio': (it.get('dominio') or '').strip().lower(),
            'modelo': modelo,
        })
    return salida

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
    def transcribir(self, wav_bytes, nombre='pista.wav', idioma='es', mime='audio/wav'):
        """Texto de un audio (WAV 16 kHz mono basta; tambien m4a/mp3). Levanta
        UserError si la API falla: quien llama decide si reintenta."""
        key = self.api_key()
        if not key:
            raise UserError('Falta la API key de OpenAI (Foco > Configuracion > Analisis de llamadas).')
        modelo = self._param('foco.openai_transcribe_model', 'gpt-4o-mini-transcribe')
        try:
            r = requests.post(
                API + '/audio/transcriptions', headers={'Authorization': 'Bearer ' + key},
                files={'file': (nombre, wav_bytes, mime)},
                data={'model': modelo, 'language': idioma, 'response_format': 'json'},
                timeout=180)
        except requests.RequestException as e:
            raise UserError('OpenAI transcripcion: sin respuesta (%s)' % e)
        if r.status_code != 200:
            raise UserError('OpenAI transcripcion %s: %s' % (r.status_code, r.text[:300]))
        return (r.json().get('text') or '').strip()

    @api.model
    def chat(self, messages, tools=None, response_format=None, max_tokens=900, model=None):
        """Una vuelta de chat.completions. Devuelve {'message', 'usage', 'modelo'}.

        Es la pieza que usa Tomy: `message` trae `content` o `tool_calls`, y
        quien llama decide si ejecuta herramientas y vuelve a llamar."""
        key = self.api_key()
        if not key:
            raise UserError('Falta la API key de OpenAI (Foco > Configuracion > Analisis de llamadas).')
        modelo = model or self._param('foco.openai_chat_model', 'gpt-4o-mini')
        cuerpo = {'model': modelo, 'temperature': 0, 'messages': messages,
                  'max_tokens': int(max_tokens or 900)}
        if tools:
            cuerpo['tools'] = tools
            cuerpo['tool_choice'] = 'auto'
        if response_format:
            cuerpo['response_format'] = response_format
        try:
            r = requests.post(API + '/chat/completions', headers={'Authorization': 'Bearer ' + key},
                              json=cuerpo, timeout=60)
        except requests.RequestException as e:
            raise UserError('OpenAI chat: sin respuesta (%s)' % e)
        if r.status_code != 200:
            raise UserError('OpenAI chat %s: %s' % (r.status_code, r.text[:300]))
        j = r.json()
        try:
            mensaje = j['choices'][0]['message']
        except (KeyError, IndexError, TypeError):
            raise UserError('OpenAI chat: respuesta sin mensaje')
        return {'message': mensaje, 'usage': j.get('usage') or {}, 'modelo': modelo}

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

    @api.model
    def clasificar_pestanas(self, rol_prompt, titulos):
        """Clasifica TITULOS de pestañas contra el perfil de un puesto.

        Devuelve una lista alineada con `titulos` (uno por cada entrada) con
        titulo, va_con_rol, sugerencia (permitir/bloquear/revisar), motivo y
        dominio (mejor conjetura del sitio desde el titulo, '' si no se deduce).

        Se empareja por el INDICE que el modelo devuelve, no por posicion:
        emparejar por posicion se rompe si el modelo reordena o se SALTA uno
        (medido el 3-oct: con 12 titulos un salto corrio los veredictos y un
        Bitbucket quedo clasificado como 'Facebook'). Se procesa en lotes para no
        pedirle demasiados de una vez. Un fallo de la API sube como UserError
        (quien llama decide si reintenta); una respuesta sin JSON valido deja ese
        lote en 'revisar'.
        """
        titulos = [t for t in (titulos or []) if t]
        if not titulos:
            return []
        rol = (rol_prompt or '').strip() or '(sin descripcion del puesto)'
        CHUNK = 20
        salida = []
        for inicio in range(0, len(titulos), CHUNK):
            salida.extend(self._clasificar_lote(rol, titulos[inicio:inicio + CHUNK]))
        return salida

    @api.model
    def _clasificar_lote(self, rol, lote):
        """Un lote: pide al modelo que DEVUELVA el indice de cada titulo y
        empareja por ese indice."""
        sistema = (
            "Eres el clasificador de PRODUCTIVIDAD de pestañas de navegador de una empresa. Te "
            "doy el PERFIL de un puesto y una lista NUMERADA de titulos de pestañas que un "
            "empleado de ese puesto tiene abiertas. Por cada titulo decide si encaja con el "
            "trabajo de ese perfil. Reglas: (1) responde un item POR CADA numero, e incluye su "
            "'i' (el numero del titulo al que responde); (2) 'va_con_rol' true si es plausiblemente "
            "de ese trabajo; (3) 'sugerencia'='permitir' si encaja, 'bloquear' si claramente es "
            "ocio o ajeno al puesto, 'revisar' si no se puede saber por el titulo; (4) 'motivo' "
            "maximo 12 palabras, sin nombres de personas; (5) 'dominio'=tu mejor conjetura del "
            "dominio (p. ej. 'youtube.com') si el titulo lo deja claro, si no cadena vacia; NO "
            "inventes un dominio que no se deduzca del titulo. Juzga cada titulo por SI MISMO.")
        esquema = {
            'name': 'clasificacion_pestanas', 'strict': True,
            'schema': {
                'type': 'object', 'additionalProperties': False,
                'properties': {
                    'items': {
                        'type': 'array',
                        'items': {
                            'type': 'object', 'additionalProperties': False,
                            'properties': {
                                'i': {'type': 'integer'},
                                'va_con_rol': {'type': 'boolean'},
                                'sugerencia': {'type': 'string',
                                               'enum': ['permitir', 'bloquear', 'revisar']},
                                'motivo': {'type': 'string'},
                                'dominio': {'type': 'string'},
                            },
                            'required': ['i', 'va_con_rol', 'sugerencia', 'motivo', 'dominio'],
                        },
                    },
                },
                'required': ['items'],
            },
        }
        usuario = ("PERFIL DEL PUESTO:\n%s\n\nPESTAÑAS:\n%s"
                   % (rol, "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(lote))))
        resp = self.chat(
            [{'role': 'system', 'content': sistema},
             {'role': 'user', 'content': usuario}],
            response_format={'type': 'json_schema', 'json_schema': esquema},
            max_tokens=1800)
        try:
            items = json.loads(resp['message'].get('content') or '{}').get('items') or []
        except (ValueError, TypeError):
            items = []
        return _alinea_pestanas(items, lote, resp.get('modelo') or '')
