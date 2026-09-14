from odoo import api, fields, models


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
