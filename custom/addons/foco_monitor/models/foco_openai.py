import json
import logging
import re

import requests

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Dos proveedores, a proposito:
# - ANTHROPIC (Claude) hace TODO el trabajo de lenguaje y vision: chat, veredicto
#   por imagen y clasificacion. Es mas preciso y alucina menos (decision del
#   8-oct-2026). La llave vive en el parametro foco.anthropic_api_key.
# - OPENAI queda SOLO para transcribir audio (Whisper): Claude no transcribe.
#   Si no se usa el analisis de llamadas de WhatsApp, su llave puede borrarse.
OPENAI_API = 'https://api.openai.com/v1'
ANTHROPIC_API = 'https://api.anthropic.com/v1/messages'
# Reglas de la familia Claude 5 (medidas contra la API el 8-oct-2026):
# - NO acepta `temperature` (deprecado -> 400).
# - Sonnet/Opus NO soportan tool_choice forzado ("tool"/"any"): el JSON
#   estructurado se pide por TEXTO y se parsea, no con una herramienta forzada.
# - El tool use AUTOMATICO (lo de Tomy) si funciona.
ANTHROPIC_VERSION = '2023-06-01'


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


def _alinea_whatsapp(items, lote, modelo):
    """Como `_alinea_pestanas`, pero para nombres de grupos de WhatsApp: la
    sugerencia es productiva/distraccion/revisar (no permitir/bloquear). Empareja
    por el indice 'i'. Funcion a nivel de modulo para poder probarla sin Odoo."""
    porindice = {}
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        i = it.get('i')
        if isinstance(i, bool):
            continue
        if not isinstance(i, int):
            try:
                i = int(str(i).strip())
            except (TypeError, ValueError):
                continue
        porindice[i] = it
    salida = []
    for pos, n in enumerate(lote, start=1):
        it = porindice.get(pos, {})
        sug = it.get('sugerencia')
        salida.append({
            'nombre': n,
            'sugerencia': sug if sug in ('productiva', 'distraccion', 'revisar') else 'revisar',
            'motivo': (it.get('motivo') or '')[:200],
            'modelo': modelo,
        })
    return salida


def _extraer_json(txt):
    """El primer objeto {...} de un texto. Tolera fences ```json y prosa
    alrededor, y cuenta llaves SALTANDO las cadenas (para no confundirse con un
    '}' dentro de un motivo). Devuelve None si no hay JSON valido."""
    txt = (txt or '').strip()
    if txt.startswith('```'):
        txt = re.sub(r'^```[a-zA-Z0-9]*\s*', '', txt)
        txt = re.sub(r'\s*```\s*$', '', txt).strip()
    try:
        return json.loads(txt)                      # camino rapido: ya es JSON limpio
    except ValueError:
        pass
    i = txt.find('{')
    if i < 0:
        return None
    prof = 0
    en_cadena = False
    escape = False
    for k in range(i, len(txt)):
        c = txt[k]
        if en_cadena:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                en_cadena = False
            continue
        if c == '"':
            en_cadena = True
        elif c == '{':
            prof += 1
        elif c == '}':
            prof -= 1
            if prof == 0:
                try:
                    return json.loads(txt[i:k + 1])
                except ValueError:
                    return None
    return None


