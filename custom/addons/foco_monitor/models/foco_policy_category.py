from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FocoPolicyCategory(models.Model):
    """La clasificacion de UNA app o UN sitio PARA UN PERFIL. Gana sobre la
    categoria global.

    POR QUE EXISTE (reunion 9-oct-2026)
        La categoria de una app o un sitio era GLOBAL: WhatsApp = Productiva para
        TODOS. Pero el cliente lo necesita por ROL -"a compras y ventas WhatsApp
        les cuenta como trabajo; a Oficina tecnica, como distraccion"-. El mismo
        patron que ya decide el BLOQUEO por perfil (foco.policy), ahora decide
        tambien el PESO en el indice.

    COMO MANDA
        En `foco.usage._compute_category_id` el orden es, de lo mas especifico a
        lo general: grupo de WhatsApp > SITIO de este perfil > sitio global >
        APP de este perfil > app global. Lo que un perfil no clasifica aqui usa
        la clasificacion global, que sigue siendo el default: esto solo AFINA.

    A QUIEN APLICA
        A los equipos cuyo perfil EFECTIVO es este (el propio, o el perfil por
        omision si no tienen uno), igual que `foco.policy._perfil_vigente`.
    """
    _name = 'foco.policy.category'
    _description = 'Clasificacion de una app o un sitio para un perfil'
    _order = 'policy_id, app_id, site_id'

    _app_uniq = models.Constraint('unique(policy_id, app_id)',
                                  'Esa app ya tiene clasificacion en este perfil.')
    _site_uniq = models.Constraint('unique(policy_id, site_id)',
                                   'Ese sitio ya tiene clasificacion en este perfil.')

    policy_id = fields.Many2one('foco.policy', string='Perfil', required=True,
                                ondelete='cascade', index=True)
    app_id = fields.Many2one('foco.app', string='Aplicacion',
                             ondelete='cascade', index=True)
    site_id = fields.Many2one('foco.site', string='Sitio',
                              ondelete='cascade', index=True)
    category_id = fields.Many2one('foco.category', string='Categoria en este perfil',
                                  required=True, ondelete='cascade')

    @api.constrains('app_id', 'site_id')
    def _check_uno(self):
        for r in self:
            if bool(r.app_id) == bool(r.site_id):
                raise ValidationError(
                    'Cada renglon clasifica UNA app o UN sitio para el perfil: '
                    'exactamente uno, no los dos ni ninguno.')

    # ---------------------------------------------------- recalculo del historico
    def _rows(self):
        """Las filas de uso que esta clasificacion afecta: las de los equipos cuyo
        perfil efectivo es este, para esa app o ese sitio. Se usa para recalcular
        la categoria del historico cuando el override cambia (si no, el pasado
        quedaria con la categoria vieja)."""
        Usage = self.env['foco.usage'].sudo()
        Comp = self.env['foco.computer'].sudo()
        Settings = self.env['foco.settings'].sudo()
        rows = Usage.browse()
        for o in self:
            if not o.policy_id:
                continue
            comps = Comp.search([('policy_id', '=', o.policy_id.id)])
            # Si este perfil es el de omision DE SU EMPRESA, tambien los equipos
            # sin perfil propio (o con el suyo archivado) DE ESA EMPRESA lo
            # adoptan; los de otra empresa se rigen por el de la suya.
            try:
                ajustes = Settings.de_empresa(o.policy_id.company_id, crear=False)
                default_pol = ajustes.default_policy_id.filtered('active')
            except Exception:
                ajustes, default_pol = Settings.browse(), self.env['foco.policy']
            if default_pol and o.policy_id == default_pol:
                dom = ['|', ('policy_id', '=', False), ('policy_id.active', '=', False)]
                de_empresa = ajustes._dom_equipos_empresa() if len(ajustes) == 1 else []
                if de_empresa:
                    dom = ['&'] + de_empresa + dom
                comps |= Comp.search(dom)
            if not comps:
                continue
            dom = [('computer_id', 'in', comps.ids)]
            if o.app_id:
                dom.append(('app_id', '=', o.app_id.id))
            elif o.site_id:
                dom.append(('site_id', '=', o.site_id.id))
            rows |= Usage.search(dom)
        return rows

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._rows()._compute_category_id()
        return recs

    def write(self, vals):
        antes = self._rows()
        res = super().write(vals)
        (antes | self._rows())._compute_category_id()
        return res

    def unlink(self):
        antes = self._rows()
        res = super().unlink()
        antes.exists()._compute_category_id()
        return res
