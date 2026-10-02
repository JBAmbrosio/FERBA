from odoo import api, models
from odoo.exceptions import UserError

# Reportes de sale.order que SON el documento que ve el cliente: la cotizacion
# con sus precios. Los reportes internos (orden de trabajo, hojas de horas) NO
# van aqui -imprimirlos antes de aprobar no le manda nada al cliente-, por eso la
# guarda no los toca.
REPORTES_COTIZACION = frozenset((
    'sale.report_saleorder',            # "PDF Quote" (sale.action_report_saleorder)
    'sale.report_saleorder_pro_forma',  # Factura PROFORMA
    'sale.report_saleorder_raw',        # "Quotation / Order" del armador de PDF
))


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    # La GUARDA de verdad contra sacar la cotizacion sin aprobar: ni el PDF ni el
    # HTML de la cotizacion se generan hasta que este aprobada. Esta en el motor
    # de reportes, no en la vista, asi que cubre TODAS las vias -imprimir desde el
    # formulario, imprimir desde la lista (seleccionando varias), la URL directa
    # /report/pdf/... y el PDF que se adjunta al mandar un correo-. Ocultar los
    # botones es solo para no enseñar lo que no se puede; esto es lo que lo impide.
    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        self._ferba_exigir_aprobacion_reporte(report_ref, res_ids)
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

    def _render_qweb_html(self, report_ref, docids, data=None):
        self._ferba_exigir_aprobacion_reporte(report_ref, docids)
        return super()._render_qweb_html(report_ref, docids, data=data)

    def _render_qweb_text(self, report_ref, docids, data=None):
        self._ferba_exigir_aprobacion_reporte(report_ref, docids)
        return super()._render_qweb_text(report_ref, docids, data=data)

    @api.model
    def _ferba_exigir_aprobacion_reporte(self, report_ref, res_ids):
        """Detiene el render si alguna de las cotizaciones del reporte no esta
        aprobada. A diferencia de enviar/confirmar/compartir, imprimir NO aprueba
        -ni siquiera a un aprobador-: se aprueba con su boton y despues se imprime.
        Asi un render en lote o en segundo plano nunca aprueba por accidente."""
        if not res_ids:
            return
        try:
            report = self._get_report(report_ref)
        except (ValueError, AttributeError):
            return
        if report.model != 'sale.order' or report.report_name not in REPORTES_COTIZACION:
            return
        ids = list(res_ids) if isinstance(res_ids, (list, tuple, set)) else [res_ids]
        ordenes = self.env['sale.order'].browse(ids).exists()
        bloqueadas = ordenes.filtered(
            lambda o: o.requiere_aprobacion and o.aprobacion_state != 'aprobada')
        if not bloqueadas:
            return
        if self.env.user.share:
            raise UserError(
                'Esta cotizacion todavia no esta liberada. Te avisaremos en cuanto '
                'puedas verla.')
        raise UserError(
            'No se puede imprimir ni enviar la cotizacion hasta que este aprobada: %s.\n\n'
            'Pulsa «Enviar a revision» y espera la aprobacion; ya aprobada se puede imprimir.'
            % ', '.join(bloqueadas.mapped('name')))
