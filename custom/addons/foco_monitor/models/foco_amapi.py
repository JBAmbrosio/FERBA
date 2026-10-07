"""Android Enterprise: cliente de la Android Management API (AMAPI) de Google.

POR QUE EXISTE (28-sep-2026)
    Los celulares de la empresa tienen que quedar mucho mas restringidos: que la
    persona NO se instale lo que quiera de la Play Store, que pida las apps y el
    administrador las mande instalar desde Odoo, que no pueda compartir internet
    a otros telefonos, que no meta cuentas personales y que los sitios web se
    bloqueen igual que en las laptops.

    La unica via en que la Play Store muestra SOLO las apps aprobadas y en que
    Odoo puede instalar una app de la Play Store sin que la persona toque nada
    es Android Enterprise: el telefono se aprovisiona "totalmente gestionado"
    por la empresa y una POLITICA (JSON) que vive en Google dice que apps hay,
    que se bloquea y que configuracion lleva cada app. Esa politica la arma
    Odoo desde los perfiles de navegacion (los mismos de las laptops), las apps
    aprobadas y las solicitudes; este modulo solo la HABLA con Google.

    No hay addon de Odoo para esto: se habla directo con la API REST.

LO QUE NECESITA (una vez, lo hace el administrador)
    1. Un proyecto de Google Cloud con la Android Management API habilitada y
       una cuenta de servicio con su llave JSON (se pega en Odoo).
    2. Registrar la empresa ("enterprise") desde Odoo: Google pide iniciar
       sesion con una cuenta de FERBA y crea la Play Store administrada.
    Sin eso, nada de este modulo hace llamadas: todo responde con un mensaje
    que dice que falta.

SIN DEPENDENCIAS NUEVAS
    La cuenta de servicio se firma a mano (JWT RS256 con `cryptography`, que ya
    trae Odoo) y las llamadas van con `requests`. Asi el modulo no pide
    instalar la libreria de Google en Odoo.sh.
"""

import base64
import json
import logging
import threading
import time

import requests

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

GRUPO_ADMIN = 'foco_monitor.group_foco_manager'


def es_admin(env):
    """Quien puede administrar los celulares de la empresa: un administrador de
    Foco, o el propio sistema (el cron corre como superusuario, y los envios
    que se programan al guardar van con sudo).

    Hace falta revisarlo A MANO: este modelo es abstracto, sin tabla ni
    permisos, y Odoo deja llamar por RPC cualquier metodo publico de cualquier
    modelo a cualquier usuario con sesion. Sin esto, un usuario comun podria
    bloquear o borrar un telefono, o cambiar la llave de la empresa."""
    return env.su or env.user.has_group(GRUPO_ADMIN)


def exigir_admin(env):
    if not es_admin(env):
        raise AccessError('Solo un administrador de Foco puede administrar los celulares de la empresa.')

AMAPI = 'https://androidmanagement.googleapis.com/v1/'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/androidmanagement'
# La llave de la cuenta de servicio NO va en un campo del modelo: va en un
# parametro del sistema, que solo lee el administrador tecnico de Odoo. Nunca
# viaja al navegador.
PARAM_CUENTA = 'foco.amapi_service_account'

# Token de acceso por proceso de Odoo: dura una hora y pedir uno por llamada
# seria una ida y vuelta a Google de mas en cada guardado.
_TOKENS = {}
_CANDADO = threading.Lock()

# Lo que el iframe de la Play Store administrada deja hacer dentro de Odoo:
# buscar en la Play Store, subir apps privadas (la propia Foco) y armar la
# tienda de la empresa.
FUNCIONES_PLAY = ['PLAY_SEARCH', 'PRIVATE_APPS', 'WEB_APPS', 'STORE_BUILDER']


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode('ascii')


