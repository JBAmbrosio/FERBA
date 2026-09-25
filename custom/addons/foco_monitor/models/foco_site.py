from urllib.parse import urlparse

from odoo import api, fields, models
from odoo.exceptions import UserError


class FocoSite(models.Model):
    """Sitio web descubierto, clasificable igual que una aplicacion.

    POR QUE EXISTE
        El agente ya sabia en que sitio estaba la pestana activa, pero ese dato
        no pesaba: la categoria del renglon la ponia la APP, asi que una hora en
        el ERP y una hora en YouTube valian lo mismo porque las dos son
        "chrome.exe". Para medir productividad eso es justo lo que hay que
        distinguir, y hoy el navegador es donde pasa la mayor parte del dia.

    COMO SE LLENA
        Se DESCUBRE solo, igual que el catalogo de apps: nunca se trae una lista
        cocida de "youtube = distraccion". Quien clasifica es el admin, porque
        el mismo sitio es trabajo en un puesto y distraccion en otro (YouTube es
        distraccion en contabilidad y herramienta en marketing).
    """

    _name = 'foco.site'
    _description = 'Sitio web descubierto'
    _order = 'last_seen desc'

    _host_uniq = models.Constraint('unique(host)',
                                   'Ese sitio ya existe en el catalogo.')

    name = fields.Char(required=True)
    host = fields.Char(
        string='Dominio', required=True, index=True,
        help='Host completo a proposito: console.aws.amazon.com no es lo mismo '
             'que amazon.com.')
    category_id = fields.Many2one(
        'foco.category', string='Categoria', ondelete='set null',
        help='La asigna el admin. Vacio = el renglon hereda la categoria de la '
             'aplicacion (normalmente "Navegador"), que es el comportamiento '
             'anterior. Clasificar el sitio solo AFINA, nunca rompe.')
    first_seen = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_seen = fields.Datetime(readonly=True)
    classified = fields.Boolean(compute='_compute_classified', store=True)

    @api.depends('category_id')
    def _compute_classified(self):
        for rec in self:
            rec.classified = bool(rec.category_id)

    @api.model
    def _get_or_create(self, host):
        host = (host or '').strip().lower()[:255]
        if not host:
            return self.browse()
        site = self.search([('host', '=', host)], limit=1)
        now = fields.Datetime.now()
        if site:
            site.write({'last_seen': now})
        else:
            site = self.create({'host': host, 'name': host, 'last_seen': now})
        return site

    @api.model
    def _normalizar_host(self, valor):
        """Lo que la persona escribe -> el host como lo reporta el agente.

        Acepta 'https://web.whatsapp.com/algo', 'www.youtube.com' o
        'YouTube.com' y devuelve 'web.whatsapp.com' / 'youtube.com': la misma
        normalizacion que `browser_url.parse_host` en el agente, para que una
        regla escrita a mano case con lo que llega del equipo. Vacio si no
        parece un dominio.
        """
        v = (valor or '').strip().lower()
        if not v or ' ' in v:
            return ''
        try:
            u = urlparse(v if '://' in v else 'http://' + v)
        except ValueError:
            return ''
        host = (u.hostname or '').strip('.')
        if host.startswith('www.'):
            host = host[4:]
        if not host or ('.' not in host and host != 'localhost'):
            return ''
        return host[:255]

    @api.model
    def name_create(self, name):
        """Escribir el dominio en un selector basta para tener el sitio.

        Existe para las reglas de monitoreo: «vigilar WhatsApp Web» se decide
        antes de que la persona lo abra, asi que el sitio puede no estar en el
        catalogo todavia. Si ya esta, se devuelve el que hay: el catalogo es
        unico por host.
        """
        host = self._normalizar_host(name)
        if not host:
            raise UserError(
                'Escribe el dominio del sitio tal como aparece en el navegador, '
                'por ejemplo web.whatsapp.com.')
        site = self.search([('host', '=', host)], limit=1)
        if not site:
            site = self.create({'host': host, 'name': host})
        return site.id, site.display_name

    def action_ver_uso(self):
        """Abre el detalle de uso de este sitio."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.host,
            'res_model': 'foco.usage',
            'view_mode': 'list,pivot,graph',
            'domain': [('site_id', '=', self.id)],
            'context': {'search_default_g_emp': 1},
        }

    def action_bloquear_en_perfil(self):
        """Agrega ESTE sitio como regla de bloqueo del perfil desde donde se llamo.

        El perfil viaja en el contexto (`foco_policy_id`) porque el boton vive
        dentro de la lista «lo que se escapa» del formulario de un perfil: ahi no
        hay ambiguedad sobre a cual se agrega, y preguntarlo con un dialogo
        convertiria un clic en tres.

        El caso que esto resuelve es el de `youtu.be`: bloquear `youtube.com` no
        lo alcanza -son dominios distintos con el mismo contenido- y el
        administrador se queda creyendo que cerro YouTube. El sitio ya esta en
        el catalogo porque alguien lo abrio, asi que en vez de que lo adivine y
        lo escriba, se bloquea de la lista donde ya aparecio.
        """
        self.ensure_one()
        pid = self.env.context.get('foco_policy_id')
        if not pid:
            raise UserError(
                'Este boton se usa desde el formulario de un perfil de '
                'navegacion, que es lo que dice a cual se agrega la regla.')
        perfil = self.env['foco.policy'].browse(pid)
        Rule = self.env['foco.policy.rule']
        # Si ya hay una regla con ese patron no se duplica: el boton puede
        # quedar visible un instante mas por el refresco de la pantalla y dos
        # clics no deben dejar dos renglones iguales.
        if Rule.search_count([('policy_id', '=', perfil.id),
                              ('pattern', '=', self.host)]):
            return True
        Rule.create({
            'policy_id': perfil.id,
            'pattern': self.host,
            'action': 'block',
            'sequence': max(perfil.rule_ids.mapped('sequence') or [0]) + 10,
            'note': 'Visto en la empresa y clasificado como no productivo',
        })
        return True
