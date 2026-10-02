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
    'order_line': 'las lineas',
    'partner_id': 'el cliente',
    'pricelist_id': 'la lista de precios',
    'payment_term_id': 'el plazo de pago',
    'currency_id': 'la moneda',
    'fiscal_position_id': 'la posicion fiscal',
    'validity_date': 'la vigencia',
}
RESUMEN_ACTIVIDAD = 'Revisar cotizacion'
# Un aprobador puede aprobar desde cualquiera de estos; no hace falta que alguien
# la mande a revision primero (19.0.1.2.0).
ESTADOS_QUE_SE_APRUEBAN = ('sin', 'revision', 'rechazada')

# Botones del encabezado que SI se ven mientras la cotizacion no esta aprobada:
# el flujo (mandarla a revision; aprobar y rechazar, que solo ven los
# aprobadores) y cancelarla. Todos los demas -Enviar, Confirmar, Vista previa,
# Imprimir, PROFORMA, Crear factura y los de Studio (Crear almacen, Lanzar
# fabricacion, Crear CC)- aparecen hasta que este aprobada (19.0.1.4.0).
BOTONES_ANTES_DE_APROBAR = frozenset({
    'action_enviar_revision', 'action_aprobar', 'action_rechazar', 'action_cancel'})
SIN_APROBAR = "(requiere_aprobacion and aprobacion_state != 'aprobada')"


