import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# El host de WhatsApp Web. Es lo que deja distinguir "WhatsApp en el navegador"
# de cualquier otra pestaña: el grupo abierto llega como el TITULO de la pestaña
# (document), y el sitio es este.
WA_WEB_HOST = 'web.whatsapp.com'

# Nombres que NO son un grupo: el titulo cuando no hay ningun chat abierto.
# WhatsApp Web pone "WhatsApp" a secas y la app de escritorio pone "WhatsApp"
# como titulo de ventana. Tratarlos como grupo meteria un "grupo" basura que se
# llevaria TODO el tiempo de WhatsApp sin chat.
_PLACEHOLDERS_WA = {'whatsapp', 'whatsapp web'}

# Contador de no leidos al inicio del titulo: "(3) Ventas" y "(5) Ventas" son el
# mismo grupo. Igual que en foco.tab.review.
_CONTADOR = re.compile(r'^\(\d+\)\s*')
# Sufijos que algunos titulos arrastran: " - WhatsApp", " • WhatsApp",
# " - WhatsApp Web". Se quitan para que el nombre del grupo quede limpio.
_SUFIJO_WA = re.compile(r'\s*[-•|]\s*whatsapp(?:\s*web)?\s*$', re.I)
# Chrome/Edge 154 decoran la pestaña dormida con "Uso de memoria: N MB".
_MEM_SUFIJO = re.compile(
    r'\s*[-:]\s*(?:uso de memoria|memory usage)\s*[-:]\s*[\d.,]+\s*[KMG]B\s*$', re.I)
# El titulo de ventana del navegador con VARIAS pestañas: "<pestaña activa> y N
# paginas mas" (es) / "<tab> and N more page(s)" (en). Es decoracion del navegador,
# no parte del nombre: medido en produccion "WhatsApp y 1 pagina mas" = WhatsApp
# sin chat abierto. Se quita para quedarse con el titulo real y que el placeholder
# lo reconozca (y para que un chat real con varias pestañas no arrastre el sufijo).
_SUFIJO_MASPESTANAS = re.compile(
    r'\s+(?:y\s+\d+\s+(?:p[aá]gina|p[aá]ginas|pesta[nñ]a|pesta[nñ]as)\s+m[aá]s'
    r'|and\s+\d+\s+more\s+(?:page|pages|tab|tabs))\s*$', re.I)


def normaliza_grupo(nombre):
    """El nombre del grupo/chat tal como se guarda y se compara. A nivel de
    modulo para poder probarla sin Odoo."""
    t = (nombre or '').strip()
    t = _CONTADOR.sub('', t)
    t = _MEM_SUFIJO.sub('', t)
    t = _SUFIJO_MASPESTANAS.sub('', t)
    t = _SUFIJO_WA.sub('', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:200]


def es_placeholder_wa(nombre):
    """`nombre` ya normalizado es el titulo sin chat abierto (no es un grupo). A
    nivel de modulo para poder probarla sin Odoo."""
    return (nombre or '').strip().lower() in _PLACEHOLDERS_WA


def app_es_whatsapp(exe, product, company):
    """¿La aplicacion del renglon es WhatsApp de ESCRITORIO? Se decide por lo que
    el binario declara ser, no por una lista de exes cocida: el exe de la version
    de la Microsoft Store cambia, pero 'whatsapp' aparece en el exe, el producto o
    el editor. A nivel de modulo para poder probarla sin Odoo."""
    for v in (exe, product, company):
        if 'whatsapp' in (v or '').strip().lower():
            return True
    return False


