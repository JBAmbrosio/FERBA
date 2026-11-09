"""Ajustes del Tablero Operativo: quien puede verlo (configurable).

La visibilidad se apoya en el grupo `group_ferba_operativo_user`:
- "Todos los usuarios internos": base.group_user IMPLICA el grupo (todos lo
  tienen y los usuarios nuevos tambien, automaticamente).
- "Solo usuarios seleccionados": se quita esa implicacion y la membresia del
  grupo queda EXACTAMENTE en los usuarios elegidos.
El estado real vive en el grupo; aqui solo se lee y se escribe.
"""

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ferba_operativo_visibilidad = fields.Selection(
        selection=[('todos', 'Todos los usuarios internos'),
                   ('seleccion', 'Solo usuarios seleccionados')],
        string='¿Quién puede ver el Tablero Operativo?',
        default='todos',
    )
    ferba_operativo_user_ids = fields.Many2many(
        'res.users', string='Usuarios con acceso',
        domain=[('share', '=', False)],
        help='Usuarios internos que podrán ver el Tablero Operativo.')

    def _grupos_operativo(self):
        grupo = self.env.ref('ferba_tablero_operativo.group_ferba_operativo_user',
                             raise_if_not_found=False)
        g_user = self.env.ref('base.group_user', raise_if_not_found=False)
        return grupo, g_user

    @api.model
    def get_values(self):
        res = super().get_values()
        grupo, g_user = self._grupos_operativo()
        todos = bool(grupo and g_user and grupo in g_user.implied_ids)
        res.update(
            ferba_operativo_visibilidad='todos' if todos else 'seleccion',
            # En "todos" el detalle no se usa (y la lista seria enorme): vacio.
            # Odoo 19: la membresia del grupo es `user_ids` (antes `users`).
            ferba_operativo_user_ids=[(6, 0, [] if todos else grupo.user_ids.ids)]
            if grupo else [(6, 0, [])],
        )
        return res

    def set_values(self):
        super().set_values()
        grupo, g_user = self._grupos_operativo()
        if not grupo or not g_user:
            return
        if self.ferba_operativo_visibilidad == 'todos':
            # Todos los internos: que group_user implique el grupo.
            if grupo not in g_user.implied_ids:
                g_user.sudo().write({'implied_ids': [(4, grupo.id)]})
        else:
            # Solo seleccionados: quitar la implicacion y fijar la membresia
            # EXACTA. Si no eligieron a nadie, se incluye al admin que configura
            # para no dejar el tablero sin nadie por accidente.
            if grupo in g_user.implied_ids:
                g_user.sudo().write({'implied_ids': [(3, grupo.id)]})
            usuarios = self.ferba_operativo_user_ids or self.env.user
            grupo.sudo().write({'user_ids': [(6, 0, usuarios.ids)]})
