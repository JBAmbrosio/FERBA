"""Veredicto por IA ante actividad sospechosa: la IA vive en Odoo, el agente
solo manda capturas.

QUE ES
    Foco mide en cada equipo patrones que PUEDEN ser actividad simulada para
    acumular horas: pantalla sin cambio con input, solo mouse sin teclado, clics
    a ritmo regular, input inyectado por software. Cuando un patron dura T
    minutos (Ajustes, 15 de fabrica), el agente abre un EPISODIO, toma unas
    pocas capturas separadas por minutos y las sube aqui. Odoo arma el contexto
    (puesto, app, duracion, senales) y le pide al modelo de vision que diga QUE
    SE VE y si es trabajo real. El resultado es un veredicto con confianza y
    motivo, nunca una sancion: queda como EVIDENCIA para que un administrador
    revise, confirme o descarte, y deje constancia.

POR QUE LA IA VIVE AQUI Y NO EN EL AGENTE
    La precision es la misma (el modelo es el mismo y ve la misma imagen), y
    una llave embebida en 30 equipos no se puede proteger: cualquier cifrado se
    abre con lo que el propio agente necesita para usarla. Aqui la llave es un
    parametro del sistema, revocarla es cambiar un parametro, y el contexto que
    hace util al modelo (puesto, catalogo, senales, horario) ya esta en Odoo.

EN QUE SE DISTINGUE DE foco.capture
    `foco.capture` dice que no se captura «al detectar distraccion» porque el
    disparador seria una clasificacion que puede estar mal. Aqui el disparador
    no es una clasificacion sino una MEDIDA (T minutos de un patron fisico), la
    decision la ordeno la direccion y esta escrita en la politica de uso de
    equipos, y el modelo esta instruido sobre todo para ABSOLVER: un viewport
    de SOLIDWORKS quieto, un plano en PDF o un render que corre son trabajo
    aunque la pantalla no cambie (medido el 7-oct: 57 min de «pantalla sin
    cambio» de un disenador eran SOLIDWORKS y Explorador, trabajo legitimo).

RETENCION
    Las capturas se CONSERVAN como evidencia (decision del 7-oct-2026). Un
    plazo en Ajustes (0 = nunca) permite purgarlas si un dia se decide.
"""
import base64
import json
import logging
import uuid
from datetime import timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from odoo.tools.image import image_process
except Exception:  # pragma: no cover - sin PIL no se redimensiona, se manda tal cual
    image_process = None

PATRONES = [
    ('pantalla_sin_cambio', 'Pantalla sin cambio con input'),
    ('solo_mouse', 'Solo mouse, sin teclado'),
    ('clics_ritmicos', 'Clics a ritmo regular'),
    ('input_inyectado', 'Input inyectado por software'),
    ('mixto', 'Varias senales a la vez'),
]

# Como se le explica el patron al modelo. El %d es el umbral en minutos.
PATRON_TEXTO = {
    'pantalla_sin_cambio': 'hubo teclado o mouse pero la pantalla no cambio durante %d minutos o mas',
    'solo_mouse': 'actividad solo con el mouse, sin una sola tecla, durante %d minutos o mas',
    'clics_ritmicos': 'clics repetidos a ritmo regular, sin cambio de ventana ni de pantalla, '
                      'durante %d minutos o mas',
    'input_inyectado': 'Windows marco el input como inyectado por software (sin teclado ni mouse '
                       'fisicos) durante %d minutos o mas',
    'mixto': 'varias senales de actividad simulada a la vez (solo mouse, pantalla sin cambio, '
             'input inyectado) durante %d minutos o mas',
}

