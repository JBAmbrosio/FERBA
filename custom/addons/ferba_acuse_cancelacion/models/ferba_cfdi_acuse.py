import base64
import logging

from lxml import etree

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Significado de los códigos por UUID según la documentación de SW ("Cancelación de CFDI").
# Cualquier otro código se muestra tal cual, sin interpretarlo.
SW_UUID_CODES = {
    '201': 'Solicitud de cancelación recibida',
    '202': 'El CFDI ya estaba cancelado',
}


class FerbaCfdiAcuse(models.Model):
    _name = 'ferba.cfdi.acuse'
    _description = 'Acuse de solicitud de cancelación CFDI'
    _order = 'create_date desc, id desc'
    _rec_name = 'uuid'

    company_id = fields.Many2one('res.company', string='Empresa', required=True, index=True, readonly=True)
    document_id = fields.Many2one('l10n_mx_edi.document', string='Documento CFDI', readonly=True, ondelete='set null')
    move_id = fields.Many2one('account.move', string='Factura / pago', readonly=True, index=True, ondelete='set null')
    invoice_ids = fields.Many2many('account.move', string='Facturas (factura global)', readonly=True)
    partner_id = fields.Many2one(related='move_id.partner_id', string='Cliente / proveedor')
    res_model = fields.Char(string='Modelo del documento', readonly=True)
    res_id = fields.Many2oneReference(string='Documento', model_field='res_model', readonly=True)

    uuid = fields.Char(string='UUID', required=True, readonly=True, index=True)
    pac = fields.Char(string='PAC', readonly=True)
    cancellation_reason = fields.Char(string='Motivo solicitado', readonly=True)
    substitution_uuid = fields.Char(string='Folio de sustitución', readonly=True)
    sw_code = fields.Char(string='Código de SW', readonly=True)
    sw_code_description = fields.Char(string='Significado del código', compute='_compute_sw_code_description')

    # Datos leídos del XML del acuse (vacíos si el acuse no los trae).
    sat_date = fields.Char(string='Fecha del acuse (SAT)', readonly=True)
    sat_rfc_emisor = fields.Char(string='RFC emisor (acuse)', readonly=True)
    sat_uuid = fields.Char(string='UUID (acuse)', readonly=True)
    sat_status = fields.Char(string='EstatusUUID (acuse)', readonly=True)
    sat_signed = fields.Boolean(string='Trae firma del SAT', readonly=True)
    parse_error = fields.Char(string='Error al leer el acuse', readonly=True)

    acuse_xml = fields.Binary(string='Acuse (XML)', attachment=True, readonly=True)
    acuse_filename = fields.Char(readonly=True)
    attachment_id = fields.Many2one('ir.attachment', string='Adjunto en el documento', readonly=True, ondelete='set null')

    @api.depends('sw_code')
    def _compute_sw_code_description(self):
        for acuse in self:
            if not acuse.sw_code:
                acuse.sw_code_description = False
            else:
                acuse.sw_code_description = SW_UUID_CODES.get(acuse.sw_code, 'Código no documentado por SW')

    @api.model
    def _ferba_parse_acuse(self, acuse):
        """Lee los datos del XML del acuse del SAT. Nunca inventa valores: si un dato no está, queda vacío."""
        result = {'sat_signed': False}
        raw = acuse.encode('utf-8') if isinstance(acuse, str) else acuse
        try:
            root = etree.fromstring(raw)
        except (etree.XMLSyntaxError, ValueError) as error:
            result['parse_error'] = str(error)[:250]
            return result

        def first_text(local_name):
            nodes = root.xpath("//*[local-name()=$n]", n=local_name)
            return nodes[0].text.strip() if nodes and nodes[0].text else False

        result.update({
            'sat_date': root.get('Fecha') or False,
            'sat_rfc_emisor': root.get('RfcEmisor') or False,
            'sat_uuid': first_text('UUID'),
            'sat_status': first_text('EstatusUUID'),
            'sat_signed': bool(root.xpath("//*[local-name()='Signature']")),
        })
        return result

    @api.model
    def _ferba_create_from_acuse(self, *, acuse, uuid, sw_code, cancel_reason, cancel_uuid, document, target, company, pac):
        """Crea el registro del acuse y, si hay documento destino, adjunta el XML y deja nota."""
        raw = acuse.encode('utf-8') if isinstance(acuse, str) else acuse
        filename = f'Acuse_solicitud_cancelacion_{uuid}.xml'
        values = {
            'company_id': company.id,
            'document_id': document.id if document else False,
            'uuid': uuid,
            'pac': pac,
            'cancellation_reason': cancel_reason or False,
            'substitution_uuid': cancel_uuid or False,
            'sw_code': sw_code or False,
            'acuse_xml': base64.b64encode(raw),
            'acuse_filename': filename,
            **self._ferba_parse_acuse(raw),
        }
        if target:
            values.update({'res_model': target._name, 'res_id': target.id})
            if target._name == 'account.move':
                values['move_id'] = target.id
        if document and document.invoice_ids and not document.move_id:
            values['invoice_ids'] = [(6, 0, document.invoice_ids.ids)]
        acuse_record = self.sudo().create(values)

        if target:
            attachment = self.env['ir.attachment'].sudo().create({
                'name': filename,
                'raw': raw,
                'mimetype': 'application/xml',
                'res_model': target._name,
                'res_id': target.id,
            })
            acuse_record.attachment_id = attachment
            code_text = f"{sw_code} ({acuse_record.sw_code_description})" if sw_code else 'sin código'
            body = self.env['ir.qweb']._render('ferba_acuse_cancelacion.message_acuse_saved', {
                'acuse': acuse_record,
                'code_text': code_text,
            })
            target.message_post(body=body, attachment_ids=attachment.ids)
        return acuse_record
