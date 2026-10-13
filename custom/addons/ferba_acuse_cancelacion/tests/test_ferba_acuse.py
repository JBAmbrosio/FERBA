"""Pruebas del acuse de cancelación con el flujo REAL de l10n_mx_edi.

Solo se simula la red: el token de SW y la respuesta HTTP de requests.post. Todo lo demás
(wizard de cancelación, _cancel_api, _sw_cancel original de Odoo, creación de documentos) es código real.
El formato de la respuesta sigue el ejemplo del SDK oficial de SW (lunasoft/sw-sdk-php, README):
{"status": "success", "data": {"acuse": "<?xml ...><Acuse ...>", "uuid": {"<UUID>": "201"}}}
"""
from unittest.mock import MagicMock, patch

from freezegun import freeze_time

from odoo.addons.l10n_mx_edi.tests.common import TestMxEdiCommon
from odoo.tests import tagged

ACUSE_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Acuse xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'Fecha="2017-02-07T11:00:54.8788503" RfcEmisor="EKU9003173C9">'
    '<Folios xmlns="http://cancelacfd.sat.gob.mx"><UUID>{uuid}</UUID><EstatusUUID>201</EstatusUUID></Folios>'
    '<Signature Id="SelloSAT" xmlns="http://www.w3.org/2000/09/xmldsig#"><SignatureValue>abc</SignatureValue></Signature>'
    '</Acuse>'
)


@tagged('post_install_l10n', 'post_install', '-at_install')
class TestFerbaAcuseCancelacion(TestMxEdiCommon):

    def setUp(self):
        super().setUp()
        self.env.company.write({
            'l10n_mx_edi_pac': 'sw',
            'l10n_mx_edi_pac_username': 'usuario_prueba',
            'l10n_mx_edi_pac_password': 'clave_prueba',
        })
        # _document_get_sw_token hace requests.post al login de SW (timbrado y cancelación): nunca salir a la red.
        self.patch(type(self.env['l10n_mx_edi.document']), '_document_get_sw_token',
                   lambda _self, _credentials: {'token': 'token_prueba'})

    def _signed_invoice(self):
        with freeze_time('2017-01-01'):
            invoice = self._create_invoice(invoice_date_due='2017-02-01')
        with freeze_time('2017-01-05'), self.with_mocked_pac_sign_success():
            invoice._l10n_mx_edi_cfdi_invoice_try_send()
        self.assertEqual(invoice.l10n_mx_edi_cfdi_state, 'sent')
        self.assertTrue(invoice.l10n_mx_edi_cfdi_uuid)
        return invoice

    def _cancel_with_sw_json(self, invoice, response_json):
        """Cancela por el wizard real; SW 'responde' response_json."""
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = response_json
        posted_urls = []

        def fake_post(url, *args, **kwargs):
            posted_urls.append(url)
            return fake_response

        with freeze_time('2017-02-07'), \
                patch('odoo.addons.l10n_mx_edi.models.l10n_mx_edi_document.requests.post', side_effect=fake_post):
            self.env['l10n_mx_edi.invoice.cancel'] \
                .with_context(invoice.button_request_cancel()['context']) \
                .create({'cancellation_reason': '02'}) \
                .action_cancel_invoice()
        # Se llamó al servicio de cancelación de SW (ambiente de pruebas de la empresa de prueba).
        self.assertEqual(posted_urls, ['https://services.test.sw.com.mx/cfdi33/cancel/csd'])

    def _attachments(self, invoice):
        return self.env['ir.attachment'].search([
            ('res_model', '=', 'account.move'), ('res_id', '=', invoice.id),
            ('name', '=like', 'Acuse_solicitud_cancelacion_%'),
        ])

    def test_01_cancelacion_exitosa_guarda_acuse(self):
        invoice = self._signed_invoice()
        uuid = invoice.l10n_mx_edi_cfdi_uuid
        self._cancel_with_sw_json(invoice, {
            'status': 'success',
            'data': {'acuse': ACUSE_XML.format(uuid=uuid), 'uuid': {uuid: '201'}},
        })

        # El flujo de Odoo siguió normal.
        self.assertIn(invoice.l10n_mx_edi_invoice_document_ids.sorted()[0].state, ('invoice_cancel', 'invoice_cancel_requested'))

        acuse = invoice.ferba_acuse_ids
        self.assertEqual(len(acuse), 1)
        self.assertRecordValues(acuse, [{
            'uuid': uuid,
            'pac': 'sw',
            'sw_code': '201',
            'sw_code_description': 'Solicitud de cancelación recibida',
            'cancellation_reason': '02',
            'sat_date': '2017-02-07T11:00:54.8788503',
            'sat_rfc_emisor': 'EKU9003173C9',
            'sat_uuid': uuid,
            'sat_status': '201',
            'sat_signed': True,
            'parse_error': False,
            'company_id': invoice.company_id.id,
            'move_id': invoice.id,
        }])
        self.assertEqual(invoice.ferba_acuse_count, 1)

        attachment = self._attachments(invoice)
        self.assertEqual(len(attachment), 1)
        self.assertIn(b'<Acuse', attachment.raw)
        self.assertEqual(acuse.attachment_id, attachment)
        self.assertTrue(invoice.message_ids.filtered(lambda m: attachment in m.attachment_ids))

        html = self.env['ir.actions.report']._render_qweb_html(
            'ferba_acuse_cancelacion.action_report_ferba_cfdi_acuse', acuse.ids)[0]
        self.assertIn(uuid.encode(), html)
        self.assertIn(b'EKU9003173C9', html)

        action = invoice.action_ferba_view_acuses()
        self.assertEqual(action['res_id'], acuse.id)

    def test_02_error_de_sw_no_guarda_nada(self):
        invoice = self._signed_invoice()
        self._cancel_with_sw_json(invoice, {
            'status': 'error', 'message': 'CA305', 'messageDetail': 'Certificado no valido',
        })
        self.assertEqual(invoice.l10n_mx_edi_invoice_document_ids.sorted()[0].state, 'invoice_cancel_failed')
        self.assertFalse(invoice.ferba_acuse_ids)
        self.assertFalse(self._attachments(invoice))

    def test_03_exito_sin_acuse_lo_dice_y_no_inventa(self):
        invoice = self._signed_invoice()
        uuid = invoice.l10n_mx_edi_cfdi_uuid
        self._cancel_with_sw_json(invoice, {'status': 'success', 'data': {'uuid': {uuid: '201'}}})
        self.assertIn(invoice.l10n_mx_edi_invoice_document_ids.sorted()[0].state, ('invoice_cancel', 'invoice_cancel_requested'))
        self.assertFalse(invoice.ferba_acuse_ids)
        self.assertFalse(self._attachments(invoice))
        self.assertTrue(invoice.message_ids.filtered(lambda m: 'no incluyó acuse' in (m.body or '')))

    def test_04_acuse_ilegible_se_guarda_tal_cual(self):
        invoice = self._signed_invoice()
        uuid = invoice.l10n_mx_edi_cfdi_uuid
        self._cancel_with_sw_json(invoice, {'status': 'success', 'data': {'acuse': 'esto no es xml', 'uuid': {uuid: '201'}}})
        acuse = invoice.ferba_acuse_ids
        self.assertEqual(len(acuse), 1)
        self.assertTrue(acuse.parse_error)
        self.assertFalse(acuse.sat_status)
        self.assertFalse(acuse.sat_signed)
        self.assertEqual(self._attachments(invoice).raw, b'esto no es xml')