def ocultar_botones_sin_aprobar(form):
    """Agrega la condicion `SIN_APROBAR` al `invisible` de cada boton del
    encabezado del formulario, salvo los del flujo. Recibe la vista YA COMBINADA
    (todos los modulos y Studio), asi que cubre tambien los botones que se
    agreguen despues, sin un xpath por boton: un xpath a un boton que pone otro
    modulo o Studio no lo encuentra cuando corre antes que ellos y rompe el
    formulario. Solo el encabezado del formulario principal (no el de una
    subvista de lineas) y solo si ese encabezado trae los dos campos que usa la
    condicion. Devuelve cuantos botones toco."""
    tocados = 0
    for header in form.findall('header'):
        campos = {f.get('name') for f in header.iter('field')}
        if not {'requiere_aprobacion', 'aprobacion_state'} <= campos:
            continue
        for boton in header.iter('button'):
            if boton.get('name') in BOTONES_ANTES_DE_APROBAR:
                continue
            actual = (boton.get('invisible') or '').strip()
            if SIN_APROBAR in actual:
                continue
            boton.set('invisible', '(%s) or %s' % (actual, SIN_APROBAR) if actual else SIN_APROBAR)
            tocados += 1
    return tocados


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
    aprobacion_retirada = fields.Char(
        string='Aprobacion retirada', copy=False, readonly=True,
        help='Por que dejo de valer la ultima aprobacion: que cambio, quien y cuando. '
             'Se ve arriba de la cotizacion y se limpia al mandarla a revision o al aprobarla.')

    # ------------------------------------------------------------ vista
    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form':
            ocultar_botones_sin_aprobar(arch)
        return arch, view

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
    def _ferba_aprobadores(self, company=None):
        """Aprobadores vivos. Con `company`, solo los que pueden entrar a esa
        empresa: asignarle una actividad o mandarle un chat de una cotizacion
        que no puede abrir es un error seguro (la actividad ni se deja crear)."""
        grupo = self.env.ref(GRUPO_APROBADOR)
        usuarios = grupo.user_ids.filtered(lambda u: u.active and not u.share)
        if company is not None:
            usuarios = usuarios.filtered(lambda u: company in u.company_ids)
        return usuarios

    def _ferba_exigir_aprobacion(self, accion):
        """Enviar y confirmar pasan por aqui, vengan del boton o de codigo.

        - Aprobada: pasa.
        - Cliente en el portal (usuario compartido): se le dice, sin jerga
          interna, que todavia no esta liberada. Llega aqui si la cotizacion
          cambio despues de enviarse o si le compartieron la liga de «Vista
          previa» de un borrador: en los dos casos la direccion no ha visto lo
          que estaria aceptando.
        - Aprobador: se aprueba y sigue. Que el aprobador tenga que mandarse la
          cotizacion a revision a si mismo para poder enviarla es un tramite sin
          sentido (Francesco con S02316, 26-sep).
        - Vendedor: se detiene con el estado de cada cotizacion, quien puede
          aprobar y que hacer si no ve el boton (un formulario cargado antes de
          un despliegue no lo tiene, pero el servidor ya aplica la regla).
        """
        bloqueadas = self.filtered(
            lambda o: o.requiere_aprobacion and o.aprobacion_state != 'aprobada')
        if not bloqueadas:
            return
        if self.env.user.share:
            raise UserError(
                'Esta cotizacion todavia no esta liberada por %s. Le avisaremos en cuanto '
                'pueda aceptarla.' % ', '.join(set(bloqueadas.mapped('company_id.name'))))
        if self.env.user.has_group(GRUPO_APROBADOR):
            bloqueadas._ferba_aprobar(
                nota='%%s aprobo la cotizacion al %s.' % accion,
                chat='Aprobe la cotizacion de %%s al %s.' % accion)
            return
        etiquetas = dict(self._fields['aprobacion_state'].selection)
        detalle = ', '.join(
            '%s (%s)' % (o.name, etiquetas[o.aprobacion_state].lower()) for o in bloqueadas)
        aprobadores = (self._ferba_aprobadores(bloqueadas[0].company_id)
                       or self._ferba_aprobadores())
        quien = ' o '.join(aprobadores.mapped('name')) or 'un aprobador (hoy no hay ninguno configurado)'
        raise UserError(
            'Antes de enviarse o confirmarse, estas cotizaciones necesitan la aprobacion '
            'de %s: %s.\n\n'
            'Pulsa «Enviar a revision» y espera la respuesta: te llega por chat.\n\n'
            'Si no ves el boton «Enviar a revision», tienes en pantalla una version anterior '
            'del formulario: recarga la pagina (F5) o cierra y vuelve a abrir Odoo.'
            % (quien, detalle))

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

    def _ferba_aprobar(self, nota, chat):
        """Marca aprobada, cierra las actividades, deja rastro y avisa al vendedor.
        `nota` y `chat` llevan un %s: el nombre del aprobador y el del cliente."""
        for order in self:
            order.with_context(ferba_sin_reset=True).write({
                'aprobacion_state': 'aprobada',
                'aprobacion_user_id': self.env.user.id,
                'aprobacion_fecha': fields.Datetime.now(),
                'aprobacion_motivo': False,
                'aprobacion_retirada': False,
            })
            order._ferba_cerrar_actividades('Aprobada')
            order._ferba_nota(nota % self.env.user.name)
            order._ferba_chat(order._ferba_destinatarios_vendedor(),
                              chat % order.partner_id.display_name)

    # ------------------------------------------------------------ botones
    def action_enviar_revision(self):
        for order in self:
            if order.state not in ('draft', 'sent'):
                raise UserError('Solo una cotizacion (no confirmada) se manda a revision: %s.' % order.name)
            if order.aprobacion_state == 'revision':
                continue
            aprobadores = self._ferba_aprobadores(order.company_id)
            if not aprobadores:
                raise UserError(
                    'No hay aprobadores que puedan entrar a la empresa %s. Ve a Ventas > '
                    'Configuracion > Ajustes > Aprobacion de cotizaciones y agrega uno (y revisa '
                    'que tenga esa empresa entre sus empresas permitidas).' % order.company_id.name)
            order.with_context(ferba_sin_reset=True).write({
                'aprobacion_state': 'revision',
                'aprobacion_solicitante_id': self.env.user.id,
                'aprobacion_solicitud_fecha': fields.Datetime.now(),
                'aprobacion_user_id': False,
                'aprobacion_fecha': False,
                'aprobacion_motivo': False,
                'aprobacion_retirada': False,
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
            if order.state not in ('draft', 'sent'):
                raise UserError('Solo una cotizacion (no confirmada) se aprueba: %s.' % order.name)
            if order.aprobacion_state not in ESTADOS_QUE_SE_APRUEBAN:
                raise UserError('La cotizacion %s ya esta aprobada.' % order.name)
            if order.aprobacion_state == 'revision':
                nota = '%s aprobo la cotizacion.'
            else:
                nota = '%s aprobo la cotizacion directamente (no estaba en revision).'
            order._ferba_aprobar(
                nota=nota,
                chat='Aprobe la cotizacion de %s. Ya la puedes enviar al cliente:')
        return True

    def action_rechazar(self):
        self._ferba_solo_aprobadores()
        self.ensure_one()
        if self.state not in ('draft', 'sent') or self.aprobacion_state not in ('sin', 'revision'):
            raise UserError('La cotizacion %s no esta pendiente de aprobacion.' % self.name)
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
                'aprobacion_retirada': False,
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
        self._ferba_exigir_aprobacion('enviarla')
        return super().action_quotation_send()

    def action_confirm(self):
        self._ferba_exigir_aprobacion('confirmarla')
        return super().action_confirm()

    def action_share(self):
        # «Compartir» genera una liga del portal: el cliente ve la cotizacion.
        # Es sacarla al cliente, igual que enviarla, asi que pasa por la misma
        # compuerta (un aprobador que comparte, aprueba en el acto).
        self._ferba_exigir_aprobacion('compartirla con el cliente')
        return super().action_share()

    def write(self, vals):
        res = super().write(vals)
        # Una aprobacion vale para LO QUE SE APROBO. Si cambian las lineas, el
        # cliente, la lista de precios, el plazo, la moneda, la posicion fiscal
        # o la vigencia, la aprobacion se retira y hay que volver a pedirla.
        # Excepcion: el propio aprobador ajustando algo mientras revisa.
        tocados = set(CAMPOS_QUE_INVALIDAN) & set(vals)
        if tocados and not self.env.context.get('ferba_sin_reset'):
            es_aprobador = self.env.user.has_group(GRUPO_APROBADOR)
            for order in self:
                if order.state not in ('draft', 'sent'):
                    continue
                if order.aprobacion_state == 'aprobada' or (
                        order.aprobacion_state == 'revision' and not es_aprobador):
                    anterior = dict(order._fields['aprobacion_state'].selection)[order.aprobacion_state]
                    cuando = fields.Datetime.context_timestamp(
                        order, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M')
                    que = ', '.join(CAMPOS_QUE_INVALIDAN[f] for f in sorted(tocados))
                    # Se guarda en la cotizacion (aviso arriba del formulario) y en
                    # el chatter. Solo en el chatter nadie lo veia: el vendedor
                    # pulsaba Enviar y no entendia por que se detenia.
                    order.with_context(ferba_sin_reset=True).write({
                        'aprobacion_state': 'sin',
                        'aprobacion_user_id': False,
                        'aprobacion_fecha': False,
                        'aprobacion_motivo': False,
                        'aprobacion_retirada': 'cambio %s (%s, %s) estando «%s»'
                                               % (que, self.env.user.name, cuando, anterior),
                    })
                    order._ferba_cerrar_actividades('La cotizacion cambio; se pedira de nuevo')
                    order.message_post(
                        body=Markup('<p>%s</p>') % escape(
                            'La cotizacion cambio (%s) estando «%s»: hay que mandarla a revision otra vez.'
                            % (que, anterior)),
                        message_type='notification')
        return res
