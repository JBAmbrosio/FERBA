from odoo import fields, models


class AccountPaymentTerm(models.Model):
    _inherit = 'account.payment.term'

    l10n_mx_edi_force_ppd = fields.Boolean(
        string="Forzar PPD (CFDI)",
        help="Si esta activado, las facturas que usen este termino de pago toman "
             "la Politica de pago CFDI 'PPD' automaticamente, sin importar el plazo "
             "ni la fecha de vencimiento. Pensado para ventas a credito que se "
             "liquidan de forma diferida o en parcialidades.\n"
             "Recuerda: una factura PPD obliga a emitir Complemento de Pago (REP) "
             "por cada abono del cliente.",
    )
