from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

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

    def _ferba_avisar(self, texto, partners):
        """Mensaje en el chatter que ademas llega a la bandeja (y al correo, segun
        la preferencia de cada quien) de los destinatarios."""
        self.message_post(
            body=Markup('<p>%s</p>') % escape(texto),
            partner_ids=partners.ids,
            subtype_xmlid='mail.mt_comment',
            message_type='comment')

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
            order._ferba_avisar(
                '%s mando a revision la cotizacion %s (%s, %s %s). Esta pendiente de aprobacion.'
                % (self.env.user.name, order.name, order.partner_id.display_name,
                   order.currency_id.symbol, '{:,.2f}'.format(order.amount_total)),
                aprobadores.partner_id)
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
            order._ferba_avisar(
                '%s aprobo la cotizacion %s. Ya se puede enviar al cliente.'
                % (self.env.user.name, order.name),
                order._ferba_destinatarios_vendedor())
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
            order._ferba_avisar(
                '%s rechazo la cotizacion %s. Motivo: %s. Corrigela y vuelve a mandarla a revision.'
                % (self.env.user.name, order.name, motivo),
                order._ferba_destinatarios_vendedor())
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
