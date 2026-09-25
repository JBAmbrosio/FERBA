from odoo import api, fields, models

GRUPO_APROBADOR = 'ferba_aprobacion_cotizaciones.group_aprobador_cotizaciones'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ferba_aprobacion_cotizaciones = fields.Boolean(
        related='company_id.ferba_aprobacion_cotizaciones', readonly=False)

    # Los aprobadores son los miembros de un grupo (asi los botones y el menu
    # «Por aprobar» se restringen con `groups=`, sin reglas a mano). Aqui se ven
    # y se editan como una lista, para que quien administra Ventas no tenga que
    # ir a Ajustes > Usuarios. No se almacena: es una vista del grupo, no puede
    # haber dos verdades sobre quien aprueba.
    ferba_aprobadores_ids = fields.Many2many(
        'res.users', string='Aprobadores',
        compute='_compute_ferba_aprobadores', inverse='_inverse_ferba_aprobadores',
        help='Reciben las cotizaciones enviadas a revision y las aprueban o rechazan. '
             'Pon a mas de una persona para cubrir vacaciones y ausencias.')

    def _grupo_aprobador(self):
        return self.env.ref(GRUPO_APROBADOR, raise_if_not_found=False)

    @api.depends('company_id')
    def _compute_ferba_aprobadores(self):
        grupo = self._grupo_aprobador()
        for rec in self:
            rec.ferba_aprobadores_ids = grupo.user_ids.filtered(lambda u: not u.share) if grupo else False

    def _inverse_ferba_aprobadores(self):
        grupo = self._grupo_aprobador()
        if not grupo:
            return
        for rec in self:
            grupo.sudo().write({'user_ids': [(6, 0, rec.ferba_aprobadores_ids.ids)]})
