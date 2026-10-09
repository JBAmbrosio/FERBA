import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Quita el contador de no leidos del inicio del titulo: "(3) WhatsApp" y
# "(5) WhatsApp" son la misma pestaña y no deben crear dos filas.
_CONTADOR = re.compile(r'^\(\d+\)\s*')

# Chrome/Edge 154 (ahorro de memoria) decoran el titulo de una pestaña dormida
# con "Uso de memoria: N MB". Llega como PREFIJO ("Uso de memoria de X: N MB") o
# como SUFIJO ("X: Uso de memoria: N MB" / "X - Uso de memoria - N MB"). Es ruido
# que ademas impide deduplicar (dos pestañas iguales con distinto MB se veian
# distintas). Se quita; se cubre español e ingles, que es lo que usa la flota.
_MEM_PREFIJO = re.compile(
    r'^(?:uso de memoria de|memory usage for)\s+(.*?):\s*[\d.,]+\s*[KMG]B\s*$', re.I)
_MEM_SUFIJO = re.compile(
    r'\s*[-:]\s*(?:uso de memoria|memory usage)\s*[-:]\s*[\d.,]+\s*[KMG]B\s*$', re.I)


def _normaliza_titulo(t):
    """El titulo tal como se guarda y se compara. Funcion a nivel de modulo para
    poder probarla sin Odoo."""
    t = (t or '').strip()
    t = _CONTADOR.sub('', t)
    m = _MEM_PREFIJO.match(t)
    if m:
        t = m.group(1).strip()
    else:
        t = _MEM_SUFIJO.sub('', t).strip()
    return t[:300]


