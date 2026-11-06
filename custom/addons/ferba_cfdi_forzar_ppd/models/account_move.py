from odoo import api, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.depends(
        'invoice_payment_term_id',
        'invoice_date',
        'invoice_date_due',
        'move_type',
        'company_id',
    )
    def _compute_l10n_mx_edi_payment_policy(self):
        # 1) Dejar que l10n_mx_edi calcule su valor estandar (PUE/PPD segun el
        #    vencimiento y las parcialidades, o False cuando no aplica CFDI).
        super()._compute_l10n_mx_edi_payment_policy()
        # 2) Forzar PPD solo en las facturas cuyo termino lo pide. El guard
        #    "in ('PUE', 'PPD')" asegura que solo tocamos facturas donde la
        #    politica aplica (el estandar ya devolvio False para lo que no es
        #    CFDI mexicano o aun no tiene fecha), sin reimplementar esa logica.
        for move in self:
            term = move.invoice_payment_term_id
            if term.l10n_mx_edi_force_ppd and move.l10n_mx_edi_payment_policy in ('PUE', 'PPD'):
                move.l10n_mx_edi_payment_policy = 'PPD'
