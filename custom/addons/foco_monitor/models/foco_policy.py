"""Que sitios puede abrir cada equipo, decidido desde Odoo.

La pieza que hace cumplir esto NO vive aqui: vive en el servicio del equipo, que
corre como SYSTEM y escribe la politica de empresa del navegador. Aqui solo se
decide y se publica. La separacion importa: Odoo es la fuente de la decision y el
equipo es quien la aplica, asi que una maquina sin red conserva la ultima
politica en vez de quedarse abierta.

Por que un PERFIL y no una lista por equipo: el cliente lo planteo por rol -"a
ventas si le hace falta WhatsApp, a un disenador no"-. Mantener treinta listas a
mano es una lista que nadie mantiene; mantener tres perfiles si.
"""

import hashlib
import json
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Esquemas que tiene sentido escribir en un patron. La lista existe para cazar
# dedazos ('htpp://'), no para limitar: lo que no lleva esquema aplica a todos.
ESQUEMAS = ('http', 'https', 'ftp', 'file', 'ws', 'wss')

# Formato de patron de Chromium (URLBlocklist):
#   [esquema://][.]host[:puerto][/ruta][@query]
# '*' solo = todo. Se valida lo que de verdad rompe: espacios, vacio, largo.
MAX_PATRON = 250


class FocoPolicy(models.Model):
    _name = 'foco.policy'
    _description = 'Perfil de navegacion'
    _order = 'name'

    _nombre_uniq = models.Constraint('unique(name)',
                                     'Ya existe un perfil con ese nombre.')

    name = fields.Char(string='Perfil', required=True)
    active = fields.Boolean(default=True)
    note = fields.Text(
        string='Para que es',
        help='Quien usa este perfil y por que. Se lee el dia que alguien '
             'pregunte por que no puede abrir un sitio.')
    rule_ids = fields.One2many('foco.policy.rule', 'policy_id', string='Reglas')
    computer_ids = fields.One2many('foco.computer', 'policy_id', string='Equipos')

    # Navegadores que se saltan el bloqueo. Medido (17-sep-2026): Opera ignora
    # la politica de empresa que Chrome, Edge, Brave y Firefox si obedecen, y
    # ademas trae una VPN integrada que brinca cualquier bloqueo por DNS. No hay
    # forma de que Opera bloquee; lo que hay es que Opera no corra. El servicio
    # del equipo (SYSTEM) cierra estos ejecutables en cuanto aparecen, avisa en
    # pantalla y lo reporta como evento. La lista es dato, no codigo: si aparece
    # otro navegador que tampoco obedece, se agrega aqui. Daniel lo destapo el
    # 25-sep: YouTube bloqueado en Chrome, 29 minutos de YouTube en Opera.
    close_unmanaged = fields.Boolean(
        string='Cerrar navegadores que se saltan el bloqueo', default=True,
        help='El servicio del equipo cierra en cuanto abren los programas de la '
             'lista y lo registra en Actividad. Sin esto, basta instalar Opera '
             'para ver cualquier sitio bloqueado.')
    kill_exes = fields.Text(
        string='Programas que se cierran (uno por linea)',
        default='opera.exe\nopera_gx.exe',
        help='Nombres de programa tal como los ve Windows (opera.exe), uno por '
             'linea. Aplica con el interruptor de arriba encendido y con el '
             'bloqueo de sitios encendido en Ajustes.')

    def _kill_list(self):
        """Los ejecutables a cerrar, limpios: minusculas, con .exe, sin repetir."""
        self.ensure_one()
        if not self.close_unmanaged:
            return []
        vistos = []
        for linea in (self.kill_exes or '').replace(',', '\n').splitlines():
            exe = linea.strip().lower()
            if not exe or any(c in exe for c in ' \\/"'):
                continue
            if not exe.endswith('.exe'):
                exe += '.exe'
            if exe not in vistos:
                vistos.append(exe)
        return vistos

    # El mismo conjunto de equipos, pero ESCRIBIBLE desde aqui.
    #
    # Existe por un defecto de uso real: la pestana de equipos mostraba el
    # one2many, que en un formulario solo deja VER y crear equipos nuevos. Para
    # asignar uno existente habia que salir a otra pantalla, buscarlo y
    # cambiarle el perfil ahi. El usuario creo un perfil, abrio la pestana, no
    # encontro su equipo y concluyo -con razon- que la pantalla no servia.
    #
    # Un many2many calculado con inverso da el selector de siempre ("escribe el
    # nombre y elige"), sin cambiar la relacion de fondo: un equipo sigue
    # teniendo UN perfil.
    computer_assign_ids = fields.Many2many(
        'foco.computer', string='Equipos con este perfil',
        compute='_compute_assign', inverse='_inverse_assign',
        help='A que equipos se les aplica. Un equipo solo puede tener un '
             'perfil: asignarlo aqui lo quita del que tuviera antes.')

    computer_count = fields.Integer(compute='_compute_computer_count')
    rule_count = fields.Integer(compute='_compute_computer_count')

    # Si el interruptor general esta apagado, este perfil no se aplica aunque
    # este perfectamente configurado. Decirlo AQUI, y no solo en Ajustes, es lo
    # que evita la conclusion de "lo configure y no hace nada".
    activo_global = fields.Boolean(compute='_compute_activo_global')
    estado_nota = fields.Char(compute='_compute_activo_global')

    @api.depends('computer_ids')
    def _compute_assign(self):
        for p in self:
            p.computer_assign_ids = p.computer_ids

    def _inverse_assign(self):
        for p in self:
            nuevos = p.computer_assign_ids
            # Los que se quitaron de la lista se quedan SIN perfil, no con otro:
            # inventarles uno seria decidir por el administrador.
            (p.computer_ids - nuevos).write({'policy_id': False})
            nuevos.write({'policy_id': p.id})

    def _compute_activo_global(self):
        ajustes = self.env['foco.settings'].sudo().get_settings()
        for p in self:
            p.activo_global = ajustes.block_enabled
            if not p.active:
                # Va PRIMERO: un perfil archivado no se aplica aunque el
                # interruptor este encendido y aunque tenga equipos asignados.
                # Y hay que decir cuantos son, porque esos equipos quedaron sin
                # bloqueo y su pantalla los sigue mostrando aqui.
                n = len(p.computer_ids)
                p.estado_nota = (
                    'Este perfil esta ARCHIVADO: no se aplica en ningun equipo.'
                    + (' Los %d equipo(s) que lo tenian asignado pasaron al '
                       'perfil por omision, o quedaron sin bloqueo si no hay '
                       'ninguno.' % n if n else ''))
            elif not ajustes.block_enabled:
                p.estado_nota = ('El bloqueo de sitios esta APAGADO en Ajustes. '
                                 'Este perfil no se esta aplicando en ningun equipo.')
            elif not p.computer_ids and ajustes.default_policy_id != p:
                p.estado_nota = ('Este perfil no tiene equipos asignados y no es '
                                 'el perfil por omision, asi que no se aplica a nadie.')
            else:
                n = len(p.computer_ids)
                p.estado_nota = ('Aplicandose en %d equipo%s.'
                                 % (n, '' if n == 1 else 's'))

    def action_encender_bloqueo(self):
        """Enciende el interruptor general sin salir de esta pantalla."""
        self.env['foco.settings'].sudo().get_settings().block_enabled = True
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # El hash del contenido. El servicio del equipo lo compara con el que tiene
    # aplicado y solo reescribe el registro si cambio: sin esto, cada ciclo de
    # cinco minutos reescribiria la politica de todos los navegadores por nada.
    version = fields.Char(string='Version', compute='_compute_version',
                          store=True, readonly=True)

    @api.depends('computer_ids')
    def _compute_computer_count(self):
        for p in self:
            p.computer_count = len(p.computer_ids)
            p.rule_count = len(p.rule_ids)

    @api.depends('rule_ids.pattern', 'rule_ids.action', 'rule_ids.active')
    def _compute_version(self):
        for p in self:
            p.version = p._hash(p._listas())

    # ------------------------------------------------------------ publicacion
    def _listas(self):
        """(bloquear, permitir) ya normalizadas y sin repetidos."""
        self.ensure_one()
        bloquear, permitir = [], []
        for r in self.rule_ids.sorted('sequence'):
            if not r.active or not r.pattern:
                continue
            (permitir if r.action == 'allow' else bloquear).append(r.pattern)
        return sorted(set(bloquear)), sorted(set(permitir))

    @api.model
    def _hash(self, listas):
        crudo = json.dumps(listas, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(crudo.encode('utf-8')).hexdigest()[:16]

    @api.model
    def _salvavidas(self):
        """El propio Odoo NUNCA se bloquea.

        Un administrador que escriba '*' para bloquear todo y armar una lista
        blanca se dejaria fuera de la unica pantalla desde la que puede
        corregirlo, en todos los equipos a la vez. Esta excepcion va siempre, no
        se configura, y por eso se declara aqui y no en un dato editable.
        """
        base = (self.env['ir.config_parameter'].sudo()
                .get_param('web.base.url') or '')
        host = re.sub(r'^\w+://', '', base).split('/')[0].split(':')[0].strip()
        return [host] if host and host not in ('localhost', '127.0.0.1') else []

    @api.model
    @api.model
    def _perfil_vigente(self, computer):
        """El perfil que DE VERDAD se le aplica a este equipo, o vacio.

        Un perfil ARCHIVADO no se aplica. Parecia obvio y no lo era: al
        archivar, Odoo NO limpia el `policy_id` de los equipos, y un registro
        archivado sigue siendo valido al leerlo desde un many2one. La version
        anterior de esto hacia `computer.policy_id or ...` y por lo tanto
        seguia publicando las reglas de un perfil retirado -con el agravante de
        que el perfil ya no sale en la lista, asi que no habia NADA en pantalla
        que explicara por que ese equipo seguia con sitios cerrados-.

        Archivar es lo que hace un administrador cuando retira un perfil;
        borrarlo es lo raro. Tenian que significar lo mismo, y ahora lo
        significan: los dos caminos dejan al equipo con el perfil por omision,
        y si ese tambien esta archivado, sin bloqueo.

        Vive aqui, y no repetido en cada quien, porque el estado que se PINTA
        en Odoo y la lista que se PUBLICA al equipo tienen que salir de la
        misma decision. Si se separan, la pantalla dice una cosa y la maquina
        hace otra, que es el unico error de esta funcion que nadie notaria.
        """
        ajustes = self.env['foco.settings'].sudo().get_settings()
        propio = computer.policy_id.sudo().filtered('active')
        return propio or ajustes.default_policy_id.sudo().filtered('active')

    def payload_for(self, computer):
        """Lo que se le manda al equipo. Siempre un dict, nunca None.

        Un equipo sin perfil toma el perfil por omision de la configuracion; si
        tampoco hay, la lista va vacia -que significa 'no bloquees nada', no
        'bloquea todo'-.
        """
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if not ajustes.block_enabled:
            # El interruptor general apagado publica listas vacias A PROPOSITO:
            # asi el equipo LIMPIA lo que tuviera puesto en vez de conservarlo.
            # `kill` vacio por lo mismo: sin bloqueo no se cierra ningun navegador.
            return {'version': 'off', 'block': [], 'allow': [], 'kill': []}
        perfil = self._perfil_vigente(computer)
        if not perfil:
            return {'version': 'vacio', 'block': [], 'allow': [], 'kill': []}
        bloquear, permitir = perfil._listas()
        permitir = sorted(set(permitir) | set(self._salvavidas()))
        return {'version': perfil._hash((bloquear, permitir)),
                'block': bloquear, 'allow': permitir,
                'kill': perfil._kill_list(),
                'policy': perfil.name}

    def action_ver_equipos(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Equipos con el perfil %s' % self.name,
            'res_model': 'foco.computer',
            'view_mode': 'list,form',
            'domain': [('policy_id', '=', self.id)],
        }

    # ------------------------------------------------------- que NO se cubre
    #
    # El caso que obliga a que esto exista: bloquear `youtube.com` NO bloquea
    # `youtu.be`. Son dominios distintos con el mismo contenido, y el
    # administrador se queda creyendo que cerro YouTube.
    #
    # La salida facil seria una tabla de "dominios hermanos conocidos". No se
    # hace: seria una lista de sitios cocida en el codigo, que es justo lo que
    # este proyecto no tiene, y ademas envejeceria sola. Lo que si hay es el
    # CATALOGO DE SITIOS, que se llena con lo que la gente de esta empresa abre
    # de verdad. Si alguien entro a `youtu.be`, ahi esta. Asi que en vez de
    # adivinar, se mira el dato y se pregunta.

    @api.model
    def _cubre(self, patron, host):
        """Este patron, tal como lo entiende el navegador, alcanza a ese host?

        Reproduce la semantica de Chromium en lo que aqui se usa:
          - `dominio`       cubre el dominio y TODOS sus subdominios
          - `.dominio`      cubre SOLO ese host exacto
          - `esquema://...` el esquema no cambia a que host alcanza
          - `dominio/ruta`  NO cubre el host entero, solo esa ruta
          - `*`             cubre todo
        """
        p = (patron or '').strip().lower()
        h = (host or '').strip().lower()
        if not p or not h:
            return False
        if p == '*':
            return True
        if '://' in p:
            p = p.split('://', 1)[1]
        if '/' in p:
            # Una regla de ruta deja el resto del sitio abierto, asi que no
            # cuenta como cobertura del host.
            return False
        exacto = p.startswith('.')
        p = p.lstrip('.')
        if not p:
            return False
        if exacto:
            return h == p
        return h == p or h.endswith('.' + p)

    sitios_sin_cubrir_ids = fields.Many2many(
        'foco.site', string='Vistos y no bloqueados',
        compute='_compute_sin_cubrir',
        help='Sitios que ALGUIEN DE ESTA EMPRESA abrio, que estan clasificados '
             'como no productivos, y que ninguna regla de este perfil alcanza. '
             'No es una lista de sitios conocidos: es lo que se midio.')
    cobertura_nota = fields.Char(compute='_compute_sin_cubrir')

    @api.depends('rule_ids.pattern', 'rule_ids.action', 'rule_ids.active')
    def _compute_sin_cubrir(self):
        Site = self.env['foco.site'].sudo()
        # weight <= 0 y clasificado = el admin ya dijo que esto no aporta. No se
        # infiere de un nombre ni de una lista: sale de su propia clasificacion.
        candidatos = Site.search([
            ('category_id', '!=', False),
            ('category_id.weight', '<=', 0.0),
            ('category_id.is_system', '=', False),
        ])
        for p in self:
            bloquea = [r.pattern for r in p.rule_ids
                       if r.active and r.action == 'block']
            permite = [r.pattern for r in p.rule_ids
                       if r.active and r.action == 'allow']
            sueltos = Site.browse()
            for s in candidatos:
                if any(self._cubre(x, s.host) for x in bloquea):
                    continue
                if any(self._cubre(x, s.host) for x in permite):
                    continue      # esta permitido a proposito
                sueltos |= s
            p.sitios_sin_cubrir_ids = sueltos
            if not candidatos:
                p.cobertura_nota = (
                    'Todavia no hay sitios clasificados como no productivos, '
                    'asi que no hay nada que sugerir.')
            elif sueltos:
                p.cobertura_nota = (
                    '%d sitio(s) que si se han abierto en la empresa y estan '
                    'clasificados como no productivos NO los alcanza ninguna '
                    'regla de este perfil.' % len(sueltos))
            else:
                p.cobertura_nota = (
                    'Este perfil alcanza todos los sitios no productivos que se '
                    'han visto (%d).' % len(candidatos))

    def action_bloquear_sugeridos(self):
        """Agrega como regla de bloqueo todo lo que quedo suelto."""
        self.ensure_one()
        Rule = self.env['foco.policy.rule']
        seq = max(self.rule_ids.mapped('sequence') or [0]) + 10
        for s in self.sitios_sin_cubrir_ids:
            Rule.create({'policy_id': self.id, 'pattern': s.host,
                         'action': 'block', 'sequence': seq,
                         'note': 'Sugerido: visto y clasificado como no productivo'})
            seq += 10
        return True


class FocoPolicyRule(models.Model):
    _name = 'foco.policy.rule'
    _description = 'Regla de navegacion'
    _order = 'policy_id, sequence, id'

    policy_id = fields.Many2one('foco.policy', required=True, ondelete='cascade',
                                index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    pattern = fields.Char(
        string='Patron', required=True,
        help="Un dominio ('youtube.com'), un subdominio ('music.youtube.com') o "
             "una ruta ('ejemplo.com/foro'). Sin esquema aplica a http y https. "
             "Un dominio incluye TODOS sus subdominios: bloquear 'youtube.com' "
             "tambien tumba 'music.youtube.com', y por eso existe 'Permitir'.")
    action = fields.Selection(
        [('block', 'Bloquear'), ('allow', 'Permitir')],
        string='Accion', default='block', required=True,
        help='Permitir GANA sobre bloquear. Es la forma de abrir una excepcion '
             'dentro de un dominio bloqueado.')
    note = fields.Char(
        string='Por que',
        help='La razon de la regla. Cuesta diez segundos escribirla y ahorra la '
             'discusion de por que este sitio esta cerrado.')

    # A que alcanza el patron, contrastado contra lo que la empresa abre DE
    # VERDAD.
    #
    # Existe porque escribir el patron a mano es el punto fragil de esta
    # pantalla: `youtube.com` y `youtubee.com` se ven casi igual y el segundo no
    # bloquea nada, sin un solo error en ningun lado. La forma barata de cazarlo
    # seria validar el dominio contra una lista; no se hace, porque seria una
    # lista de sitios cocida y ademas no distingue un dedazo de un sitio que
    # todavia nadie abrio.
    #
    # Lo que si es un hecho medido: que sitios del CATALOGO -los que alguien de
    # esta empresa visito- caen bajo este patron. Cero es un indicio fuerte de
    # dedazo, pero se dice como indicio, no como error: un sitio recien
    # bloqueado por adelantado tambien da cero y es correcto.
    alcance = fields.Char(string='A que alcanza', compute='_compute_alcance')

    @api.depends('pattern')
    def _compute_alcance(self):
        Policy = self.env['foco.policy']
        # Una sola busqueda para todas las reglas: el catalogo se lee igual para
        # cada una y en un perfil con veinte reglas serian veinte consultas.
        hosts = self.env['foco.site'].sudo().search([]).mapped('host')
        for r in self:
            patron = (r.pattern or '').strip()
            if not patron:
                r.alcance = ''
                continue
            sin_esquema = patron.split('://', 1)[-1]
            if '/' in sin_esquema:
                r.alcance = ('Regla de ruta: alcanza solo esa ruta, no el sitio '
                             'completo.')
                continue
            casan = [h for h in hosts if Policy._cubre(patron, h)]
            if casan:
                muestra = ', '.join(sorted(casan)[:3])
                resto = len(casan) - 3
                r.alcance = ('Alcanza %d sitio(s) ya vistos: %s%s'
                             % (len(casan), muestra,
                                ' y %d mas' % resto if resto > 0 else ''))
            elif hosts:
                r.alcance = ('Ningun sitio de los %d ya vistos cae bajo este '
                             'patron. Puede estar bien -si nadie lo ha abierto- '
                             'o ser un dedazo.' % len(hosts))
            else:
                r.alcance = 'Todavia no hay sitios vistos con que contrastar.'

    @api.model
    def _normalizar(self, crudo):
        """Deja el patron en la forma que entiende el navegador.

        Se acepta que peguen una URL completa del navegador -es lo que va a
        pasar- y se recorta a lo util. No se intenta adivinar mas: un patron que
        el administrador no reconoce es un patron que no puede corregir.
        """
        p = (crudo or '').strip()
        if not p:
            return ''
        if p == '*':
            return '*'
        # Se tira la parte que no filtra nada y solo confunde.
        p = re.sub(r'^(https?://)?(www\.)?', '', p, flags=re.I)
        p = p.split('#')[0].split('?')[0].rstrip('/')
        return p.lower()

    @api.constrains('pattern')
    def _check_pattern(self):
        for r in self:
            p = (r.pattern or '').strip()
            if not p:
                raise ValidationError('El patron no puede ir vacio.')
            if len(p) > MAX_PATRON:
                raise ValidationError(
                    'El patron es demasiado largo (%d caracteres, maximo %d).'
                    % (len(p), MAX_PATRON))
            if any(c.isspace() for c in p):
                raise ValidationError(
                    "El patron '%s' lleva espacios. Un patron es un dominio o "
                    "una ruta, no una frase." % p)
            if '://' in p:
                esquema = p.split('://')[0].lower().lstrip('*.')
                if esquema and esquema not in ESQUEMAS:
                    raise ValidationError(
                        "'%s' no es un esquema valido. Se esperaba uno de: %s."
                        % (esquema, ', '.join(ESQUEMAS)))
            if p == '*' and r.action == 'block':
                # No se prohibe: se avisa en el log. Bloquear todo y abrir una
                # lista blanca es una estrategia legitima, pero si alguien lo
                # escribio sin querer deja treinta equipos sin internet.
                _logger.warning(
                    "Perfil '%s': regla '*' -> se bloquea TODA la navegacion "
                    "salvo lo que este en Permitir.", r.policy_id.name)

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('pattern'):
                v['pattern'] = self._normalizar(v['pattern'])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('pattern'):
            vals['pattern'] = self._normalizar(vals['pattern'])
        return super().write(vals)
