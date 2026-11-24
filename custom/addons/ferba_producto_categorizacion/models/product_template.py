import logging
from collections import defaultdict

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.ferba_categoria_sat.models.product_template import EMPRESA_POR_CATEGORIA, GRUPO_GESTOR

from ..hooks import PARAM_RAIZ

_logger = logging.getLogger(__name__)

ESTADOS = [
    ('pendiente', 'Por categorizar'),
    ('categorizado', 'Categorizado'),
    ('rechazado', 'Alta rechazada'),
]
RESUMEN_ACTIVIDAD = 'Categorizar producto'
# Mas altas que esto en una sola operacion (una importacion) = un solo chat con la liga a la lista,
# no un chat y una actividad por producto.
MAX_AVISOS_INDIVIDUALES = 5
ACCION_COLA = 'ferba_producto_categorizacion.action_productos_por_categorizar'
# Contexto con el que el propio modulo escribe sin pasar por sus reglas.
SIN_REGLAS = 'ferba_sin_categorizacion'
AYUDA_COLA = 'Inventario, Ventas o Compras > Productos > Por categorizar'


def es_categorizador(env):
    return env.user.has_group(GRUPO_GESTOR)


def raiz_contable(env):
    """Categoria raiz configurada (hoy "CATEGORIAS CONTABLES", id 78). Vacia si no hay."""
    valor = env['ir.config_parameter'].sudo().get_param(PARAM_RAIZ)
    try:
        return env['product.category'].browse(int(valor)).exists()
    except (TypeError, ValueError):
        return env['product.category']


def es_contable(categ, raiz):
    """Una categoria contable es una descendiente de la raiz (la raiz misma no cuenta). Sin raiz
    configurada, cualquier categoria cuenta: no hay contra que validar."""
    if not categ:
        return False
    if not raiz:
        return True
    return categ != raiz and (categ.parent_path or '').startswith(raiz.parent_path or '/')