def _esquema_a_texto(esquema):
    """Saca el JSON Schema de un `response_format`/esquema estilo OpenAI
    ({'name','strict','schema': {...}}) o acepta el esquema pelado."""
    if isinstance(esquema, dict) and 'schema' in esquema:
        esquema = esquema['schema']
    return json.dumps(esquema or {}, ensure_ascii=False)

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
    """Cliente de IA de Foco. El nombre del modelo se queda ('foco.openai') para
    no tocar a las decenas de sitios que lo llaman, pero por dentro habla con
    ANTHROPIC (Claude) para chat, vision y clasificacion; OPENAI solo se usa para
    transcribir audio.

    Las llaves viven en parametros del sistema (foco.anthropic_api_key,
    foco.openai_api_key), nunca en el codigo ni en los equipos: revocarlas es
    cambiar un parametro. Los modelos tambien son parametros, para cambiarlos sin
    desplegar.
    """
    _name = 'foco.openai'
    _description = 'Cliente de IA para Foco (Claude; OpenAI solo transcripcion)'

    @api.model
    def _param(self, clave, default=''):
        return (self.env['ir.config_parameter'].sudo().get_param(clave) or default).strip()

    @api.model
    def api_key(self):
        """La llave de la IA principal es la de Anthropic (Claude)."""
        return self._param('foco.anthropic_api_key')

    @api.model
    def _openai_key(self):
        """Solo para transcribir audio (Whisper)."""
        return self._param('foco.openai_api_key')

    @api.model
    def configurado(self):
        return bool(self.api_key())

    # --------------------------------------------------------- nucleo Anthropic
    @api.model
    def _anthropic(self, system, messages, max_tokens, modelo, tools=None,
                   tool_choice=None, timeout=120):
        """Una llamada a /v1/messages. Sin `temperature` (la familia Claude 5 ya
        no lo acepta). Levanta UserError si falla: quien llama decide si reintenta."""
        key = self.api_key()
        if not key:
            raise UserError('Falta la API key de Anthropic (Foco > Configuracion).')
        cuerpo = {'model': modelo, 'max_tokens': int(max_tokens or 700), 'messages': messages}
        if system:
            cuerpo['system'] = system
        if tools:
            cuerpo['tools'] = tools
        if tool_choice:
            cuerpo['tool_choice'] = tool_choice
        headers = {'x-api-key': key, 'anthropic-version': ANTHROPIC_VERSION,
                   'content-type': 'application/json'}
        try:
            r = requests.post(ANTHROPIC_API, headers=headers, json=cuerpo, timeout=timeout)
        except requests.RequestException as e:
            raise UserError('Anthropic: sin respuesta (%s)' % e)
        if r.status_code != 200:
            raise UserError('Anthropic %s: %s' % (r.status_code, r.text[:300]))
        return r.json()

    @api.model
    def _tokens(self, j):
        u = j.get('usage') or {}
        return int((u.get('input_tokens') or 0) + (u.get('output_tokens') or 0))

    @api.model
    def _texto(self, j):
        return ''.join(b.get('text', '') for b in (j.get('content') or [])
                       if b.get('type') == 'text')

    @api.model
    def _json_por_texto(self, system, messages, esquema, max_tokens, modelo, timeout=120):
        """JSON estructurado SIN tool forzado (Sonnet/Opus 5.x no lo soportan):
        se pide el JSON por texto, con el esquema en el sistema, y se parsea.
        Devuelve (dict, tokens). Levanta UserError si no vino JSON valido."""
        sis = ((system + '\n\n') if system else '')
        sis += ('Responde UNICAMENTE con un objeto JSON valido que cumpla este JSON Schema, '
                'sin ningun texto antes ni despues y sin ```:\n' + _esquema_a_texto(esquema))
        j = self._anthropic(sis, messages, max_tokens, modelo, timeout=timeout)
        res = _extraer_json(self._texto(j))
        if not isinstance(res, dict):
            raise UserError('Anthropic: respuesta sin JSON valido')
        return res, self._tokens(j)

    # ---- traductores entre el formato OpenAI (que usan los que llaman) y Anthropic
    @api.model
    def _traducir_mensajes(self, messages):
        """messages estilo OpenAI -> (system, messages estilo Anthropic).

        - 'system' se saca aparte (Anthropic lo lleva en un campo propio).
        - 'assistant' con tool_calls -> bloques tool_use.
        - 'tool' (resultado) -> bloque tool_result dentro de un mensaje user;
          varios seguidos se juntan en el MISMO user (Anthropic lo exige)."""
        partes_sistema = []
        salida = []
        for m in (messages or []):
            rol = m.get('role')
            if rol == 'system':
                c = m.get('content')
                if c:
                    partes_sistema.append(c if isinstance(c, str) else str(c))
            elif rol == 'tool':
                bloque = {'type': 'tool_result', 'tool_use_id': m.get('tool_call_id'),
                          'content': m.get('content') or ''}
                if (salida and salida[-1]['role'] == 'user'
                        and isinstance(salida[-1]['content'], list)
                        and salida[-1]['content']
                        and salida[-1]['content'][-1].get('type') == 'tool_result'):
                    salida[-1]['content'].append(bloque)
                else:
                    salida.append({'role': 'user', 'content': [bloque]})
            elif rol == 'assistant':
                bloques = []
                if m.get('content'):
                    bloques.append({'type': 'text', 'text': m['content']})
                for tc in (m.get('tool_calls') or []):
                    fn = tc.get('function') or {}
                    try:
                        args = json.loads(fn.get('arguments') or '{}')
                    except (ValueError, TypeError):
                        args = {}
                    bloques.append({'type': 'tool_use', 'id': tc.get('id'),
                                    'name': fn.get('name'), 'input': args})
                salida.append({'role': 'assistant', 'content': bloques or ''})
            else:  # user
                c = m.get('content')
                salida.append({'role': 'user', 'content': c if isinstance(c, list) else (c or '')})
        return '\n\n'.join(p for p in partes_sistema if p), salida

    @api.model
    def _traducir_tools(self, tools):
        """tools estilo OpenAI ({'type':'function','function':{...}}) -> Anthropic."""
        out = []
        for t in (tools or []):
            fn = t.get('function') or {}
            out.append({'name': fn.get('name'),
                        'description': fn.get('description') or '',
                        'input_schema': fn.get('parameters') or {'type': 'object', 'properties': {}}})
        return out

    @api.model
    def _de_anthropic(self, j):
        """Respuesta de Anthropic -> `message` estilo OpenAI, para que quien
        llama (Tomy) lea `content` y `tool_calls` igual que antes."""
        texto = []
        tool_calls = []
        for blk in (j.get('content') or []):
            t = blk.get('type')
            if t == 'text':
                texto.append(blk.get('text') or '')
            elif t == 'tool_use':
                tool_calls.append({
                    'id': blk.get('id'), 'type': 'function',
                    'function': {'name': blk.get('name'),
                                 'arguments': json.dumps(blk.get('input') or {}, ensure_ascii=False)}})
        mensaje = {'role': 'assistant', 'content': (''.join(texto) or None)}
        if tool_calls:
            mensaje['tool_calls'] = tool_calls
        return mensaje

    # --------------------------------------------------- transcripcion (OpenAI)
    @api.model
    def transcribir(self, wav_bytes, nombre='pista.wav', idioma='es', mime='audio/wav'):
        """Texto de un audio (WAV 16 kHz mono basta; tambien m4a/mp3). Usa OpenAI
        (Whisper): Claude no transcribe audio. Levanta UserError si la API falla o
        si no hay llave de OpenAI."""
        key = self._openai_key()
        if not key:
            raise UserError('La transcripcion de llamadas usa OpenAI (Whisper); Claude no '
                            'transcribe audio. Pon una API key de OpenAI en Configuracion solo '
                            'para esto, o desactiva el analisis de llamadas.')
        modelo = self._param('foco.openai_transcribe_model', 'gpt-4o-mini-transcribe')
        try:
            r = requests.post(
                OPENAI_API + '/audio/transcriptions', headers={'Authorization': 'Bearer ' + key},
                files={'file': (nombre, wav_bytes, mime)},
                data={'model': modelo, 'language': idioma, 'response_format': 'json'},
                timeout=180)
        except requests.RequestException as e:
            raise UserError('OpenAI transcripcion: sin respuesta (%s)' % e)
        if r.status_code != 200:
            raise UserError('OpenAI transcripcion %s: %s' % (r.status_code, r.text[:300]))
        return (r.json().get('text') or '').strip()

    # ----------------------------------------------------------- chat (Claude)
    @api.model
    def chat(self, messages, tools=None, response_format=None, max_tokens=900, model=None):
        """Una vuelta de conversacion. Devuelve {'message', 'usage', 'modelo'}
        con `message` en forma OpenAI ('content' o 'tool_calls'), para no tocar a
        Tomy. Si `response_format` pide un json_schema, se resuelve por texto y el
        JSON viene como cadena en `message.content` (como antes)."""
        modelo = model or self._param('foco.ai_chat_model', 'claude-sonnet-5-5')
        system, amsgs = self._traducir_mensajes(messages)
        if response_format and response_format.get('type') == 'json_schema':
            esquema = response_format.get('json_schema') or {}
            res, tokens = self._json_por_texto(system, amsgs, esquema, max_tokens or 900, modelo)
            return {'message': {'role': 'assistant',
                                'content': json.dumps(res, ensure_ascii=False), 'tool_calls': []},
                    'usage': {'total_tokens': tokens}, 'modelo': modelo}
        atools = self._traducir_tools(tools) if tools else None
        # tool_choice se deja en automatico (ni "tool" ni "any": Sonnet/Opus 5.x
        # no los aceptan, y Tomy necesita que el modelo decida).
        j = self._anthropic(system, amsgs, max_tokens or 900, modelo, tools=atools)
        return {'message': self._de_anthropic(j),
                'usage': {'total_tokens': self._tokens(j)}, 'modelo': modelo}

    # --------------------------------------------------------- vision (Claude)
    @api.model
    def vision(self, imagenes, sistema, usuario, esquema, max_tokens=700, detail='low'):
        """Una pregunta con imagenes y respuesta en JSON con esquema.

        `imagenes`: lista de (mime, base64) en el orden en que el modelo debe
        verlas. `detail` era de OpenAI (resolucion); Anthropic no lo usa y se
        conserva solo por compatibilidad de firma. Devuelve {'json','tokens','modelo'}.
        Levanta UserError si la API falla o no contesta JSON."""
        modelo = self._param('foco.ai_vision_model', 'claude-opus-5-5')
        bloques = [{'type': 'text', 'text': usuario}]
        for mime, b64 in imagenes:
            if isinstance(b64, bytes):
                b64 = b64.decode('ascii', 'ignore')
            bloques.append({'type': 'image', 'source': {
                'type': 'base64', 'media_type': mime or 'image/jpeg', 'data': b64}})
        res, tokens = self._json_por_texto(
            sistema, [{'role': 'user', 'content': bloques}], esquema, max_tokens or 700, modelo)
        return {'json': res, 'tokens': tokens, 'modelo': modelo}

    # ---------------------------------------------------- clasificar (Claude)
    @api.model
    def clasificar(self, contexto, empleado, otro, duracion_s):
        """dict(clasificacion, confianza, con_quien, motivo, tokens, modelo)."""
        modelo = self._param('foco.ai_classify_model', 'claude-sonnet-5-5')
        contexto = (contexto or '').strip()
        if contexto and not contexto.endswith('.'):
            contexto += '.'
        usuario = ('Duracion: %d s.\n\nEMPLEADO:\n%s\n\nINTERLOCUTOR:\n%s'
                   % (int(duracion_s or 0), (empleado or '').strip() or '(sin voz)',
                      (otro or '').strip() or '(sin voz)'))
        res, tokens = self._json_por_texto(
            SISTEMA.format(contexto=contexto or 'la empresa.'),
            [{'role': 'user', 'content': usuario}], ESQUEMA, 500, modelo)
        res['tokens'] = tokens
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

    @api.model
    def clasificar_whatsapp(self, nombres):
        """Clasifica NOMBRES de grupos/chats de WhatsApp como productivos o
        distraccion (requisito de Francesco, 9-oct-2026).

        Devuelve una lista alineada con `nombres`: nombre, sugerencia
        (productiva/distraccion/revisar), motivo y modelo. Igual que las
        pestañas: se empareja por indice y se procesa en lotes. Juzga SOLO por el
        nombre del grupo, nunca mensajes. Un fallo de la API sube como UserError.
        """
        nombres = [n for n in (nombres or []) if n]
        if not nombres:
            return []
        CHUNK = 20
        salida = []
        for inicio in range(0, len(nombres), CHUNK):
            salida.extend(self._clasificar_wa_lote(nombres[inicio:inicio + CHUNK]))
        return salida

    @api.model
    def _clasificar_wa_lote(self, lote):
        sistema = (
            "Eres el clasificador de PRODUCTIVIDAD de WhatsApp de una empresa. Te doy una "
            "lista NUMERADA de NOMBRES de grupos o chats de WhatsApp (solo el nombre, nunca "
            "mensajes). Por cada uno decide si su uso es de TRABAJO o de distraccion. Reglas: "
            "(1) responde un item POR CADA numero, e incluye su 'i' (el numero al que "
            "respondes); (2) 'sugerencia'='productiva' si el nombre sugiere trabajo (ventas, "
            "clientes, proveedores, un proyecto, obra, cotizaciones, soporte, coordinacion de "
            "un area), 'distraccion' si sugiere ocio o personal (memes, familia, amigos, "
            "futbol, fiestas, chismes), 'revisar' si el nombre no permite saberlo (un nombre "
            "de persona a secas, siglas, algo ambiguo); (3) 'motivo' maximo 12 palabras, sin "
            "nombres de personas. Juzga cada nombre por SI MISMO; ante la duda, 'revisar'.")
        esquema = {
            'name': 'clasificacion_whatsapp', 'strict': True,
            'schema': {
                'type': 'object', 'additionalProperties': False,
                'properties': {
                    'items': {
                        'type': 'array',
                        'items': {
                            'type': 'object', 'additionalProperties': False,
                            'properties': {
                                'i': {'type': 'integer'},
                                'sugerencia': {'type': 'string',
                                               'enum': ['productiva', 'distraccion', 'revisar']},
                                'motivo': {'type': 'string'},
                            },
                            'required': ['i', 'sugerencia', 'motivo'],
                        },
                    },
                },
                'required': ['items'],
            },
        }
        usuario = ("GRUPOS DE WHATSAPP:\n%s"
                   % "\n".join("%d. %s" % (i + 1, n) for i, n in enumerate(lote)))
        resp = self.chat(
            [{'role': 'system', 'content': sistema},
             {'role': 'user', 'content': usuario}],
            response_format={'type': 'json_schema', 'json_schema': esquema},
            max_tokens=1500)
        try:
            items = json.loads(resp['message'].get('content') or '{}').get('items') or []
        except (ValueError, TypeError):
            items = []
        return _alinea_whatsapp(items, lote, resp.get('modelo') or '')