VEREDICTOS = [
    ('trabajo_real', 'Trabajo real'),
    ('revision_pasiva', 'Revision pasiva (lee, mide, espera un proceso)'),
    ('entretenimiento', 'Entretenimiento (video, deportes, redes)'),
    ('personal', 'Asunto personal (chat, compras, tramites)'),
    ('inactiva', 'Pantalla inactiva (bloqueo, escritorio vacio)'),
    ('simulacion', 'Actividad simulada (jiggler o similar)'),
    ('indeterminado', 'Indeterminado'),
]
SOSPECHOSOS = ('entretenimiento', 'personal', 'inactiva', 'simulacion')

ESTADOS = [
    ('abierto', 'Episodio en curso'),
    ('pendiente', 'Por analizar'),
    ('analizado', 'Analizado'),
    ('sin_capturas', 'Sin capturas'),
    ('error', 'Error al analizar'),
]

REVISION = [
    ('pendiente', 'Sin revisar'),
    ('confirmado', 'Confirmado por un administrador'),
    ('descartado', 'Descartado (falso positivo)'),
]

CAMBIO = [
    ('una_sola', 'Una sola captura'),
    ('identicas', 'Identicas'),
    ('cambios_menores', 'Cambios menores'),
    ('cambios_claros', 'Cambios claros'),
]

# Tope duro de capturas por episodio, pase lo que pase en Ajustes o en el
# agente: mas imagenes no mejoran el veredicto y si el costo.
MAX_CAPTURAS_DURO = 12
MAX_REINTENTOS = 5
# Lado mayor con el que se manda la imagen al modelo. Una pantalla 4K a tamano
# real no aporta nada en detalle «low» y pesa 10 veces mas.
LADO_MAX_API = 1600

SISTEMA_VISION = (
    "Eres el revisor de evidencia de pantalla de {contexto} Foco, el monitor de productividad "
    "de la empresa, detecto en la computadora de un empleado un patron que PUEDE ser actividad "
    "simulada para acumular horas: {patron}. Te doy el puesto de la persona, la aplicacion al "
    "frente, cuanto duro, las senales medidas y entre 1 y {n} capturas de pantalla tomadas "
    "durante ese rato, en orden. Tu trabajo es decir que se ve y si es trabajo real.\n"
    "Reglas:\n"
    "1. Primero describe QUE SE VE en 'que_se_ve' (maximo 60 palabras): el tipo de contenido y "
    "de ventana, sin nombres de personas, numeros, correos ni datos privados.\n"
    "2. Veredictos: 'trabajo_real' (edita, dibuja, escribe, navega contenido del trabajo); "
    "'revision_pasiva' (lee planos, PDF, hojas, correo, espera un render o un calculo, esta en "
    "videollamada o tiene una pantalla de referencia abierta: es trabajo aunque no cambie); "
    "'entretenimiento' (video, streaming, deportes, musica al frente, redes sociales, noticias "
    "ajenas al trabajo); 'personal' (chat o sitios personales, compras, tramites propios); "
    "'inactiva' (pantalla de bloqueo, protector, escritorio vacio, inicio de sesion, ventana sin "
    "contenido); 'simulacion' (las capturas son identicas o solo se mueve el cursor sobre algo que "
    "no requiere atencion, con senales de input que no producen ningun cambio: patron de "
    "jiggler); 'indeterminado' (no se puede saber con lo que hay).\n"
    "3. 'contenido_de_trabajo' es true si las capturas muestran contenido de trabajo: un modelo o "
    "plano en CAD, una hoja de calculo, un documento, un PDF tecnico, correo, el ERP, un chat o "
    "una pagina de trabajo, un render o calculo en curso.\n"
    "4. REGLA DURA: si 'contenido_de_trabajo' es true, el veredicto NO puede ser 'simulacion' "
    "aunque las capturas sean identicas y aunque las senales digan solo mouse o pantalla sin "
    "cambio. Los programas de CAD e ingenieria (SOLIDWORKS, AutoCAD, EPLAN, Inventor), las hojas, "
    "los planos, los PDF y los renders quedan quietos MUCHOS minutos mientras la persona piensa, "
    "mide o revisa, y ese rato produce exactamente esas senales: es 'revision_pasiva' (o "
    "'trabajo_real' si hay cambios). El patron que disparo el episodio ya lo sabemos; tu "
    "aportas lo que esta EN la pantalla.\n"
    "5. 'simulacion' se reserva para cuando NO hay contenido de trabajo en pantalla (escritorio "
    "vacio, ventana sin contenido, protector, una pagina estatica sin relacion con el trabajo) y "
    "aun asi hubo input que no cambio nada. La duda se resuelve hacia 'indeterminado', nunca "
    "hacia 'simulacion'.\n"
    "6. 'capturas_cambian' dice si las capturas son identicas, con cambios menores (cursor, "
    "reloj, scroll) o con cambios claros de contenido; 'una_sola' si solo hay una.\n"
    "7. 'confianza' va de 0 a 1. 'motivo' tiene maximo 20 palabras. Responde en espanol."
)

