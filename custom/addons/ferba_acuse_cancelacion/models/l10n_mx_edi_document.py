import logging
import uuid as uuidlib

from markupsafe import Markup, escape

from odoo import api, models

_logger = logging.getLogger(__name__)

# Respuestas de SW capturadas durante una cancelación, por llave única de la llamada.
# Se usa una llave de texto en el contexto (hashable) y este diccionario del proceso;
# la entrada se borra siempre al terminar (try/finally).
_SW_CANCEL_RESPONSES = {}
_CAPTURE_KEY = 'ferba_sw_cancel_capture_key'


class L10nMxEdiDocument(models.Model):
    _inherit = 'l10n_mx_edi.document'

    @api.model
    def _document_sw_call(self, url, headers, payload=None):
        response_json = super()._document_sw_call(url, headers, payload=payload)
        key = self.env.context.get(_CAPTURE_KEY)
        if key and key in _SW_CANCEL_RESPONSES:
            _SW_CANCEL_RESPONSES[key] = response_json
        return response_json

    @api.model
    def _sw_cancel(self, cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=None):
        """Igual que l10n_mx_edi, pero conserva el acuse del SAT que SW devuelve en data.acuse."""
        key = uuidlib.uuid4().hex
        _SW_CANCEL_RESPONSES[key] = None
        try:
            results = super(L10nMxEdiDocument, self.with_context(**{_CAPTURE_KEY: key}))._sw_cancel(
                cfdi_values, credentials, uuid, cancel_reason, cancel_uuid=cancel_uuid,
            )
            response_json = _SW_CANCEL_RESPONSES.get(key)
        finally:
            _SW_CANCEL_RESPONSES.pop(key, None)

        # Solo cuando Odoo consideró la solicitud exitosa (códigos 201/202 de SW).
        if not results.get('errors'):
            self._ferba_save_sw_cancel_acuse(response_json, uuid, cancel_reason, cancel_uuid, cfdi_values)
        return results

    def _ferba_acuse_target(self):
        """El registro de negocio dueño del CFDI que se cancela (factura, pago, etc.)."""
        if len(self) != 1:
            return None
        if self.move_id:
            return self.move_id
        for field_name in ('picking_id', 'payslip_id'):
            if field_name in self._fields and self[field_name]:
                return self[field_name]
        if self.invoice_ids:
            return self.invoice_ids[:1]
        return None

    def _ferba_save_sw_cancel_acuse(self, response_json, uuid, cancel_reason, cancel_uuid, cfdi_values):
        """Guarda el acuse sin poder romper la cancelación: cualquier error queda aislado y anotado."""
        document = self if len(self) == 1 else self.browse()
        target = document._ferba_acuse_target() if document else None
        company = (target.company_id if target and 'company_id' in target._fields and target.company_id
                   else cfdi_values.get('root_company'))
        data = (response_json or {}).get('data') or {}
        codes = data.get('uuid') or {}
        sw_code = codes.get(uuid) or (next(iter(codes.values())) if codes else False)
        acuse = data.get('acuse')

        if not acuse:
            _logger.warning("SW no devolvió acuse al cancelar el UUID %s (código %s)", uuid, sw_code)
            if target:
                target.message_post(body=Markup(
                    "<p>La solicitud de cancelación se envió al SAT a través de SW (código %s), "
                    "pero la respuesta <b>no incluyó acuse</b>; no se guardó ningún acuse.</p>"
                ) % escape(sw_code or 'sin código'))
            return False

        try:
            with self.env.cr.savepoint():
                return self.env['ferba.cfdi.acuse']._ferba_create_from_acuse(
                    acuse=acuse, uuid=uuid, sw_code=sw_code, cancel_reason=cancel_reason,
                    cancel_uuid=cancel_uuid, document=document, target=target, company=company, pac='sw',
                )
        except Exception as error:  # noqa: BLE001 - no debe interrumpir la cancelación
            _logger.exception("No se pudo guardar el acuse de cancelación del UUID %s", uuid)
            if target:
                target.message_post(body=Markup(
                    "<p>La cancelación se solicitó correctamente, pero <b>no se pudo guardar el acuse</b>: %s. "
                    "Avise a sistemas.</p>"
                ) % escape(str(error)[:300]))
            return False
