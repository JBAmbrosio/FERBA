from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    ferba_acuse_ids = fields.One2many('ferba.cfdi.acuse', 'move_id', string='Acuses de cancelación')
    ferba_acuse_count = fields.Integer(string='Acuses', compute='_compute_ferba_acuse_count')

    def _ferba_acuse_domain(self):
        self.ensure_one()
        return ['|', ('move_id', '=', self.id), ('invoice_ids', 'in', self.id)]

    def _compute_ferba_acuse_count(self):
        Acuse = self.env['ferba.cfdi.acuse']
        for move in self:
            move.ferba_acuse_count = Acuse.search_count(move._ferba_acuse_domain()) if move.id else 0

    def action_ferba_view_acuses(self):
        self.ensure_one()
        acuses = self.env['ferba.cfdi.acuse'].search(self._ferba_acuse_domain())
        action = self.env['ir.actions.act_window']._for_xml_id('ferba_acuse_cancelacion.action_ferba_cfdi_acuse')
        action['domain'] = [('id', 'in', acuses.ids)]
        action['context'] = {'create': False}
        if len(acuses) == 1:
            action.update({'view_mode': 'form', 'views': [(False, 'form')], 'res_id': acuses.id})
        return action