class FocoAmapi(models.AbstractModel):
    _name = 'foco.amapi'
    _description = 'Android Enterprise (Android Management API)'

    # ------------------------------------------------------------ cuenta
    @api.model
    def _cuenta(self):
        crudo = self.env['ir.config_parameter'].sudo().get_param(PARAM_CUENTA) or ''
        if not crudo.strip():
            raise UserError(
                'Falta la cuenta de servicio de Google. En Foco > Configuración > '
                'Monitoreo móvil > Android Enterprise, pulsa «Cargar cuenta de '
                'servicio» y pega el archivo JSON que descargaste de Google Cloud.')
        try:
            cuenta = json.loads(crudo)
        except ValueError:
            raise UserError('La cuenta de servicio guardada no es un JSON válido. Vuelve a cargarla.')
        return cuenta

    @api.model
    def validar_cuenta(self, crudo):
        """Revisa el JSON de la cuenta de servicio ANTES de guardarlo y devuelve
        el diccionario. Un archivo equivocado (la llave de otra cosa, un JSON
        de OAuth de escritorio) se rechaza aqui con un mensaje claro, no en la
        primera llamada a Google."""
        try:
            cuenta = json.loads(crudo or '')
        except ValueError:
            raise UserError('Eso no es un JSON. Pega el contenido completo del archivo .json de la cuenta de servicio.')
        if not isinstance(cuenta, dict) or cuenta.get('type') != 'service_account':
            raise UserError('El JSON no es de una cuenta de servicio ("type": "service_account"). '
                            'En Google Cloud: IAM y administración > Cuentas de servicio > Claves > Agregar clave > JSON.')
        for k in ('client_email', 'private_key', 'project_id'):
            if not cuenta.get(k):
                raise UserError('A la cuenta de servicio le falta «%s».' % k)
        return cuenta

    @api.model
    def guardar_cuenta(self, crudo):
        exigir_admin(self.env)
        cuenta = self.validar_cuenta(crudo)
        self.env['ir.config_parameter'].sudo().set_param(
            PARAM_CUENTA, json.dumps(cuenta, separators=(',', ':')))
        with _CANDADO:
            _TOKENS.clear()
        return cuenta

    @api.model
    def resumen_cuenta(self):
        """'foco@proyecto.iam.gserviceaccount.com · proyecto' o '' (sin cuenta).
        Nunca devuelve la llave."""
        if not es_admin(self.env):
            return ''
        try:
            c = self._cuenta()
        except UserError:
            return ''
        return '%s · %s' % (c.get('client_email', ''), c.get('project_id', ''))

    @api.model
    def proyecto(self):
        return self._cuenta()['project_id']

    # ------------------------------------------------------------ token
    @api.model
    def _jwt(self, cuenta, ahora):
        """La afirmacion firmada que se cambia por un token de acceso (el flujo
        de cuenta de servicio de Google, RFC 7523)."""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError:
            raise UserError('Falta la libreria "cryptography" en el servidor de Odoo.')
        cabecera = {'alg': 'RS256', 'typ': 'JWT'}
        if cuenta.get('private_key_id'):
            cabecera['kid'] = cuenta['private_key_id']
        reclamo = {'iss': cuenta['client_email'], 'scope': SCOPE, 'aud': TOKEN_URL,
                   'iat': ahora, 'exp': ahora + 3600}
        entrada = '%s.%s' % (
            _b64url(json.dumps(cabecera, separators=(',', ':')).encode()),
            _b64url(json.dumps(reclamo, separators=(',', ':')).encode()))
        try:
            llave = serialization.load_pem_private_key(
                cuenta['private_key'].encode(), password=None)
        except Exception:
            raise UserError('La llave privada de la cuenta de servicio no se pudo leer. Descarga una clave JSON nueva.')
        firma = llave.sign(entrada.encode(), padding.PKCS1v15(), hashes.SHA256())
        return '%s.%s' % (entrada, _b64url(firma))

    @api.model
    def _token(self):
        cuenta = self._cuenta()
        correo = cuenta['client_email']
        with _CANDADO:
            t = _TOKENS.get(correo)
            if t and t[1] - 60 > time.time():
                return t[0]
        ahora = int(time.time())
        try:
            r = requests.post(TOKEN_URL, timeout=20, data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': self._jwt(cuenta, ahora)})
        except requests.RequestException as e:
            raise UserError('No se pudo hablar con Google (%s).' % type(e).__name__)
        if r.status_code != 200:
            raise UserError('Google no aceptó la cuenta de servicio: %s' % self._mensaje(r))
        datos = r.json()
        tok = datos.get('access_token')
        if not tok:
            raise UserError('Google no devolvió un token de acceso.')
        with _CANDADO:
            _TOKENS[correo] = (tok, ahora + int(datos.get('expires_in') or 3600))
        return tok

    @api.model
    def _mensaje(self, r):
        """El mensaje de error de Google, legible. Es lo que el administrador
        necesita leer para arreglarlo (API no habilitada, permiso, campo)."""
        try:
            d = r.json()
        except ValueError:
            return 'HTTP %s' % r.status_code
        e = d.get('error')
        if isinstance(e, dict):
            return '%s (%s)' % (e.get('message') or '', e.get('status') or r.status_code)
        if isinstance(e, str):
            return '%s: %s' % (e, d.get('error_description') or '')
        return 'HTTP %s' % r.status_code

    @api.model
    def _llamar(self, metodo, ruta, params=None, cuerpo=None):
        # Toda orden a Google pasa por aqui: es el candado que no se salta.
        exigir_admin(self.env)
        tok = self._token()
        try:
            r = requests.request(
                metodo, AMAPI + ruta, params=params, json=cuerpo, timeout=40,
                headers={'Authorization': 'Bearer ' + tok})
        except requests.RequestException as e:
            raise UserError('No se pudo hablar con Google (%s).' % type(e).__name__)
        if r.status_code >= 400:
            raise UserError('Google respondió: %s' % self._mensaje(r))
        return r.json() if r.content else {}

    # ------------------------------------------------------------ empresa
    @api.model
    def empresa(self):
        exigir_admin(self.env)
        e = self.env['foco.settings'].sudo().get_settings().amapi_enterprise
        if not e:
            raise UserError('La empresa todavía no está registrada en Google. En Foco > '
                            'Configuración > Monitoreo móvil > Android Enterprise, pulsa '
                            '«Registrar la empresa en Google».')
        return e

    @api.model
    def probar(self):
        """Que la cuenta sirve: pide un token y lista las empresas del proyecto."""
        res = self._llamar('GET', 'enterprises', params={'projectId': self.proyecto(), 'pageSize': 20})
        return [e.get('name') for e in (res.get('enterprises') or [])]

    @api.model
    def url_registro(self, callback):
        """Inicia el alta de la empresa: Google devuelve una URL a la que se
        lleva al administrador, y al terminar vuelve a `callback` con un token."""
        return self._llamar('POST', 'signupUrls', params={
            'projectId': self.proyecto(), 'callbackUrl': callback})

    @api.model
    def registrar(self, nombre_signup, token_empresa, nombre_visible):
        return self._llamar('POST', 'enterprises', params={
            'projectId': self.proyecto(), 'signupUrlName': nombre_signup,
            'enterpriseToken': token_empresa,
        }, cuerpo={'enterpriseDisplayName': nombre_visible or 'FERBA'})

    # ------------------------------------------------------------ politicas
    @api.model
    def politica(self, id_politica, cuerpo):
        return self._llamar('PATCH', '%s/policies/%s' % (self.empresa(), id_politica),
                            cuerpo=cuerpo)

    @api.model
    def borrar_politica(self, id_politica):
        return self._llamar('DELETE', '%s/policies/%s' % (self.empresa(), id_politica))

    # ------------------------------------------------------------ altas
    @api.model
    def token_alta(self, nombre_politica, dato_extra, dias=7):
        return self._llamar('POST', '%s/enrollmentTokens' % self.empresa(), cuerpo={
            'policyName': nombre_politica,
            'duration': '%ds' % (int(dias) * 86400),
            'additionalData': dato_extra,
            'oneTimeOnly': True,
            # Totalmente gestionado: sin perfil personal. Es un telefono de la
            # empresa.
            'allowPersonalUsage': 'PERSONAL_USAGE_DISALLOWED',
        })

    # ------------------------------------------------------------ equipos
    @api.model
    def dispositivos(self):
        salida, pagina = [], None
        for _i in range(50):            # tope duro: 5,000 equipos
            params = {'pageSize': 100}
            if pagina:
                params['pageToken'] = pagina
            res = self._llamar('GET', '%s/devices' % self.empresa(), params=params)
            salida.extend(res.get('devices') or [])
            pagina = res.get('nextPageToken')
            if not pagina:
                break
        return salida

    @api.model
    def comando(self, nombre_equipo, tipo):
        return self._llamar('POST', '%s:issueCommand' % nombre_equipo, cuerpo={'type': tipo})

    @api.model
    def borrar_equipo(self, nombre_equipo, motivo=''):
        """Borra el equipo de la empresa. En un telefono totalmente gestionado
        eso es RESTABLECERLO DE FABRICA: se pierde todo lo que tenga."""
        params = {}
        if motivo:
            params['wipeReasonMessage'] = motivo[:200]
        return self._llamar('DELETE', nombre_equipo, params=params)

    # ------------------------------------------------------------ play store
    @api.model
    def ficha_play(self, url_padre):
        """Token de un solo uso para el iframe de la Play Store administrada."""
        res = self._llamar('POST', '%s/webTokens' % self.empresa(), cuerpo={
            'parentFrameUrl': url_padre, 'enabledFeatures': FUNCIONES_PLAY})
        return res.get('value') or ''


class FocoAmapiCuentaWizard(models.TransientModel):
    """Pegar la llave JSON de la cuenta de servicio. Un asistente y no un campo
    del ajuste para que la llave nunca se vuelva a leer desde el navegador:
    entra, se valida y se guarda en un parametro del sistema."""
    _name = 'foco.amapi.cuenta.wizard'
    _description = 'Cargar la cuenta de servicio de Google'

    cuenta_json = fields.Text(
        string='Contenido del archivo JSON', required=True,
        help='Abre con el Bloc de notas el archivo .json que descargaste de Google '
             'Cloud (Cuentas de servicio > Claves > Agregar clave > JSON) y pega '
             'aqui todo su contenido.')

    def action_guardar(self):
        self.ensure_one()
        cuenta = self.env['foco.amapi'].guardar_cuenta(self.cuenta_json)
        # La llave no se queda en el asistente (los transitorios viven un rato
        # en la base).
        self.cuenta_json = '(guardada)'
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'sticky': False,
            'message': 'Cuenta de servicio guardada: %s (proyecto %s).' % (
                cuenta['client_email'], cuenta['project_id']),
            'next': {'type': 'ir.actions.act_window_close'},
        }}
