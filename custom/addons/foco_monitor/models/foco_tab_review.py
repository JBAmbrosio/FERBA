import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Quita el contador de no leidos del inicio del titulo: "(3) WhatsApp" y
# "(5) WhatsApp" son la misma pestaña y no deben crear dos filas.
_CONTADOR = re.compile(r'^\(\d+\)\s*')


def _normaliza_titulo(t):
    """El titulo tal como se guarda y se compara. Funcion a nivel de modulo para
    poder probarla sin Odoo."""
    t = (t or '').strip()
    t = _CONTADOR.sub('', t)
    return t[:300]


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
        help='Para bloquear hace falta el dominio. La IA lo sugiere desde el '
             'titulo si puede; si no, escribelo (p. ej. youtube.com) y ya se '
             'puede bloquear.')
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
    def ingest_tabs(self, computer, titulos):
        """Guarda las pestañas NUEVAS de un equipo como filas por clasificar.

        NO clasifica aqui: la IA corre en un cron, fuera del camino caliente del
        ingest (una llamada a OpenAI no puede meterle latencia a cada envio del
        agente). No repregunta: si ya hay una fila -en cualquier estado- para
        (equipo, titulo), no crea otra. Devuelve cuantas filas nuevas creo.
        """
        vistos, limpios = set(), []
        for t in (titulos or []):
            n = _normaliza_titulo(t)
            if n and n not in vistos:
                vistos.add(n)
                limpios.append(n)
        if not limpios:
            return 0
        ya = set(self.search([
            ('computer_id', '=', computer.id),
            ('title', 'in', limpios)]).mapped('title'))
        perfil = self.env['foco.policy']._perfil_vigente(computer)
        nuevos = [{'computer_id': computer.id,
                   'policy_id': perfil.id if perfil else False,
                   'title': t}
                  for t in limpios if t not in ya]
        if nuevos:
            self.create(nuevos)
        return len(nuevos)

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
                f.write({
                    'ia_matches': r['va_con_rol'],
                    'ia_suggestion': r['sugerencia'],
                    'ia_reason': r['motivo'],
                    'host': f.host or r['dominio'] or False,
                    'ia_model': r['modelo'],
                    'ia_clasificado': True,
                })

    def action_permitir(self):
        self.write({'state': 'permitido', 'decided_uid': self.env.uid,
                    'decided_at': fields.Datetime.now()})
        return True

    def action_bloquear(self):
        """Agrega el dominio a las reglas del perfil y marca la fila bloqueada.
        El servicio del equipo hace cumplir la regla en su siguiente ciclo."""
        Rule = self.env['foco.policy.rule'].sudo()
        for r in self:
            if r.state == 'bloqueado':
                continue
            host = (r.host or '').strip().lower()
            if not host:
                raise UserError(
                    'Para bloquear "%s" falta el dominio. Escribelo en la columna '
                    'Dominio (p. ej. youtube.com) y vuelve a bloquear.'
                    % (r.title or ''))
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
            r.write({'state': 'bloqueado', 'decided_uid': self.env.uid,
                     'decided_at': fields.Datetime.now()})
        return True