class FocoWhatsappGroup(models.Model):
    """Un grupo o chat de WhatsApp descubierto, clasificable como productivo o
    distraccion.

    POR QUE EXISTE (requisito de Francesco, reunion 9-oct-2026)
        WhatsApp se medía como UNA sola bolsa: o todo el tiempo en WhatsApp
        contaba como productivo o todo como distraccion. Pero el grupo de VENTAS
        es trabajo y un grupo de memes no. Este catalogo baja un nivel: el grupo
        ABIERTO (el titulo del chat) es a WhatsApp lo que un SITIO es al
        navegador, y su categoria MANDA sobre la de la app, igual que la del
        sitio manda sobre la del navegador.

    DE DONDE SALE EL NOMBRE
        - WhatsApp Web: el titulo de la pestaña (`document`) con host
          web.whatsapp.com.
        - WhatsApp de escritorio: el chat activo que el agente lee del arbol de
          accesibilidad (el titulo de ventana es solo "WhatsApp").
        NUNCA el contenido de los mensajes: solo el NOMBRE del grupo, igual que
        de un sitio solo se guarda el dominio. Debe estar en el aviso de
        privacidad que firma el personal.

    QUIEN CLASIFICA
        La IA (Claude) propone productivo/distraccion desde el nombre; el
        aprobador (Francesco) confirma con un clic, igual que las pestañas y las
        cotizaciones. No se auto-clasifica: el mismo grupo puede ser trabajo o no
        segun la empresa.
    """
    _name = 'foco.whatsapp.group'
    _description = 'Grupo o chat de WhatsApp descubierto'
    _order = 'last_seen desc'

    _name_uniq = models.Constraint('unique(name)',
                                   'Ese grupo de WhatsApp ya existe en el catalogo.')

    name = fields.Char(string='Grupo o chat', required=True, index=True)
    category_id = fields.Many2one(
        'foco.category', string='Categoria', ondelete='set null',
        help='La confirma el aprobador. Vacio = el tiempo de este grupo hereda la '
             'categoria de WhatsApp (sin clasificar). Clasificarlo solo AFINA: un '
             'grupo de ventas pasa a contar como productivo y uno de ocio como '
             'distraccion, sin tocar a los demas.')
    ia_clasificado = fields.Boolean(default=False, index=True)
    ia_suggestion = fields.Selection(
        [('productiva', 'Productivo'), ('distraccion', 'Distraccion'),
         ('revisar', 'Revisar')],
        string='Sugerencia de la IA', default='revisar')
    ia_reason = fields.Char(string='Motivo')
    ia_model = fields.Char(string='Modelo')
    first_seen = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_seen = fields.Datetime(readonly=True)
    classified = fields.Boolean(compute='_compute_classified', store=True, index=True)
    # Expuestos para que la lista pueda colorear productivo (verde) vs distraccion
    # (rojo) sin acceso punteado a category_id en la decoracion, que no siempre
    # se carga en la vista de lista.
    category_weight = fields.Float(related='category_id.weight', string='Peso')
    category_system = fields.Boolean(related='category_id.is_system', string='Sistema')
    decided_uid = fields.Many2one('res.users', string='Clasifico', readonly=True)
    decided_at = fields.Datetime(string='Clasificado', readonly=True)

    @api.depends('category_id')
    def _compute_classified(self):
        for rec in self:
            rec.classified = bool(rec.category_id)

    # ------------------------------------------------------------ descubrimiento
    @api.model
    def _get_or_create(self, nombre):
        """Devuelve el grupo del catalogo para `nombre` (ya normalizado), creandolo
        si es nuevo. Vacio si el nombre no sirve (placeholder sin chat)."""
        nombre = normaliza_grupo(nombre)
        if not nombre or es_placeholder_wa(nombre):
            return self.browse()
        grupo = self.search([('name', '=', nombre)], limit=1)
        now = fields.Datetime.now()
        if grupo:
            grupo.write({'last_seen': now})
        else:
            grupo = self.create({'name': nombre, 'last_seen': now})
        return grupo

    @api.model
    def resolver_desde_uso(self, app, host, documento):
        """El grupo de WhatsApp de un renglon de uso, o vacio si ese renglon no es
        WhatsApp con un chat abierto.

        Unifica los dos caminos: WhatsApp Web (la app es el navegador, host =
        web.whatsapp.com) y WhatsApp de escritorio (la app ES WhatsApp, sin host).
        En ambos el nombre del chat viaja en `document`. Fuera del ingest en un
        metodo propio para poder probarlo.
        """
        nombre = normaliza_grupo(documento)
        if not nombre or es_placeholder_wa(nombre):
            return self.browse()
        h = (host or '').strip().lower()
        es_web = (h == WA_WEB_HOST)
        es_app = (not h) and app_es_whatsapp(
            app.exe, app.product, app.company) if app else False
        if es_web or es_app:
            return self._get_or_create(nombre)
        return self.browse()

    # ------------------------------------------------------------ clasificacion
    @api.model
    def _cron_clasificar(self, limite=200):
        """La IA propone productivo/distraccion para los grupos sin clasificar.
        Fuera del ingest, como la cola de pestañas. No fija la categoria: eso lo
        confirma el aprobador (acuerdo de la reunion del 1-oct)."""
        oi = self.env['foco.openai'].sudo()
        if not oi.configurado():
            return
        pendientes = self.search([('ia_clasificado', '=', False)], limit=limite)
        if not pendientes:
            return
        try:
            res = oi.clasificar_whatsapp(pendientes.mapped('name'))
        except Exception:
            _logger.exception('Foco: clasificacion de grupos de WhatsApp')
            return
        pormbre = {r['nombre']: r for r in res}
        for g in pendientes:
            r = pormbre.get(g.name)
            if not r:
                g.ia_clasificado = True        # no se repregunta en vano
                continue
            g.write({
                'ia_suggestion': r['sugerencia'],
                'ia_reason': r['motivo'],
                'ia_model': r['modelo'],
                'ia_clasificado': True,
            })

    def _fijar(self, code):
        cat = self.env.ref('foco_monitor.cat_%s' % code, raise_if_not_found=False)
        if not cat:
            raise UserError('No existe la categoria "%s".' % code)
        self.write({'category_id': cat.id, 'decided_uid': self.env.uid,
                    'decided_at': fields.Datetime.now()})
        return True

    def action_productiva(self):
        return self._fijar('productiva')

    def action_distraccion(self):
        return self._fijar('distraccion')

    def action_ver_uso(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'foco.usage',
            'view_mode': 'list,pivot,graph',
            'domain': [('wa_group_id', '=', self.id)],
            'context': {'search_default_g_emp': 1},
        }

    # ------------------------------------------- WhatsApp de escritorio (vision)
    # La app UWP de escritorio (WhatsApp.Root, WebView2) NO expone su arbol de
    # accesibilidad: UIA solo ve el marco de la ventana, no el nombre del chat
    # (medido 9-oct-2026: 6 elementos). La unica via que ve el contenido es la
    # VISION sobre un recorte del encabezado. El agente manda el recorte con una
    # HUELLA; aqui se resuelve el nombre con Claude, cacheado por huella para no
    # repetir la llamada, y se le devuelve al agente para que lo use como el chat
    # abierto (`document`), que el resolver de arriba convierte en grupo.

    @api.model
    def resolver_headers(self, items):
        """Llamado desde el ingest (camino caliente): NO corre vision. Guarda los
        recortes nuevos como pendientes y devuelve {huella: nombre} SOLO de los que
        ya estan resueltos (en cache). Los pendientes los resuelve un cron; el
        agente los vuelve a preguntar hasta que haya nombre."""
        out = {}
        Header = self.env['foco.whatsapp.header'].sudo()
        nuevos = 0
        for it in (items or [])[:8]:
            if not isinstance(it, dict):
                continue
            h = (it.get('h') or '').strip()[:64]
            if not h:
                continue
            rec = Header.search([('hash', '=', h)], limit=1)
            if rec:
                if rec.state == 'resuelto':
                    out[h] = rec.name or ''
                continue
            if nuevos >= 4:                  # cota por envio: no crear sin fin
                continue
            Header.create({'hash': h, 'image_b64': (it.get('img') or '')[:300000],
                           'state': 'pendiente'})
            nuevos += 1
        return out

    @api.model
    def _nombre_por_imagen(self, img_b64):
        """El nombre del chat/grupo que se ve en el recorte del encabezado, por
        vision (Claude). '' si no se ve un nombre. Solo el NOMBRE, nunca mensajes."""
        oi = self.env['foco.openai'].sudo()
        if not oi.configurado() or not img_b64:
            return ''
        sistema = (
            "Ves el RECORTE de la franja superior (el encabezado) de una conversacion de "
            "WhatsApp de escritorio. Devuelve SOLO el nombre del chat o grupo que aparece "
            "en ese encabezado, tal cual se lee, sin agregar nada. Si no se alcanza a leer "
            "un nombre de chat, devuelve una cadena vacia. NUNCA inventes un nombre y NUNCA "
            "describas la imagen ni leas mensajes: solo el nombre del encabezado.")
        esquema = {
            'name': 'wa_header', 'strict': True,
            'schema': {
                'type': 'object', 'additionalProperties': False,
                'properties': {'nombre': {'type': 'string'}},
                'required': ['nombre'],
            },
        }
        res = oi.vision([('image/webp', img_b64)], sistema,
                        'Encabezado de WhatsApp. Da el nombre del chat o grupo.',
                        esquema, max_tokens=120)
        return ((res.get('json') or {}).get('nombre') or '').strip()

    @api.model
    def _cron_resolver_headers(self, limite=30):
        """Resuelve con vision los encabezados pendientes. Fuera del ingest, como
        la cola de pestañas. Tras varios intentos fallidos deja el recorte resuelto
        con nombre vacio para no reintentar sin fin."""
        Header = self.env['foco.whatsapp.header'].sudo()
        oi = self.env['foco.openai'].sudo()
        if not oi.configurado():
            return
        pendientes = Header.search([('state', '=', 'pendiente')], limit=limite)
        for hdr in pendientes:
            try:
                nombre = normaliza_grupo(self._nombre_por_imagen(hdr.image_b64))
            except Exception:
                _logger.exception('Foco: vision de encabezado de WhatsApp %s', hdr.hash)
                hdr.attempts += 1
                if hdr.attempts >= 3:
                    hdr.write({'state': 'resuelto', 'name': '', 'image_b64': False})
                continue
            if nombre and es_placeholder_wa(nombre):
                nombre = ''
            grupo = self._get_or_create(nombre) if nombre else self.browse()
            # La imagen se BORRA al resolver: no se guarda mas de lo necesario.
            hdr.write({'state': 'resuelto', 'name': nombre,
                       'group_id': grupo.id or False, 'image_b64': False})


class FocoWhatsappHeader(models.Model):
    """Cache del nombre del encabezado de WhatsApp de escritorio por HUELLA del
    recorte. Existe para no pedirle a la vision el mismo encabezado dos veces: el
    agente manda la huella (y la imagen la 1a vez), el cron la resuelve una vez y
    el nombre queda aqui para todos los equipos. El recorte se BORRA al resolver."""
    _name = 'foco.whatsapp.header'
    _description = 'Encabezado de WhatsApp resuelto por vision (cache por huella)'
    _order = 'create_date desc'

    _hash_uniq = models.Constraint('unique(hash)', 'Esa huella de encabezado ya existe.')

    hash = fields.Char(required=True, index=True)
    name = fields.Char(string='Nombre leido')
    group_id = fields.Many2one('foco.whatsapp.group', ondelete='set null', index=True)
    state = fields.Selection([('pendiente', 'Pendiente'), ('resuelto', 'Resuelto')],
                             default='pendiente', required=True, index=True)
    # El recorte del encabezado (webp base64). Temporal: se borra en cuanto la
    # vision lo resuelve. Nunca es la pantalla completa, solo la franja del titulo.
    image_b64 = fields.Text(string='Recorte (temporal)')
    attempts = fields.Integer(default=0)