_IPV4 = re.compile(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$')
_TLD_ALFA = re.compile(r'^[a-z]{2,}$')


def _dominio_bloqueable(host):
    """¿`host` (ya normalizado) sirve como patron de bloqueo REAL? IPv4 valida,
    localhost, o dominio cuyo TLD sean letras. Rechaza basura como '0.00' o
    'x.123': crearian una regla que no bloquea nada, el fallo silencioso que este
    proyecto evita. A nivel de modulo para poder probarla sin Odoo."""
    h = (host or '').strip().lower()
    if not h:
        return False
    if h == 'localhost':
        return True
    m = _IPV4.match(h)
    if m:
        return all(int(x) <= 255 for x in m.groups())
    return '.' in h and bool(_TLD_ALFA.match(h.rsplit('.', 1)[-1]))


# Buscadores: NO se bloquean nunca. Bloquear google.com apaga la busqueda
# ENTERA (su dominio es google.com, no la consulta), que es justo lo que no se
# quiere (reunion 8-oct-2026). Se tratan como permitidos y action_bloquear los
# rechaza. Gmail/Drive son otros hosts (mail.google.com, drive.google.com), asi
# que proteger el google.com pelado solo cubre el buscador.
DOMINIOS_PROTEGIDOS = {'google.com', 'google.com.mx', 'bing.com', 'duckduckgo.com'}


def _es_buscador(host):
    """`host` ya normalizado es un buscador que no debe bloquearse. A nivel de
    modulo para poder probarla sin Odoo."""
    return (host or '').strip().lower() in DOMINIOS_PROTEGIDOS


def _es_pdf(titulo):
    """El titulo es un archivo PDF abierto en el navegador, no un sitio: no tiene
    sentido ofrecerlo para bloquear (reunion 8-oct-2026). Facil de ampliar a otros
    documentos si hiciera falta. A nivel de modulo para poder probarla sin Odoo."""
    return (titulo or '').strip().lower().endswith('.pdf')


class FocoTabReview(models.Model):
    """Una pestaña abierta que la IA clasifico contra el perfil del puesto y que
    espera la decision de un aprobador (permitir / bloquear).

    POR QUE EXISTE
        El agente ya reporta los TITULOS de todas las pestañas abiertas (la URL
        exacta solo de la activa). La IA los clasifica por perfil; las que no
        encajan aterrizan aqui para que el aprobador decida una por una, igual
        que las cotizaciones. Bloquear agrega el dominio a las reglas del perfil
        y el servicio del equipo lo hace cumplir.

    DECISIONES POR ROL, NO POR PERSONA
        El bloqueo se escribe en el PERFIL de navegacion (foco.policy), que es el
        grupo; afecta a todos los equipos de ese perfil. Es lo que se quiere: el
        rol define lo permitido.
    """
    _name = 'foco.tab.review'
    _description = 'Pestaña abierta por revisar'
    _order = 'create_date desc'

    computer_id = fields.Many2one('foco.computer', required=True,
                                  ondelete='cascade', index=True)
    employee_id = fields.Many2one('hr.employee', related='computer_id.employee_id',
                                  store=True, index=True)
    policy_id = fields.Many2one('foco.policy', ondelete='set null', index=True,
                                string='Perfil',
                                help='El perfil (rol) del equipo cuando se vio la pestaña.')
    title = fields.Char(required=True)
    host = fields.Char(
        string='Dominio',
        help='Para bloquear hace falta el dominio. Lo mejor es el capturado "por '
             'uso" (host real de cuando la persona tuvo la pestaña al frente); si '
             'no, la IA lo sugiere desde el titulo; si tampoco, escribelo a mano '
             '(p. ej. youtube.com) y ya se puede bloquear.')
    host_capturado = fields.Boolean(
        string='Dominio por uso', default=False,
        help='El dominio se capturo cuando la persona tuvo la pestaña al frente '
             '(dato real), no es la conjetura de la IA desde el titulo. Es el que '
             'conviene para bloquear.')
    ia_clasificado = fields.Boolean(default=False, index=True)
    ia_matches = fields.Boolean(string='Va con el rol')
    ia_suggestion = fields.Selection(
        [('permitir', 'Permitir'), ('bloquear', 'Bloquear'), ('revisar', 'Revisar')],
        string='Sugerencia de la IA', default='revisar')
    ia_reason = fields.Char(string='Motivo')
    ia_model = fields.Char(string='Modelo')
    state = fields.Selection(
        [('pendiente', 'Pendiente'), ('permitido', 'Permitido'), ('bloqueado', 'Bloqueado')],
        default='pendiente', required=True, index=True)
    decided_uid = fields.Many2one('res.users', string='Decidio', readonly=True)
    decided_at = fields.Datetime(string='Decidido', readonly=True)

    @api.model
    def ingest_tabs(self, computer, titulos, tab_hosts=None):
        """Guarda las pestañas NUEVAS de un equipo como filas por clasificar.

        NO clasifica aqui: la IA corre en un cron, fuera del camino caliente del
        ingest (una llamada a OpenAI no puede meterle latencia a cada envio del
        agente). No repregunta: si ya hay una fila -en cualquier estado- para
        (equipo, titulo), no crea otra. Devuelve cuantas filas nuevas creo.

        `tab_hosts` es {titulo: host} con los hosts REALES que el agente capturo
        cuando esa pestaña estuvo al frente. Es la verdad para bloquear: se pega a
        la fila y gana sobre la conjetura de la IA. Tambien MEJORA filas ya
        existentes: si el host real llega despues (la persona enfoco la pestaña mas
        tarde), se les pone.
        """
        vistos, limpios = set(), []
        for t in (titulos or []):
            n = _normaliza_titulo(t)
            # Los PDF abiertos en el navegador no son sitios: no se ofrecen para
            # bloquear (reunion 8-oct-2026), asi no ensucian la cola de revision.
            if n and n not in vistos and not _es_pdf(n):
                vistos.add(n)
                limpios.append(n)
        if not limpios:
            return 0
        # Hosts reales por titulo, con la llave normalizada igual que el titulo.
        real = {}
        for k, v in (tab_hosts or {}).items():
            nt = _normaliza_titulo(k)
            host = (v or '').strip().lower()
            if nt and host:
                real[nt] = host
        existentes = self.search([
            ('computer_id', '=', computer.id), ('title', 'in', limpios)])
        ya = set(existentes.mapped('title'))
        # Mejorar lo que ya existe: si llego el host real de un titulo PENDIENTE
        # que aun no lo tiene capturado (tenia la conjetura de la IA, o nada),
        # pegarselo. Solo pendientes: en una fila ya bloqueada la regla ya se creo
        # con el host de entonces, y cambiarlo dejaria fila y regla distintas.
        for f in existentes:
            h = real.get(f.title)
            if h and not f.host_capturado and f.state == 'pendiente':
                vals = {'host': h, 'host_capturado': True}
                if _es_buscador(h):
                    vals.update(self._permiso_buscador())
                f.write(vals)
        perfil = self.env['foco.policy']._perfil_vigente(computer)
        nuevos = []
        for t in limpios:
            if t in ya:
                continue
            h = real.get(t)
            vals = {'computer_id': computer.id,
                    'policy_id': perfil.id if perfil else False,
                    'title': t,
                    'host': h or False,
                    'host_capturado': bool(h)}
            # Un buscador (google.com) entra ya PERMITIDO: no se bloquea nunca.
            if h and _es_buscador(h):
                vals.update(self._permiso_buscador())
            nuevos.append(vals)
        if nuevos:
            self.create(nuevos)
        return len(nuevos)

    @api.model
    def _permiso_buscador(self):
        """Valores para dejar una fila de buscador como permitida y fuera de la
        cola de la IA: se decide sola, no hay nada que revisar."""
        return {'state': 'permitido', 'ia_clasificado': True,
                'ia_suggestion': 'permitir',
                'ia_reason': 'Buscador: no se bloquea para no apagar la busqueda',
                'decided_uid': self.env.uid, 'decided_at': fields.Datetime.now()}

    @api.model
    def _cron_clasificar(self, limite=200):
        """Clasifica con la IA las pestañas pendientes, agrupadas por perfil (el
        prompt del rol es el mismo para todo el grupo). Fuera del ingest."""
        oi = self.env['foco.openai'].sudo()
        if not oi.configurado():
            return
        pendientes = self.search([('ia_clasificado', '=', False)], limit=limite)
        if not pendientes:
            return
        porgrupo = {}
        for r in pendientes:
            porgrupo.setdefault(r.policy_id.id, self.browse())
            porgrupo[r.policy_id.id] |= r
        Policy = self.env['foco.policy']
        for pid, filas in porgrupo.items():
            perfil = Policy.browse(pid) if pid else Policy
            rol = perfil.rol_prompt if pid else ''
            try:
                res = oi.clasificar_pestanas(rol, filas.mapped('title'))
            except Exception:
                # Un fallo de OpenAI no traba la cola: se reintenta en la
                # siguiente corrida (siguen ia_clasificado=False).
                _logger.exception('Foco: clasificacion de pestañas (perfil %s)', pid)
                continue
            porindice = {r['titulo']: r for r in res}
            for f in filas:
                r = porindice.get(f.title)
                if not r:
                    f.ia_clasificado = True        # no se repregunta en vano
                    continue
                vals = {
                    'ia_matches': r['va_con_rol'],
                    'ia_suggestion': r['sugerencia'],
                    'ia_reason': r['motivo'],
                    'ia_model': r['modelo'],
                    'ia_clasificado': True,
                }
                # El host REAL capturado manda; la conjetura de la IA solo rellena
                # cuando no hay un host capturado.
                if not f.host_capturado:
                    vals['host'] = f.host or r['dominio'] or False
                # Si lo que quedo (capturado o adivinado) es un buscador, se
                # permite y no se ofrece para bloquear, diga lo que diga la IA.
                host_final = (vals.get('host') if 'host' in vals else f.host) or ''
                if _es_buscador(host_final):
                    vals.update({'ia_suggestion': 'permitir',
                                 'ia_reason': 'Buscador: no se bloquea para no apagar la busqueda'})
                    if f.state == 'pendiente':
                        vals.update({'state': 'permitido', 'decided_uid': self.env.uid,
                                     'decided_at': fields.Datetime.now()})
                f.write(vals)

    def action_permitir(self):
        self.write({'state': 'permitido', 'decided_uid': self.env.uid,
                    'decided_at': fields.Datetime.now()})
        return True

    def action_bloquear(self):
        """Agrega el dominio a las reglas del perfil y marca la fila bloqueada.
        El servicio del equipo hace cumplir la regla en su siguiente ciclo."""
        Rule = self.env['foco.policy.rule'].sudo()
        Site = self.env['foco.site']
        for r in self:
            if r.state == 'bloqueado':
                continue
            # Se normaliza el dominio (quita esquema, www y ruta, y VALIDA que sea
            # un host real): asi no se mete una regla que no bloquea nada, como
            # 'www.youtube.com' (no cubre youtube.com) o basura que la IA hubiera
            # adivinado mal.
            host = Site._normalizar_host(r.host or '')
            if not _dominio_bloqueable(host):
                raise UserError(
                    'Para bloquear "%s" hace falta un dominio valido (p. ej. '
                    'youtube.com). "%s" no lo parece; corrigelo en la columna '
                    'Dominio y vuelve a bloquear.' % (r.title or '', r.host or ''))
            # Un buscador no se bloquea: su dominio es la busqueda entera, no una
            # consulta, y bloquearlo la apagaria para todos (reunion 8-oct-2026).
            if _es_buscador(host):
                raise UserError(
                    'No se puede bloquear "%s": es un buscador. Su dominio es la '
                    'busqueda completa, no una consulta, asi que bloquearlo apagaria '
                    'la busqueda para todos. Dejalo permitido.' % host)
            perfil = r.policy_id
            if not perfil:
                raise UserError(
                    'Esta pestaña no tiene un perfil de navegacion asociado, asi '
                    'que no hay donde agregar la regla. Asigna un perfil al equipo.')
            if not Rule.search_count([('policy_id', '=', perfil.id),
                                      ('pattern', '=', host)]):
                Rule.create({
                    'policy_id': perfil.id, 'pattern': host, 'action': 'block',
                    'sequence': max(perfil.rule_ids.mapped('sequence') or [0]) + 10,
                    'note': 'Bloqueado desde la revision de pestañas',
                })
            vals = {'state': 'bloqueado', 'decided_uid': self.env.uid,
                    'decided_at': fields.Datetime.now()}
            if host != (r.host or ''):
                vals['host'] = host      # deja guardado el dominio limpio que se bloqueo
            r.write(vals)
        return True
