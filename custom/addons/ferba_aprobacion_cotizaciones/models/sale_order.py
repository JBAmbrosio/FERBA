import logging

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

GRUPO_APROBADOR = 'ferba_aprobacion_cotizaciones.group_aprobador_cotizaciones'

# Si alguno de estos cambia despues de aprobarse, la aprobacion deja de valer:
# el aprobador vio otra cotizacion. `date_order` NO va (Odoo lo mueve al
# confirmar) ni `state` (lo mueve al enviar).
CAMPOS_QUE_INVALIDAN = {
    'order_line', 'partner_id', 'pricelist_id', 'payment_term_id',
    'currency_id', 'fiscal_position_id', 'validity_date',
}
RESUMEN_ACTIVIDAD = 'Revisar cotizacion'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    aprobacion_state = fields.Selection(
        [('sin', 'Sin revisar'),
         ('revision', 'En revision'),
         ('aprobada', 'Aprobada'),
         ('rechazada', 'Rechazada')],
        string='Aprobacion', default='sin', copy=False, tracking=True, index=True)
    requiere_aprobacion = fields.Boolean(compute='_compute_requiere_aprobacion')
    puede_aprobar = fields.Boolean(compute='_compute_puede_aprobar')
    aprobacion_solicitante_id = fields.Many2one(
        'res.users', string='Mando a revision', copy=False, readonly=True)
    aprobacion_solicitud_fecha = fields.Datetime(
        string='Enviada a revision', copy=False, readonly=True)
    aprobacion_user_id = fields.Many2one(
        'res.users', string='Aprobo / rechazo', copy=False, readonly=True)
    aprobacion_fecha = fields.Datetime(string='Fecha de decision', copy=False, readonly=True)
    aprobacion_motivo = fields.Text(string='Motivo del rechazo', copy=False, readonly=True)

    # ------------------------------------------------------------ computes
    @api.depends('state', 'company_id.ferba_aprobacion_cotizaciones')
    def _compute_requiere_aprobacion(self):
        for order in self:
            # Los pedidos de la tienda en linea los confirma el propio cliente al
            # pagar; ahi no hay vendedor que mande a revision.
            web = 'website_id' in order._fields and order.website_id
            order.requiere_aprobacion = bool(
                order.company_id.ferba_aprobacion_cotizaciones
                and order.state in ('draft', 'sent') and not web)

    @api.depends_context('uid')
    def _compute_puede_aprobar(self):
        puede = self.env.user.has_group(GRUPO_APROBADOR)
        for order in self:
            order.puede_aprobar = puede

    # ------------------------------------------------------------ helpers
    def _ferba_aprobadores(self):
        grupo = self.env.ref(GRUPO_APROBADOR)
        return grupo.user_ids.filtered(lambda u: u.active and not u.share)

    def _ferba_exigir_aprobacion(self):
        """Enviar y confirmar pasan por aqui, vengan del boton o de codigo."""
        bloqueadas = self.filtered(
            lambda o: o.requiere_aprobacion and o.aprobacion_state != 'aprobada')
        if bloqueadas:
            raise UserError(
                'Estas cotizaciones necesitan aprobacion antes de enviarse o confirmarse: %s.\n'
                'Usa «Enviar a revision» y espera a que un aprobador la apruebe.'
                % ', '.join(bloqueadas.mapped('name')))

    def _ferba_solo_aprobadores(self):
        if not self.env.user.has_group(GRUPO_APROBADOR):
            raise AccessError('Solo los aprobadores configurados en Ventas > Configuracion > '
                              'Ajustes pueden aprobar o rechazar cotizaciones.')

    def _ferba_destinatarios_vendedor(self):
        """A quien se le avisa la decision: quien la mando y el vendedor del documento."""
        usuarios = self.aprobacion_solicitante_id | self.user_id
        return usuarios.partner_id.filtered(lambda p: p.id != self.env.user.partner_id.id)

    def _ferba_cerrar_actividades(self, feedback):
        acts = self.activity_ids.filtered(
            lambda a: a.summary and a.summary.startswith(RESUMEN_ACTIVIDAD))
        if acts:
            acts.action_feedback(feedback=feedback)

    def _ferba_url(self):
        self.ensure_one()
        return '%s/odoo/action-sale.action_quotations_with_onboarding/%d' % (self.get_base_url(), self.id)

    def _ferba_nota(self, texto):
        """Rastro en el chatter de la cotizacion. Sin destinatarios a proposito:
        avisar es trabajo del chat, no del correo."""
        self.message_post(
            body=Markup('<p>%s</p>') % escape(texto),
            message_type='notification', subtype_xmlid='mail.mt_note')

    def _ferba_chat(self, partners, texto):
        """Mensaje DIRECTO en Conversaciones, del usuario actual a cada
        destinatario, como si se lo escribiera a mano: le brinca la burbuja del
        chat y el contador. Es la notificacion que FERBA usa de verdad (25-sep:
        "el chat si lo usamos"; a Francesco le llega por ahi, no por correo).
        Nunca tumba la operacion: si el chat falla, la aprobacion sigue y queda
        el rastro en el chatter."""
        Canal = self.env['discuss.channel']
        yo = self.env.user.partner_id
        for partner in partners:
            if partner == yo:
                continue
            try:
                canal = Canal._get_or_create_chat(partners_to=[partner.id])
                canal.message_post(
                    body=Markup('<p>%s <a href="%s">%s</a></p>') % (texto, self._ferba_url(), self.name),
                    message_type='comment', subtype_xmlid='mail.mt_comment')
            except Exception:
                _logger.exception('ferba_aprobacion: no se pudo mandar el chat a %s por %s',
                                  partner.name, self.name)

    # ------------------------------------------------------------ botones
    def action_enviar_revision(self):
        aprobadores = self._ferba_aprobadores()
        if not aprobadores:
            raise UserError('No hay aprobadores configurados. Ve a Ventas > Configuracion > '
                            'Ajustes > Aprobacion de cotizaciones y agrega al menos uno.')
        for order in self:
            if order.state not in ('draft', 'sent'):
                raise UserError('Solo una cotizacion (no confirmada) se manda a revision: %s.' % order.name)
            if order.aprobacion_state == 'revision':
                continue
            order.with_context(ferba_sin_reset=True).write({
                'aprobacion_state': 'revision',
                'aprobacion_solicitante_id': self.env.user.id,
                'aprobacion_solicitud_fecha': fields.Datetime.now(),
                'aprobacion_user_id': False,
                'aprobacion_fecha': False,
                'aprobacion_motivo': False,
            })
            order._ferba_nota('%s mando la cotizacion a revision.' % self.env.user.name)
            order._ferba_chat(
                aprobadores.partner_id,
                'Te mande a revision la cotizacion de %s por %s %s. ¿La apruebas?'
                % (order.partner_id.display_name, order.currency_id.symbol,
                   '{:,.2f}'.format(order.amount_total)))
            for usuario in aprobadores:
                order.activity_schedule(
                    'mail.mail_activity_data_todo', user_id=usuario.id,
                    summary='%s %s' % (RESUMEN_ACTIVIDAD, order.name),
                    note='La mando %s. Aprobar o rechazar desde la cotizacion.' % self.env.user.name)
        return True

    def action_aprobar(self):
        self._ferba_solo_aprobadores()
        for order in self:
            if order.aprobacion_state != 'revision':
                raise UserError('La cotizacion %s no esta en revision.' % order.name)
            order.with_context(ferba_sin_reset=True).write({
                'aprobacion_state': 'aprobada',
                'aprobacion_user_id': self.env.user.id,
                'aprobacion_fecha': fields.Datetime.now(),
                'aprobacion_motivo': False,
            })
            order._ferba_cerrar_actividades('Aprobada')
            order._ferba_nota('%s aprobo la cotizacion.' % self.env.user.name)
            order._ferba_chat(
                order._ferba_destinatarios_vendedor(),
                'Aprobe la cotizacion de %s. Ya la puedes enviar al cliente:'
                % order.partner_id.display_name)
        return True

    def action_rechazar(self):
        self._ferba_solo_aprobadores()
        self.ensure_one()
        if self.aprobacion_state != 'revision':
            raise UserError('La cotizacion %s no esta en revision.' % self.name)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Rechazar cotizacion %s' % self.name,
            'res_model': 'ferba.cotizacion.rechazo',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _ferba_rechazar(self, motivo):
        """La llama el asistente de rechazo, ya con el motivo escrito."""
        self._ferba_solo_aprobadores()
        motivo = (motivo or '').strip()
        if not motivo:
            raise UserError('Escribe el motivo del rechazo: es lo que le llega al vendedor.')
        for order in self:
            order.with_context(ferba_sin_reset=True).write({
                'aprobacion_state': 'rechazada',
                'aprobacion_user_id': self.env.user.id,
                'aprobacion_fecha': fields.Datetime.now(),
                'aprobacion_motivo': motivo,
            })
            order._ferba_cerrar_actividades('Rechazada: %s' % motivo)
            order._ferba_nota('%s rechazo la cotizacion. Motivo: %s' % (self.env.user.name, motivo))
            order._ferba_chat(
                order._ferba_destinatarios_vendedor(),
                'Rechace la cotizacion de %s. Motivo: %s. Corrigela y vuelve a mandarla a revision:'
                % (order.partner_id.display_name, motivo))
        return True

    # ------------------------------------------------------------ compuertas
    def action_quotation_send(self):
        self._ferba_exigir_aprobacion()
        return super().action_quotation_send()

    def action_confirm(self):
        self._ferba_exigir_aprobacion()
        return super().action_confirm()

    def write(self, vals):
        res = super().write(vals)
        # Una aprobacion vale para LO QUE SE APROBO. Si cambian las lineas, el
        # cliente, la lista de precios, el plazo, la moneda, la posicion fiscal
        # o la vigencia, la aprobacion se retira y hay que volver a pedirla.
        # Excepcion: el propio aprobador ajustando algo mientras revisa.
        tocados = CAMPOS_QUE_INVALIDAN & set(vals)
        if tocados and not self.env.context.get('ferba_sin_reset'):
            es_aprobador = self.env.user.has_group(GRUPO_APROBADOR)
            for order in self:
                if order.state not in ('draft', 'sent'):
                    continue
                if order.aprobacion_state == 'aprobada' or (
                        order.aprobacion_state == 'revision' and not es_aprobador):
                    anterior = dict(order._fields['aprobacion_state'].selection)[order.aprobacion_state]
                    order.with_context(ferba_sin_reset=True).write({
                        'aprobacion_state': 'sin',
                        'aprobacion_user_id': False,
                        'aprobacion_fecha': False,
                        'aprobacion_motivo': False,
                    })
                    order._ferba_cerrar_actividades('La cotizacion cambio; se pedira de nuevo')
                    order.message_post(
                        body=Markup('<p>%s</p>') % escape(
                            'La cotizacion cambio (%s) estando «%s»: hay que mandarla a revision otra vez.'
                            % (', '.join(sorted(tocados)), anterior)),
                        message_type='notification')
        return res