def categoria_restringida(env, empresa):
    """Donde la categoria solo se elige entre las contables: con la opcion encendida, y en Francisco
    (17), que ya lo hacia por las vistas 6661/6662 desde el 24-sep."""
    return empresa.id == EMPRESA_POR_CATEGORIA or empresa.ferba_categorizacion_obligatoria


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    ferba_categorizacion = fields.Selection(
        ESTADOS, string='Categorizacion', default='categorizado', required=True,
        copy=False, tracking=True, index=True,
        help='Por categorizar: lo dio de alta alguien que no es categorizador; esta oculto (archivado) y no '
             'se puede usar hasta que se le asigne su categoria contable.')
    ferba_categ_sugerida_id = fields.Many2one(
        'product.category', string='Categoria sugerida', copy=False, tracking=True,
        help='La propone quien da de alta el producto. Quien categoriza la confirma o la cambia.')
    ferba_categorizacion_motivo = fields.Text(string='Motivo del rechazo', copy=False, readonly=True)
    ferba_categorizado_por_id = fields.Many2one(
        'res.users', string='Categorizado por', copy=False, readonly=True)
    ferba_categorizado_fecha = fields.Datetime(string='Categorizado el', copy=False, readonly=True)

    # Para la pantalla: dependen de quien mira y de la empresa activa, no se guardan.
    ferba_categorizacion_activa = fields.Boolean(compute='_compute_ferba_categorizacion_contexto')
    ferba_puede_categorizar = fields.Boolean(compute='_compute_ferba_categorizacion_contexto')
    ferba_categ_raiz_id = fields.Many2one(
        'product.category', compute='_compute_ferba_categorizacion_contexto')
    ferba_sin_categoria_contable = fields.Boolean(
        string='Sin categoria contable', compute='_compute_ferba_sin_categoria_contable',
        search='_search_ferba_sin_categoria_contable')

    # ------------------------------------------------------------ computes
    @api.depends('company_id')
    @api.depends_context('company', 'uid')
    def _compute_ferba_categorizacion_contexto(self):
        puede = es_categorizador(self.env)
        raiz = raiz_contable(self.env)
        for producto in self:
            empresa = producto.company_id or self.env.company
            producto.ferba_categorizacion_activa = empresa.ferba_categorizacion_obligatoria
            producto.ferba_puede_categorizar = puede
            # Solo se llena donde la categoria esta restringida: las vistas arman el dominio con el.
            producto.ferba_categ_raiz_id = raiz if categoria_restringida(self.env, empresa) else False

    @api.depends('categ_id')
    def _compute_ferba_sin_categoria_contable(self):
        raiz = raiz_contable(self.env)
        for producto in self:
            producto.ferba_sin_categoria_contable = not es_contable(producto.categ_id, raiz)

    def _search_ferba_sin_categoria_contable(self, operator, value):
        if operator in ('in', 'not in'):
            buscados = {bool(v) for v in value}
        elif operator in ('=', '!='):
            buscados = {bool(value)}
        else:
            return NotImplemented
        if operator in ('not in', '!='):
            buscados = {True, False} - buscados
        if buscados == {True, False}:
            return []
        if not buscados:
            return [('id', '=', False)]
        raiz = raiz_contable(self.env)
        con = ([('categ_id', 'child_of', raiz.id), ('categ_id', '!=', raiz.id)] if raiz
               else [('categ_id', '!=', False)])
        return (['!', '&'] + con) if True in buscados else (['&'] + con)

    @api.depends_context('company', 'uid')
    def _compute_ferba_ve_contabilidad(self):
        # ferba_categoria_sat oculta la pestana de contabilidad en Francisco a quien no es gestor de
        # categorias. Donde la categorizacion es obligatoria pasa lo mismo: las cuentas salen de la
        # categoria y una cuenta puesta en el producto le ganaria.
        super()._compute_ferba_ve_contabilidad()
        if self.env.company.ferba_categorizacion_obligatoria and not es_categorizador(self.env):
            for producto in self:
                producto.ferba_ve_contabilidad = False

    # ------------------------------------------------------------ helpers
    def _ferba_empresa(self):
        self.ensure_one()
        return self.company_id or self.env.company

    @api.model
    def _ferba_categorizadores(self, empresa):
        """Categorizadores vivos que pueden entrar a la empresa: a quien no puede abrir el producto no
        se le asigna actividad (ni se deja crear)."""
        grupo = self.env.ref(GRUPO_GESTOR, raise_if_not_found=False)
        if not grupo:
            return self.env['res.users']
        return grupo.sudo().user_ids.filtered(
            lambda u: u.active and not u.share and empresa in u.company_ids)

    def _ferba_url(self):
        self.ensure_one()
        return '%s/odoo/product.template/%d' % (self.get_base_url(), self.id)

    @api.model
    def _ferba_url_cola(self):
        return '%s/odoo/action-%s' % (self.get_base_url(), ACCION_COLA)

    def _ferba_nota(self, texto):
        """Rastro en el chatter del producto, sin destinatarios: avisar es trabajo del chat."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self.message_post(body=Markup('<p>%s</p>') % escape(texto),
                                  message_type='notification', subtype_xmlid='mail.mt_note')
        except Exception:
            _logger.exception('ferba_producto_categorizacion: no se pudo dejar la nota en %s', self.display_name)

    @api.model
    def _ferba_chat(self, partners, texto, url, etiqueta):
        """Mensaje directo en Conversaciones, del usuario actual a cada destinatario, igual que la
        aprobacion de cotizaciones (es el aviso que FERBA usa). Nunca tumba la operacion."""
        Canal = self.env['discuss.channel']
        yo = self.env.user.partner_id
        for partner in partners:
            if partner == yo:
                continue
            try:
                with self.env.cr.savepoint():
                    canal = Canal._get_or_create_chat(partners_to=[partner.id])
                    canal.message_post(
                        body=Markup('<p>%s <a href="%s">%s</a></p>') % (texto, url, etiqueta),
                        message_type='comment', subtype_xmlid='mail.mt_comment')
            except Exception:
                _logger.exception('ferba_producto_categorizacion: no se pudo mandar el chat a %s', partner.name)

    def _ferba_cerrar_actividades(self, feedback):
        self.ensure_one()
        acts = self.activity_ids.filtered(lambda a: a.summary and a.summary.startswith(RESUMEN_ACTIVIDAD))
        if acts:
            try:
                with self.env.cr.savepoint():
                    # sudo: las actividades son de cada categorizador y las cierra el que categorizo.
                    acts.sudo().action_feedback(feedback=feedback)
            except Exception:
                _logger.exception('ferba_producto_categorizacion: no se pudieron cerrar las actividades de %s',
                                  self.display_name)

    def _ferba_avisar_alta(self):
        """Al dar de alta productos que quedan por categorizar: nota en cada uno, chat y actividad a los
        categorizadores de la empresa. En altas masivas, un solo chat con la liga a la lista."""
        yo = self.env.user
        por_empresa = defaultdict(lambda: self.browse())
        for producto in self:
            por_empresa[producto._ferba_empresa()] |= producto
        for empresa, productos in por_empresa.items():
            for producto in productos:
                producto._ferba_nota('%s dio de alta el producto. Queda oculto hasta que un categorizador '
                                     'le asigne su categoria contable.' % yo.name)
            categorizadores = self._ferba_categorizadores(empresa) - yo
            if not categorizadores:
                _logger.warning('ferba_producto_categorizacion: no hay categorizadores con acceso a %s', empresa.name)
                continue
            if len(productos) > MAX_AVISOS_INDIVIDUALES:
                self._ferba_chat(
                    categorizadores.partner_id,
                    'Di de alta %d productos en %s que necesitan categoria antes de poder usarse.'
                    % (len(productos), empresa.name),
                    self._ferba_url_cola(), 'Ver productos por categorizar')
                continue
            for producto in productos:
                sugerida = producto.ferba_categ_sugerida_id
                texto = 'Di de alta el producto %s en %s. Necesita categoria para poder usarse%s.' % (
                    producto.display_name, empresa.name,
                    (' (sugiero %s)' % sugerida.name) if sugerida else '')
                producto._ferba_chat(categorizadores.partner_id, texto, producto._ferba_url(), 'Abrir producto')
                for usuario in categorizadores:
                    try:
                        with self.env.cr.savepoint():
                            producto.activity_schedule(
                                'mail.mail_activity_data_todo', user_id=usuario.id,
                                summary='%s %s' % (RESUMEN_ACTIVIDAD, producto.display_name)[:250],
                                note='Lo dio de alta %s. Asignale su categoria con el boton «Categorizar».' % yo.name)
                    except Exception:
                        _logger.exception('ferba_producto_categorizacion: no se pudo asignar la actividad a %s',
                                          usuario.name)

    # ------------------------------------------------------------ reglas
    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(SIN_REGLAS) or self.env.su:
            # Procesos del sistema (sudo) y el propio modulo: sin reglas.
            return super().create(vals_list)
        Categoria = self.env['product.category']
        raiz = raiz_contable(self.env)
        puede = es_categorizador(self.env)
        ahora = fields.Datetime.now()
        pendientes_idx = []
        for i, vals in enumerate(vals_list):
            empresa = (self.env['res.company'].browse(vals['company_id']) if vals.get('company_id')
                       else self.env.company)
            if not empresa.ferba_categorizacion_obligatoria:
                continue
            categ = Categoria.browse(vals['categ_id']) if vals.get('categ_id') else Categoria
            contable = es_contable(categ, raiz)
            if puede and contable:
                vals.update(ferba_categorizacion='categorizado',
                            ferba_categorizado_por_id=self.env.uid, ferba_categorizado_fecha=ahora)
                continue
            # Lo que traia (al duplicar, o el categorizador sin elegir) queda como sugerencia.
            if contable and not vals.get('ferba_categ_sugerida_id'):
                vals['ferba_categ_sugerida_id'] = categ.id
            vals.update(ferba_categorizacion='pendiente', categ_id=False, active=False,
                        ferba_categorizado_por_id=False, ferba_categorizado_fecha=False)
            pendientes_idx.append(i)
        productos = super().create(vals_list)
        if pendientes_idx:
            productos.browse([productos[i].id for i in pendientes_idx])._ferba_avisar_alta()
        return productos

    def write(self, vals):
        if self.env.context.get(SIN_REGLAS) or self.env.su:
            return super().write(vals)
        if 'ferba_categorizacion' in vals and any(
                p.ferba_categorizacion != vals['ferba_categorizacion'] for p in self):
            raise AccessError('La categorizacion cambia solo con «Categorizar» o «Rechazar alta».')
        if vals.get('active'):
            ocultos = self.filtered(lambda p: p.ferba_categorizacion != 'categorizado')
            if ocultos:
                raise UserError(
                    'Estos productos no se desarchivan a mano: se activan solos cuando un categorizador les '
                    'asigna su categoria (%s): %s' % (AYUDA_COLA, ', '.join(ocultos.mapped('display_name'))))
        if 'categ_id' in vals:
            nueva = self.env['product.category'].browse(vals['categ_id'] or [])
            # Solo cuenta si de verdad cambia: el formulario manda la categoria en cada guardado
            # (force_save) aunque nadie la toque.
            cambian = self.filtered(
                lambda p: p.categ_id != nueva and p._ferba_empresa().ferba_categorizacion_obligatoria)
            if cambian:
                cambian._ferba_validar_categoria(nueva)
                resto = {k: v for k, v in vals.items() if k != 'categ_id'}
                otros = self - cambian
                res = super(ProductTemplate, otros).write(vals) if otros else True
                if resto:
                    super(ProductTemplate, cambian).write(resto)
                # Valida (categorizador, categoria contable) y aplica clave SAT, activacion y avisos.
                cambian._ferba_categorizar(nueva)
                return res
        return super().write(vals)

    # ------------------------------------------------------------ categorizar / rechazar
    @api.model
    def _ferba_validar_categoria(self, categ):
        if not es_categorizador(self.env):
            raise AccessError(
                'Solo los categorizadores asignan o cambian la categoria de un producto (Contabilidad > '
                'Configuracion > Ajustes > Categorizacion de productos). Puedes dejar tu propuesta en '
                '«Categoria sugerida».')
        raiz = raiz_contable(self.env)
        if not es_contable(categ, raiz):
            raise UserError('La categoria tiene que ser una de «%s» (la categoria contable decide cuentas y '
                            'clave SAT). «%s» no lo es.' % (raiz.display_name, categ.display_name or 'vacia'))

    def _ferba_categorizar(self, categ):
        """Unica puerta para asignar la categoria donde la categorizacion es obligatoria."""
        self._ferba_validar_categoria(categ)
        ahora = fields.Datetime.now()
        yo = self.env.user
        for producto in self.with_context(active_test=False):
            empresa = producto._ferba_empresa()
            clave = categ.with_company(empresa).ferba_unspsc_code_id
            era = producto.ferba_categorizacion
            vals = {'categ_id': categ.id, 'ferba_categorizado_por_id': yo.id, 'ferba_categorizado_fecha': ahora}
            if clave:
                # La clave de la categoria gana al categorizar (misma regla de ferba_categoria_sat al
                # cambiar de categoria). Tambien corrige la que borra la automatizacion 22 de Studio.
                vals['unspsc_code_id'] = clave.id
            if era != 'categorizado':
                vals.update(ferba_categorizacion='categorizado', ferba_categorizacion_motivo=False, active=True)
            producto.with_context(**{SIN_REGLAS: True}).write(vals)
            if era != 'categorizado':
                producto._ferba_cerrar_actividades('Categorizado en %s' % categ.display_name)
                producto._ferba_nota('%s lo categorizo en %s%s. Ya se puede usar.' % (
                    yo.name, categ.display_name, (', clave SAT %s' % clave.code) if clave else ''))
                creador = producto.create_uid.partner_id
                self._ferba_chat(creador, 'Ya categorice el producto %s en %s. Ya se puede usar:'
                                 % (producto.display_name, categ.name), producto._ferba_url(), 'Abrir producto')
            else:
                producto._ferba_nota('%s cambio la categoria a %s.' % (yo.name, categ.display_name))
        return True

    def _ferba_rechazar(self, motivo):
        if not es_categorizador(self.env):
            raise AccessError('Solo los categorizadores rechazan el alta de un producto.')
        motivo = (motivo or '').strip()
        if not motivo:
            raise UserError('Escribe el motivo del rechazo: es lo que le llega a quien dio de alta el producto.')
        ahora = fields.Datetime.now()
        yo = self.env.user
        for producto in self.with_context(active_test=False):
            if producto.ferba_categorizacion != 'pendiente':
                raise UserError('Solo se rechaza un producto que esta por categorizar: %s.' % producto.display_name)
            producto.with_context(**{SIN_REGLAS: True}).write({
                'ferba_categorizacion': 'rechazado',
                'ferba_categorizacion_motivo': motivo,
                'ferba_categorizado_por_id': yo.id,
                'ferba_categorizado_fecha': ahora,
            })
            producto._ferba_cerrar_actividades('Alta rechazada: %s' % motivo)
            producto._ferba_nota('%s rechazo el alta. Motivo: %s' % (yo.name, motivo))
            self._ferba_chat(producto.create_uid.partner_id,
                             'Rechace el alta del producto %s. Motivo: %s.' % (producto.display_name, motivo),
                             producto._ferba_url(), 'Abrir producto')
        return True

    # ------------------------------------------------------------ botones
    def action_ferba_categorizar(self):
        return self._ferba_abrir_asistente('categorizar')

    def action_ferba_rechazar(self):
        return self._ferba_abrir_asistente('rechazar')

    def _ferba_abrir_asistente(self, modo):
        if not es_categorizador(self.env):
            raise AccessError('Solo los categorizadores categorizan o rechazan productos.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Categorizar producto' if modo == 'categorizar' else 'Rechazar alta',
            'res_model': 'ferba.producto.categorizar',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_product_tmpl_ids': [(6, 0, self.ids)],
                'default_modo': modo,
                'active_test': False,
            },
        }


class ProductProduct(models.Model):
    _inherit = 'product.product'

    # Propios y no delegados a la plantilla (como ferba_ve_contabilidad): dependen de quien mira.
    ferba_categorizacion_activa = fields.Boolean(compute='_compute_ferba_categorizacion_contexto')
    ferba_puede_categorizar = fields.Boolean(compute='_compute_ferba_categorizacion_contexto')
    ferba_categ_raiz_id = fields.Many2one(
        'product.category', compute='_compute_ferba_categorizacion_contexto')

    @api.depends('company_id')
    @api.depends_context('company', 'uid')
    def _compute_ferba_categorizacion_contexto(self):
        puede = es_categorizador(self.env)
        raiz = raiz_contable(self.env)
        for producto in self:
            empresa = producto.company_id or self.env.company
            producto.ferba_categorizacion_activa = empresa.ferba_categorizacion_obligatoria
            producto.ferba_puede_categorizar = puede
            producto.ferba_categ_raiz_id = raiz if categoria_restringida(self.env, empresa) else False

    @api.depends_context('company', 'uid')
    def _compute_ferba_ve_contabilidad(self):
        super()._compute_ferba_ve_contabilidad()
        if self.env.company.ferba_categorizacion_obligatoria and not es_categorizador(self.env):
            for producto in self:
                producto.ferba_ve_contabilidad = False

    @api.model_create_multi
    def create(self, vals_list):
        productos = super().create(vals_list)
        if not (self.env.context.get(SIN_REGLAS) or self.env.su):
            # Alta rapida desde una linea («Crear ...»): la variante nace activa aunque su plantilla haya
            # quedado por categorizar. Se oculta igual que la plantilla.
            ocultar = productos.filtered(
                lambda p: p.active and p.product_tmpl_id.ferba_categorizacion != 'categorizado')
            if ocultar:
                ocultar.with_context(**{SIN_REGLAS: True}).write({'active': False})
        return productos

    def write(self, vals):
        if vals.get('active') and not (self.env.context.get(SIN_REGLAS) or self.env.su):
            ocultos = self.filtered(lambda p: p.product_tmpl_id.ferba_categorizacion != 'categorizado')
            if ocultos:
                raise UserError(
                    'Estos productos no se desarchivan a mano: se activan solos cuando un categorizador les '
                    'asigna su categoria (%s): %s' % (AYUDA_COLA, ', '.join(ocultos.mapped('display_name'))))
        return super().write(vals)

    def action_ferba_categorizar(self):
        return self.product_tmpl_id.action_ferba_categorizar()

    def action_ferba_rechazar(self):
        return self.product_tmpl_id.action_ferba_rechazar()