ESQUEMA_VISION = {
    'name': 'veredicto_pantalla',
    'strict': True,
    'schema': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'que_se_ve': {'type': 'string'},
            'contenido_de_trabajo': {'type': 'boolean'},
            'veredicto': {'type': 'string', 'enum': [v for v, _ in VEREDICTOS]},
            'confianza': {'type': 'number'},
            'motivo': {'type': 'string'},
            'capturas_cambian': {'type': 'string', 'enum': [v for v, _ in CAMBIO]},
        },
        'required': ['que_se_ve', 'contenido_de_trabajo', 'veredicto', 'confianza', 'motivo',
                     'capturas_cambian'],
    },
}


def _mime_de(b64):
    """El tipo de imagen por la firma del base64 (sin decodificar)."""
    s = b64[:8] if isinstance(b64, str) else b64[:8].decode('ascii', 'ignore')
    if s.startswith('iVBOR'):
        return 'image/png'
    if s.startswith('UklGR'):
        return 'image/webp'
    if s.startswith('R0lGOD'):
        return 'image/gif'
    return 'image/jpeg'


class FocoIntegrityCapture(models.Model):
    _name = 'foco.integrity.capture'
    _description = 'Captura de un episodio sospechoso'
    _order = 'taken_at asc, seq asc, id asc'

    verdict_id = fields.Many2one('foco.integrity.verdict', string='Episodio', required=True,
                                 ondelete='cascade', index=True)
    seq = fields.Integer(string='Nº', default=1)
    taken_at = fields.Datetime(string='Tomada', default=fields.Datetime.now)
    image = fields.Binary(string='Imagen', attachment=True)
    frame_hash = fields.Char(string='Huella', help='Hash del cuadro, para no guardar dos iguales.')
    bytes = fields.Integer(string='Peso (bytes)', readonly=True)
    width = fields.Integer(readonly=True)
    height = fields.Integer(readonly=True)
    exe = fields.Char(string='Ejecutable al frente')
    title = fields.Char(string='Titulo de la ventana')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            img = vals.get('image')
            if img and not vals.get('bytes'):
                try:
                    vals['bytes'] = len(base64.b64decode(img))
                except Exception:
                    pass
        return super().create(vals_list)

    def _para_api(self):
        """(mime, base64) listo para el modelo: la imagen tal cual, o reducida
        a LADO_MAX_API si viene grande. Nunca falla: si no se puede reducir,
        se manda la original."""
        self.ensure_one()
        b64 = self.image or b''
        if isinstance(b64, bytes):
            b64 = b64.decode('ascii', 'ignore')
        mime = _mime_de(b64)
        grande = (self.bytes or 0) > 1200000 or (self.width or 0) > LADO_MAX_API
        if grande and image_process:
            try:
                raw = image_process(base64.b64decode(b64), size=(LADO_MAX_API, LADO_MAX_API),
                                    quality=80, output_format='JPEG')
                return 'image/jpeg', base64.b64encode(raw).decode('ascii')
            except Exception as e:
                _logger.info('foco.integrity.capture %s: no se redimensiono (%s)', self.id, e)
        return mime, b64


