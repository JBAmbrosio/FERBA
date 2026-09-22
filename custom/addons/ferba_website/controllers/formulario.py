# -*- coding: utf-8 -*-
"""Controles de servidor para el formulario de contacto.

El formulario de views/ferba_contacto.xml publica contra la ruta genérica de
Odoo, /website/form/, que no impone tamaño, tipo ni número de ficheros. El
único tope de toda la cadena es el DEFAULT_MAX_CONTENT_LENGTH de 128 MiB de
todo el proceso: 6,4 veces lo que la propia página le promete al visitante en
views/ferba_contacto.xml:251-253 («PDF, JPG, PNG, MP4, máx. 20 MB»). Un
`accept` de HTML es una pista para el selector de ficheros que nunca viaja por
el cable, y el texto de ayuda es prosa.

Aquí se re-declara esa ruta para que el servidor exija lo que la página dice.
Dos capas, porque hacen cosas distintas:

  1. `max_content_length` en la ruta. Werkzeug rechaza el cuerpo ANTES de
     leerlo, así que una subida enorme no llega a costar memoria ni disco.
     Es el techo duro. Odoo ya trae el mecanismo (odoo/http.py lo aplica en
     pre_dispatch); la ruta de formularios simplemente no lo usaba.
  2. Comprobación explícita de extensión, tamaño y número. Es la que produce
     un mensaje legible junto al formulario para los casos normales, dentro
     del techo anterior.

No se toca la lógica del framework: se valida antes y se delega en super().
Odoo re-declarando una ruta en una subclase FUSIONA el diccionario de routing
con el del padre en vez de reemplazarlo, así que la ruta, el modelo, el
auth='public', el csrf=False y el captcha del padre se conservan intactos.

Nota honesta de alcance: esto acota el coste POR PETICIÓN. No acota el NÚMERO
de peticiones. El límite de tasa y el captcha son configuración del
despliegue, no de este módulo, y siguen pendientes en el informe.
"""
import json
import os

from odoo import http
from odoo.http import request

from odoo.addons.website.controllers.form import WebsiteForm

MAX_ADJUNTO = 20 * 1024 * 1024        # lo que la página promete
MAX_PETICION = 21 * 1024 * 1024       # el adjunto más el resto del formulario
MAX_FICHEROS = 1
EXTENSIONES = ('.pdf', '.jpg', '.jpeg', '.png', '.mp4')


class FormularioFerba(WebsiteForm):

    @http.route(max_content_length=MAX_PETICION)
    def website_form(self, model_name, **kwargs):
        problema = self._ferba_revisar_adjuntos()
        if problema:
            # Misma forma que usa el controlador padre para UserError, que es
            # lo que el snippet de Odoo sabe mostrar junto al formulario. No
            # se puede dejar escapar la excepción: el try/except del padre
            # está DENTRO de website_form y no cubre lo que pase antes.
            return json.dumps({'error': problema})
        return super().website_form(model_name, **kwargs)

    @staticmethod
    def _ferba_revisar_adjuntos():
        """Devuelve el mensaje de error, o None si los ficheros son aceptables."""
        adjuntos = [
            fichero
            for _campo, fichero in request.httprequest.files.items(multi=True)
            # Un input de fichero vacío también viaja: se ignora, no se rechaza.
            if fichero is not None and fichero.filename
        ]
        if not adjuntos:
            return None

        if len(adjuntos) > MAX_FICHEROS:
            return "Solo se puede adjuntar un archivo por solicitud."

        for fichero in adjuntos:
            nombre = fichero.filename
            if any(c in nombre for c in ('/', '\\', '\x00')):
                return "El nombre del archivo no es válido."

            extension = os.path.splitext(nombre)[1].lower()
            if extension not in EXTENSIONES:
                return ("Formato no admitido. Se aceptan PDF, JPG, PNG y MP4.")

            # content_length no es fiable en multipart; se mide el flujo y se
            # rebobina, porque insert_attachment lo va a leer después.
            flujo = fichero.stream
            flujo.seek(0, os.SEEK_END)
            tamano = flujo.tell()
            flujo.seek(0)
            if tamano > MAX_ADJUNTO:
                return ("El archivo pesa %.1f MB y el máximo son 20 MB."
                        % (tamano / (1024.0 * 1024.0)))

        return None
