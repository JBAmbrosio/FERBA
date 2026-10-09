from odoo import api, models, tools


class IrUiMenu(models.Model):
    """Oculta ciertas apps a los VENDEDORES acotados, sin tocar a los demas.

    Odoo no permite "ocultar un menu A UN grupo" de forma nativa: `groups_id` de
    un menu es POSITIVO (se ve si estas en alguno de esos grupos). Para hacerlo
    por rol se filtra aqui, en `_visible_menu_ids`, que es justo el punto por el
    que el cliente web pide los menus visibles del usuario. Solo afecta a quien
    tenga el grupo `group_foco_vendedor_acotado`; para cualquier otro, el
    comportamiento es exactamente el de siempre (se llama a super y ya).
    """
    _inherit = 'ir.ui.menu'

    # Menus RAIZ que un vendedor acotado NO debe ver. Por xmlid, no por id, para
    # no depender de la base. Un modulo no instalado se ignora en silencio.
    _FOCO_OCULTOS_VENDEDOR = (
        'base.menu_management',                # Aplicaciones
        'website.menu_website_configuration',  # Sitio web
        'approvals.approvals_menu_root',       # Aprobaciones
        'maintenance.menu_maintenance_title',  # Mantenimiento
        'hr.menu_hr_root',                      # Empleados
    )

    @api.model
    @tools.ormcache()
    def _foco_menus_ocultos_vendedor(self):
        """Ids a ocultar: las raices configuradas MAS todos sus descendientes
        (ocultar solo la raiz no basta; los submenus seguirian contando). Se
        cachea porque el arbol de menus cambia muy poco; el cache se limpia al
        recargar el registro."""
        raices = self.browse()
        for xid in self._FOCO_OCULTOS_VENDEDOR:
            m = self.env.ref(xid, raise_if_not_found=False)
            if m:
                raices |= m
        todos = raices
        frontera = raices
        while frontera:
            hijos = self.sudo().search([('parent_id', 'in', frontera.ids)]) - todos
            todos |= hijos
            frontera = hijos
        return tuple(todos.ids)

    @api.model
    def _visible_menu_ids(self, *args, **kwargs):
        # *args/**kwargs para no atarse a la firma exacta de Odoo (pasa lo que
        # Odoo pase). Solo filtra para el vendedor acotado; para el resto es super.
        ids = super()._visible_menu_ids(*args, **kwargs)
        if self.env.user.has_group('foco_monitor.group_foco_vendedor_acotado'):
            ocultos = set(self._foco_menus_ocultos_vendedor())
            if ocultos:
                ids = set(ids) - ocultos
        return ids
