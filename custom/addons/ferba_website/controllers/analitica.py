# -*- coding: utf-8 -*-
import hashlib
import logging
import os
import threading
import time
from urllib.parse import urlsplit

from odoo import fields, http
from odoo.http import request

from ..models.ferba_evento import SENALES

_logger = logging.getLogger(__name__)

NOMBRES_VALIDOS = {clave for clave, _etiqueta in SENALES}
LARGO_MAX = 128          # los campos libres se recortan, no se rechazan
VENTANA_REPETIDO = 600   # segundos que una misma señal se considera la misma

# Prefijos que Excel y LibreOffice interpretan como fórmula al abrir una hoja.
# Odoo antepone un apóstrofo en la rama CSV de sus exportaciones, pero NO en la
# XLSX ni en la del pivote, así que se neutraliza al ESCRIBIR y no al leer: de
# ese modo queda cubierto cualquier consumidor de estos campos, presente o
# futuro, incluidas las rutas de exportación que el módulo no controla.
PREFIJOS_FORMULA = ('=', '+', '-', '@', '\t', '\r')

# Cupo por origen. No sustituye a un límite de tasa en el borde, que es donde
# corresponde y que este módulo no puede poner; acota lo que un solo cliente
# consigue sacarle a un worker. Es por proceso y se pierde al reciclarlo: es
# una barrera de coste, no una garantía.
CUPO_SENALES = 30        # señales aceptadas por ventana y origen
CUPO_VENTANA = 600       # segundos
CUPO_MAX_CLAVES = 4096   # techo de memoria del contador

_cupo = {}
_cupo_cerrojo = threading.Lock()
# Sal por proceso: el contador no debe poder revertirse a una dirección IP.
_cupo_sal = os.urandom(16)