class FocoIntegrityVerdict(models.Model):
    _name = 'foco.integrity.verdict'
    _description = 'Veredicto de la IA sobre un episodio sospechoso'
    _order = 'started_at desc, id desc'

    _uniq = models.Constraint('unique(computer_id, ref)', 'Ese episodio ya estaba registrado.')

    computer_id = fields.Many2one('foco.computer', string='Equipo', required=True,
                                  ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='computer_id.employee_id', store=True,
                                  string='Empleado', index=True)
    department_id = fields.Many2one(related='employee_id.department_id', store=True,
                                    string='Departamento')
    ref = fields.Char(string='Referencia', required=True, index=True,
                      default=lambda self: uuid.uuid4().hex,
                      help='La que le puso el agente al abrir el episodio. Con ella los '
                           'reenvios no duplican nada.')
    pattern = fields.Selection(PATRONES, string='Patron detectado', required=True,
                               default='pantalla_sin_cambio', index=True)
    app_id = fields.Many2one('foco.app', string='Aplicacion (catalogo)', ondelete='set null')
    app_name = fields.Char(string='Aplicacion al frente')
    exe = fields.Char(string='Ejecutable')
    title = fields.Char(string='Titulo de la ventana')
    started_at = fields.Datetime(string='Inicio', required=True, default=fields.Datetime.now,
                                 index=True)
    ended_at = fields.Datetime(string='Fin')
    duration_min = fields.Float(string='Duracion (min)', digits=(8, 1))
    senales = fields.Text(string='Senales del agente (JSON)',
                          help='Lo que midio el agente en el episodio: segundos sin tecla, sin '
                               'cambio de pantalla, clics, input inyectado, cuadros iguales.')
    state = fields.Selection(ESTADOS, string='Estado', default='abierto', index=True)

    capture_ids = fields.One2many('foco.integrity.capture', 'verdict_id', string='Capturas')
    capture_count = fields.Integer(string='Capturas', compute='_compute_capture_count')

    veredicto = fields.Selection(VEREDICTOS, string='Veredicto de la IA', index=True)
    confianza = fields.Float(string='Confianza', digits=(3, 2))
    motivo = fields.Char(string='Motivo', help='Maximo 20 palabras, lo dice el modelo.')
    que_se_ve = fields.Text(string='Que se ve')
    capturas_cambian = fields.Selection(CAMBIO, string='Las capturas')
    contenido_de_trabajo = fields.Boolean(
        string='Hay contenido de trabajo en pantalla',
        help='Lo dice el modelo mirando las capturas. Con esto en si, el veredicto '
             'nunca es «simulacion»: una pantalla de trabajo quieta es revision, no '
             'un jiggler. La regla se aplica ademas en el servidor.')
    ajustado = fields.Boolean(
        string='Veredicto ajustado por regla', readonly=True,
        help='El modelo dijo «simulacion» con contenido de trabajo en pantalla (o '
             'en una app que el catalogo marca productiva) y el servidor lo bajo a '
             '«revision pasiva». Queda a la vista para auditar al modelo.')
    sospechoso = fields.Boolean(string='No parece trabajo', compute='_compute_sospechoso',
                                store=True, index=True)
    modelo = fields.Char(string='Modelo')
    tokens = fields.Integer(string='Tokens del modelo')
    decided_at = fields.Datetime(string='Analizado el')
    retries = fields.Integer(string='Reintentos', default=0)
    error_detail = fields.Char(string='Ultimo error')

    # Revision humana: la IA aporta evidencia; la decision y la constancia son
    # de una persona con nombre y fecha.
    review_outcome = fields.Selection(REVISION, string='Revision', default='pendiente', index=True)
    reviewed_by = fields.Many2one('res.users', string='Reviso', readonly=True)
    reviewed_at = fields.Datetime(string='Revisado el', readonly=True)
    review_note = fields.Text(string='Nota de la revision')

    @api.depends('capture_ids')
    def _compute_capture_count(self):
        for r in self:
            r.capture_count = len(r.capture_ids)

    @api.depends('veredicto')
    def _compute_sospechoso(self):
        for r in self:
            r.sospechoso = r.veredicto in SOSPECHOSOS

    @api.depends('employee_id', 'computer_id', 'pattern', 'started_at')
    def _compute_display_name(self):
        etiquetas = dict(PATRONES)
        for r in self:
            quien = r.employee_id.name or r.computer_id.name or '?'
            cuando = fields.Datetime.to_string(r.started_at)[:16] if r.started_at else ''
            r.display_name = '%s · %s · %s' % (quien, etiquetas.get(r.pattern, r.pattern or ''), cuando)

    # ------------------------------------------------------------ desde el agente
    @api.model
    def _registrar(self, computer, ref, data):
        """Crea o completa el episodio `ref` de ese equipo con lo que trae el
        envio. Idempotente: un reenvio no duplica capturas (por huella o por
        numero de secuencia). Devuelve (registro, capturas nuevas)."""
        settings = self.env['foco.settings'].sudo().get_settings()
        Event = self.env['foco.event'].sudo()
        rec = self.search([('computer_id', '=', computer.id), ('ref', '=', ref)], limit=1)
        patron = data.get('patron') if data.get('patron') in dict(PATRONES) else 'mixto'
        exe = (data.get('exe') or '').strip()[:255]
        app = self.env['foco.app'].sudo()._por_exe(exe.lower()) if exe else self.env['foco.app']
        started = Event._parse_utc(data.get('started_at'))
        ended = Event._parse_utc(data.get('ended_at'))
        senales = data.get('senales')
        if isinstance(senales, (dict, list)):
            senales = json.dumps(senales, ensure_ascii=False)
        elif senales is not None:
            senales = str(senales)[:4000]
        try:
            duracion = float(data.get('duracion_min') or 0.0)
        except (TypeError, ValueError):
            duracion = 0.0
        if not rec:
            rec = self.create({
                'computer_id': computer.id, 'ref': ref, 'pattern': patron,
                'app_id': app.id or False,
                'app_name': (data.get('app') or app.display_name or exe or '')[:255],
                'exe': exe or False, 'title': (data.get('titulo') or '')[:255] or False,
                'started_at': started or fields.Datetime.now(), 'ended_at': ended or False,
                'duration_min': duracion, 'senales': senales or False, 'state': 'abierto',
            })
        else:
            cambios = {}
            if ended and ended != rec.ended_at:
                cambios['ended_at'] = ended
            if duracion and duracion != rec.duration_min:
                cambios['duration_min'] = duracion
            if senales:
                cambios['senales'] = senales
            if data.get('titulo') and not rec.title:
                cambios['title'] = data.get('titulo')[:255]
            if patron != 'mixto' and rec.pattern != patron and rec.pattern == 'mixto':
                cambios['pattern'] = patron
            if cambios:
                rec.write(cambios)
        tope = min(max(int(settings.vision_max_capturas or 4), 1), MAX_CAPTURAS_DURO)
        huellas = {c.frame_hash for c in rec.capture_ids if c.frame_hash}
        secuencias = {c.seq for c in rec.capture_ids}
        nuevas = []
        for c in data.get('capturas') or []:
            if not isinstance(c, dict):
                continue
            b64 = c.get('jpeg_b64') or c.get('image') or ''
            if not b64:
                continue
            try:
                seq = int(c.get('seq') or 0)
            except (TypeError, ValueError):
                seq = 0
            huella = (c.get('hash') or '').strip()[:64]
            if (huella and huella in huellas) or (seq and seq in secuencias):
                continue
            if len(rec.capture_ids) + len(nuevas) >= tope:
                break
            try:
                peso = len(base64.b64decode(b64))
            except Exception:
                continue
            nuevas.append({
                'verdict_id': rec.id, 'seq': seq or (len(rec.capture_ids) + len(nuevas) + 1),
                'taken_at': Event._parse_utc(c.get('at')) or fields.Datetime.now(),
                'image': b64, 'frame_hash': huella or False, 'bytes': peso,
                'width': int(c.get('w') or 0), 'height': int(c.get('h') or 0),
                'exe': (c.get('exe') or '')[:255] or False,
                'title': (c.get('titulo') or '')[:255] or False,
            })
            if huella:
                huellas.add(huella)
            secuencias.add(nuevas[-1]['seq'])
        if nuevas:
            self.env['foco.integrity.capture'].sudo().create(nuevas)
        if data.get('cerrar') and rec.state == 'abierto':
            rec.write({'state': 'pendiente', 'ended_at': rec.ended_at or fields.Datetime.now()})
        return rec, len(nuevas)

    # ------------------------------------------------------------ el analisis
    def action_analizar(self):
        """Boton de la ficha: (re)pide el veredicto. Sirve para probar a mano
        subiendo capturas sin agente, y para repetir con otro modelo."""
        for rec in self:
            if rec.state == 'abierto':
                rec.write({'state': 'pendiente', 'ended_at': rec.ended_at or fields.Datetime.now()})
        self._analizar(forzar=True)
        malos = self.filtered(lambda r: r.state == 'error')
        if malos:
            raise UserError('No se pudo analizar: %s' % (malos[0].error_detail or 'error'))
        return True

    def _contexto_usuario(self, settings):
        self.ensure_one()
        zona = settings._tzinfo_for(self.employee_id, self.computer_id) or pytz.UTC

        def local(dt):
            return pytz.UTC.localize(dt).astimezone(zona).strftime('%Y-%m-%d %H:%M') if dt else '?'

        emp = self.employee_id
        puesto = (emp.job_title or (emp.job_id.name if emp.job_id else '') or 'sin puesto registrado')
        depto = emp.department_id.name or 'sin departamento'
        duracion = self.duration_min
        if not duracion and self.started_at and self.ended_at:
            duracion = (self.ended_at - self.started_at).total_seconds() / 60.0
        caps = self.capture_ids.sorted(lambda c: (c.taken_at or self.started_at, c.seq, c.id))
        horas = ', '.join(local(c.taken_at)[-5:] for c in caps)
        senales = (self.senales or '').strip() or '(no se mandaron senales)'
        if len(senales) > 1500:
            senales = senales[:1500] + '...'
        cat = self.app_id.category_id
        if cat:
            catalogo = '%s (peso de productividad %.1f)' % (cat.name, cat.weight or 0.0)
        elif self.app_id:
            catalogo = 'sin clasificar todavia'
        else:
            catalogo = 'no esta en el catalogo'
        return (
            'PUESTO DE LA PERSONA: %s (%s)\n'
            'PATRON DETECTADO: %s\n'
            'APLICACION AL FRENTE: %s%s%s\n'
            'COMO LA CLASIFICA EL CATALOGO DE LA EMPRESA: %s\n'
            'DURACION: %.0f min, de %s a %s (hora local)\n'
            'SENALES DEL AGENTE: %s\n'
            'CAPTURAS: %d, tomadas a las %s, en el orden en que te las doy.'
        ) % (puesto, depto, dict(PATRONES).get(self.pattern, self.pattern),
             self.app_name or self.app_id.display_name or self.exe or 'desconocida',
             (' (%s)' % self.exe) if self.exe and self.exe != self.app_name else '',
             (' — titulo de la ventana: %s' % self.title) if self.title else '',
             catalogo, duracion or 0, local(self.started_at), local(self.ended_at),
             senales, len(caps), horas or '?')

    def _app_productiva(self):
        """La app del episodio esta en el catalogo con peso de productividad
        alto (1.0 = productiva). Es el juicio del administrador sobre la app,
        y vale como contenido de trabajo aunque el modelo no lo reconozca."""
        self.ensure_one()
        cat = self.app_id.category_id
        return bool(cat and not cat.is_system and (cat.weight or 0.0) >= 0.9)

    def _analizar(self, forzar=False):
        settings = self.env['foco.settings'].sudo().get_settings()
        OpenAI = self.env['foco.openai']
        T = int(settings.vision_umbral_minutos or 15)
        for rec in self:
            if rec.state == 'analizado' and not forzar:
                continue
            caps = rec.capture_ids.sorted(lambda c: (c.taken_at or rec.started_at, c.seq, c.id))
            if not caps:
                rec.write({'state': 'sin_capturas', 'veredicto': 'indeterminado', 'confianza': 0.0,
                           'motivo': 'sin capturas que mirar', 'decided_at': fields.Datetime.now()})
                continue
            if not OpenAI.configurado():
                rec.write({'state': 'error', 'retries': rec.retries + 1,
                           'error_detail': 'Falta la API key de OpenAI en Ajustes.'})
                continue
            contexto = (settings.call_review_context or '').strip()
            if contexto and not contexto.endswith('.'):
                contexto += '.'
            sistema = SISTEMA_VISION.format(
                contexto=contexto or 'la empresa.',
                patron=PATRON_TEXTO.get(rec.pattern, PATRON_TEXTO['mixto']) % T,
                n=MAX_CAPTURAS_DURO)
            try:
                imagenes = [c._para_api() for c in caps[:MAX_CAPTURAS_DURO]]
                res = OpenAI.vision(imagenes, sistema, rec._contexto_usuario(settings),
                                    ESQUEMA_VISION, detail=settings.vision_detalle or 'low')
            except Exception as e:
                rec.write({'state': 'error', 'retries': rec.retries + 1,
                           'error_detail': str(e)[:240]})
                _logger.warning('foco.integrity.verdict %s: no se pudo analizar (%s)', rec.ref, e)
                continue
            j = res.get('json') or {}
            veredicto = j.get('veredicto') if j.get('veredicto') in dict(VEREDICTOS) else 'indeterminado'
            cambian = j.get('capturas_cambian') if j.get('capturas_cambian') in dict(CAMBIO) else False
            if len(caps) == 1:
                cambian = 'una_sola'
            try:
                confianza = min(max(float(j.get('confianza') or 0.0), 0.0), 1.0)
            except (TypeError, ValueError):
                confianza = 0.0
            motivo = (j.get('motivo') or '').strip()
            contenido = bool(j.get('contenido_de_trabajo'))
            # REGLA EN EL SERVIDOR, no solo en el prompt (medido el 7-oct: con la
            # regla solo en el prompt, gpt-4o dio «simulacion 0.9» a dos capturas
            # identicas de SOLIDWORKS con un ensamble abierto). Una pantalla con
            # contenido de trabajo, o una app que el catalogo marca productiva,
            # quieta durante T minutos es revision, no un jiggler. El ajuste
            # queda a la vista para auditar al modelo.
            ajustado = False
            if veredicto == 'simulacion' and (contenido or rec._app_productiva()):
                veredicto, ajustado = 'revision_pasiva', True
                motivo = ('Ajustado: hay contenido de trabajo en pantalla; quieta no es simulada. '
                          + motivo)
            rec.write({
                'state': 'analizado', 'veredicto': veredicto, 'confianza': confianza,
                'motivo': motivo[:160] or False, 'contenido_de_trabajo': contenido,
                'ajustado': ajustado,
                'que_se_ve': (j.get('que_se_ve') or '').strip()[:2000] or False,
                'capturas_cambian': cambian, 'modelo': res.get('modelo') or False,
                'tokens': int(res.get('tokens') or 0), 'decided_at': fields.Datetime.now(),
                'error_detail': False,
            })
            rec._rehacer_hecho()

    # ------------------------------------------------------------ hecho de integridad
    def _dia_local(self, settings=None):
        self.ensure_one()
        settings = settings or self.env['foco.settings'].sudo().get_settings()
        zona = settings._tzinfo_for(self.employee_id, self.computer_id) or pytz.UTC
        return pytz.UTC.localize(self.started_at).astimezone(zona).date() if self.started_at else False

    def _rehacer_hecho(self):
        """El hecho de integridad 'veredicto_ia' del dia de esa persona se
        rehace en cuanto hay veredicto o revision, sin esperar al cron de 6 h:
        el tablero y Tomy leen los hechos, no los episodios."""
        if 'foco.integrity.fact' not in self.env:
            return
        settings = self.env['foco.settings'].sudo().get_settings()
        Fact = self.env['foco.integrity.fact'].sudo()
        for rec in self:
            dia = rec._dia_local(settings) if rec.employee_id else False
            if not dia:
                continue
            try:
                Fact.rebuild(rec.employee_id, [dia])
            except Exception as e:
                _logger.warning('foco.integrity.verdict %s: no se rehizo el hecho (%s)', rec.ref, e)

    def unlink(self):
        # Borrar un episodio tiene que borrar lo que el hecho decia de el.
        pares = [(r.employee_id, r._dia_local()) for r in self if r.employee_id and r.started_at]
        res = super().unlink()
        if pares and 'foco.integrity.fact' in self.env:
            Fact = self.env['foco.integrity.fact'].sudo()
            for emp, dia in {(e.id, d): (e, d) for e, d in pares}.values():
                try:
                    Fact.rebuild(emp, [dia])
                except Exception as e:
                    _logger.warning('foco.integrity.verdict: no se rehizo el hecho al borrar (%s)', e)
        return res

    # ------------------------------------------------------------ revision humana
    def action_confirmar(self):
        self._revisar('confirmado')
        return True

    def action_descartar(self):
        self._revisar('descartado')
        return True

    def action_reabrir_revision(self):
        self._revisar('pendiente')
        return True

    def _revisar(self, resultado):
        self.write({'review_outcome': resultado,
                    'reviewed_by': self.env.user.id if resultado != 'pendiente' else False,
                    'reviewed_at': fields.Datetime.now() if resultado != 'pendiente' else False})
        # Descartar un episodio lo saca del hecho; confirmarlo lo deja marcado.
        self._rehacer_hecho()

    # ------------------------------------------------------------ cron
    @api.model
    def _cron_pendientes(self):
        """Cierra los episodios que el agente dejo abiertos, analiza lo que
        espera y reintenta lo que fallo. Cada 5 min."""
        settings = self.env['foco.settings'].sudo().get_settings()
        ahora = fields.Datetime.now()
        T = int(settings.vision_umbral_minutos or 15)
        # Un episodio sin noticias en 2T minutos no va a recibir mas capturas
        # (agente muerto, equipo apagado): se analiza con lo que llego.
        tope = ahora - timedelta(minutes=2 * T)
        for rec in self.search([('state', '=', 'abierto'), ('write_date', '<', tope)]):
            rec.write({'state': 'pendiente', 'ended_at': rec.ended_at or rec.write_date or ahora})
        pendientes = self.search([('state', 'in', ('pendiente', 'error')),
                                  ('retries', '<', MAX_REINTENTOS)], order='started_at asc', limit=50)
        if pendientes:
            pendientes._analizar()
        # Purga opcional de capturas (0 = se conservan como evidencia).
        dias = int(settings.vision_retention_days or 0)
        if dias > 0:
            viejas = self.env['foco.integrity.capture'].sudo().search(
                [('taken_at', '<', ahora - timedelta(days=dias))], limit=500)
            if viejas:
                viejas.unlink()