class AnaliticaFerba(http.Controller):
    """Entrada de las señales del sitio de Ferba.

    Es un endpoint público, sin sesión y sin CSRF, porque lo llama
    navigator.sendBeacon, que no puede llevar el token y no espera
    respuesta. Eso obliga a tratarlo como entrada hostil:

      - la petición tiene que venir del propio sitio: con csrf=False, Odoo 19
        no deja NINGÚN respaldo de Origin, Referer ni Sec-Fetch para una ruta
        type='http', así que la procedencia se comprueba a mano;
      - un mismo origen tiene un cupo de señales por ventana;
      - el nombre de la señal se valida contra la lista cerrada del modelo;
      - los campos libres se recortan a 128 caracteres y se les neutraliza
        el prefijo de fórmula;
      - una misma señal del mismo visitante no se repite dentro de 10
        minutos, lo que de paso mantiene limpia la tabla;
      - no se crea el visitante desde aquí (sería crear cookies desde un
        beacon): si todavía no existe, la señal se guarda sin él;
      - la respuesta es siempre vacía y siempre 200, para no dar pistas.

    Las dos primeras comprobaciones son de seguridad y van antes que nada,
    porque no tocan la base. La antirrepetición NO es un control de seguridad:
    es una regla de producto, y quien la evada se topa con el cupo.
    """

    @http.route('/ferba/evento', type='http', auth='public', website=True,
                methods=['POST'], csrf=False, sitemap=False)
    def evento(self, nombre=None, detalle=None, url=None, origen=None, campana=None, **kw):
        if (nombre in NOMBRES_VALIDOS and request.website
                and self._mismo_sitio() and self._dentro_del_cupo()):
            try:
                self._registrar(nombre, detalle, url, origen, campana)
            except Exception:
                # Una señal perdida no vale una página rota ni un 500 en los
                # registros del servidor. Se anota y se sigue.
                _logger.warning("Ferba: no se pudo guardar la señal %s", nombre, exc_info=True)
        return request.make_response('', headers=[('Content-Type', 'text/plain')])

    # ------------------------------------------------------------------
    #  Controles previos: baratos, sin tocar la base
    # ------------------------------------------------------------------
    @staticmethod
    def _mismo_sitio():
        """¿Viene la petición de una página de este sitio?

        Sustituye al token CSRF que sendBeacon no puede enviar. Sin esto, una
        página de cualquier otro dominio puede hacer un POST que el navegador
        acompaña con la cookie de sesión de la víctima; como la identidad del
        visitante se deriva en el servidor de esa cookie, la señal falsa
        quedaría atribuida al visitante víctima y, por los campos related
        almacenados, sellada con su contacto y su país.

        Se mira primero Sec-Fetch-Site, que es la señal hecha a propósito para
        esto: la pone el navegador, no se puede falsear desde la página que
        origina la petición y, a diferencia de Origin, no la altera la política
        de referente del sitio. Solo vale «same-origin»: «same-site» NO basta,
        porque odoo.com no está en la Public Suffix List y eso haría pasar por
        propia a una página alojada en cualquier otro inquilino *.odoo.com.

        Para navegadores que no la mandan se compara Origin o Referer a mano.
        Si no viene ninguna de las tres, o el Origin es «null» (iframe aislado
        o política de referente estricta), se rechaza: perder una señal es
        barato, aceptarla falsificada no.
        """
        peticion = request.httprequest

        sitio = peticion.headers.get('Sec-Fetch-Site')
        if sitio:
            if sitio == 'same-origin':
                return True
            _logger.debug("Ferba: señal descartada, Sec-Fetch-Site=%r", sitio)
            return False

        procedencia = peticion.headers.get('Origin') or peticion.headers.get('Referer')
        if not procedencia:
            _logger.debug("Ferba: señal descartada, sin Sec-Fetch-Site, Origin ni Referer")
            return False
        try:
            ajeno = urlsplit(procedencia).hostname
        except ValueError:
            return False
        propio = (peticion.host or '').split(':')[0]
        if not ajeno or not propio or ajeno != propio:
            _logger.debug("Ferba: señal descartada, procedencia %r no es %r", ajeno, propio)
            return False
        return True

    @staticmethod
    def _dentro_del_cupo():
        """Token bucket en memoria por (IP, agente), con ventana deslizante.

        Deliberadamente NO se guarda en la base: un contador de abuso no debe
        crear filas, que es justo el recurso que se quiere proteger.
        """
        peticion = request.httprequest
        semilla = b'|'.join([
            _cupo_sal,
            (peticion.remote_addr or '').encode('utf-8', 'replace'),
            (peticion.headers.get('User-Agent') or '').encode('utf-8', 'replace'),
        ])
        clave = hashlib.sha256(semilla).digest()[:16]

        ahora = time.time()
        with _cupo_cerrojo:
            if len(_cupo) > CUPO_MAX_CLAVES:
                caducadas = [k for k, (inicio, _n) in _cupo.items()
                             if ahora - inicio > CUPO_VENTANA]
                for k in caducadas:
                    del _cupo[k]
                if len(_cupo) > CUPO_MAX_CLAVES:
                    # Bajo presión se prefiere olvidar a crecer sin tope.
                    _cupo.clear()
            inicio, cuenta = _cupo.get(clave, (ahora, 0))
            if ahora - inicio > CUPO_VENTANA:
                inicio, cuenta = ahora, 0
            cuenta += 1
            _cupo[clave] = (inicio, cuenta)

        if cuenta > CUPO_SENALES:
            _logger.debug("Ferba: señal descartada, cupo agotado para este origen")
            return False
        return True

    # ------------------------------------------------------------------
    #  Registro
    # ------------------------------------------------------------------
    def _registrar(self, nombre, detalle, url, origen, campana):
        # El equipo de Ferba y quien edite el sitio no son visitantes: si se
        # midieran, las primeras semanas de datos serían nuestras propias
        # visitas de prueba.
        if request.env.user.has_group('base.group_user'):
            return

        visitante = request.env['website.visitor'].sudo()._get_visitor_from_request()

        valores = {
            'nombre': nombre,
            'detalle': self._recortar(detalle),
            'url': self._recortar(url),
            'origen': self._recortar(origen),
            'campana': self._recortar(campana),
            'visitor_id': visitante.id if visitante else False,
        }

        if visitante and self._repetida(valores):
            return

        request.env['ferba.evento'].sudo().create(valores)

    def _repetida(self, valores):
        """¿Ya llegó esta misma señal hace un momento?

        Es una regla de PRODUCTO, no un control de seguridad: evita que el
        visitante que hace clic tres veces en WhatsApp aparezca como tres
        oportunidades. Su clave incluye `detalle`, que lo envía el cliente, de
        modo que variándolo se evade; eso es aceptable porque quien lo haga
        sigue topándose con _dentro_del_cupo(), que no depende de ningún campo
        que el cliente controle.
        """
        limite = fields.Datetime.subtract(fields.Datetime.now(), seconds=VENTANA_REPETIDO)
        return bool(request.env['ferba.evento'].sudo().search_count([
            ('visitor_id', '=', valores['visitor_id']),
            ('nombre', '=', valores['nombre']),
            ('detalle', '=', valores['detalle']),
            ('create_date', '>=', limite),
        ], limit=1))

    @staticmethod
    def _recortar(texto):
        if not texto:
            return False
        limpio = str(texto)
        if limpio.startswith(PREFIJOS_FORMULA):
            # El apóstrofo es la convención que la propia exportación CSV de
            # Odoo usa: la celda se lee como texto y el apóstrofo no se
            # imprime. Se recorta uno menos para no pasar de LARGO_MAX al
            # añadirlo.
            return "'" + limpio[:LARGO_MAX - 1]
        return limpio[:LARGO_MAX]
