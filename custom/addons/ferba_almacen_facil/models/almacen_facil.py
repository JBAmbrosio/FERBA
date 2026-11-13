# -*- coding: utf-8 -*-
"""Almacén Fácil (FERBA) — datos y acciones para el almacenista.

AbstractModel sin tablas: expone métodos que el front OWL llama por RPC. Envuelve
el inventario nativo (stock.picking) para dar una pantalla simple. Las operaciones
corren con sudo() para que el rol "Almacenista" NO necesite ver el inventario
completo de Odoo; la trazabilidad se guarda con la firma (x_studio_firma_de_
responsable) y una nota en el chatter con el usuario que recibió.
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

# Una recepción está "pendiente" si es de entrada y aún no se validó.
DOM_RECEP_PEND = [
    ('picking_type_code', '=', 'incoming'),
    ('state', 'in', ['assigned', 'confirmed', 'waiting']),
]
# Una entrega está "pendiente" si es de salida y aún no se validó.
DOM_ENTREGA_PEND = [
    ('picking_type_code', '=', 'outgoing'),
    ('state', 'in', ['assigned', 'confirmed', 'waiting']),
]


class AlmacenFacil(models.AbstractModel):
    _name = 'ferba.almacen.facil'
    _description = 'Almacén Fácil (datos para el almacenista)'

    # ------------------------------------------------------------------ util
    @api.model
    def _check_acceso(self):
        u = self.env.user
        if (u.has_group('ferba_almacen_facil.group_ferba_almacenista')
                or u.has_group('stock.group_stock_user')
                or u._is_admin()):
            return True
        raise AccessError("No tienes acceso a Almacén Fácil.")

    @api.model
    def _dias(self, dt):
        if not dt:
            return 0
        try:
            return max(0, (fields.Datetime.now() - dt).days)
        except Exception:
            return 0

    def _proyecto(self, picking):
        # El proyecto puede venir del campo Studio del picking o del origen.
        val = picking.x_studio_proyecto if 'x_studio_proyecto' in picking._fields else False
        return val or ''

    # ------------------------------------------------------------------ inicio
    @api.model
    def resumen(self):
        self._check_acceso()
        P = self.env['stock.picking'].sudo()
        return {
            'usuario': self.env.user.name,
            'recepciones': P.search_count(DOM_RECEP_PEND),
            'entregas': P.search_count(DOM_ENTREGA_PEND),
        }

    # ------------------------------------------------------------------ lista
    def _lista(self, dom):
        P = self.env['stock.picking'].sudo()
        pickings = P.search(dom, order='scheduled_date asc, id asc')
        res = []
        for p in pickings:
            piezas = sum(p.move_ids.mapped('product_uom_qty'))
            res.append({
                'id': p.id,
                'name': p.name,
                'socio': p.partner_id.display_name or '—',
                'proyecto': self._proyecto(p),
                'origen': p.origin or '',
                'piezas': round(piezas, 2),
                'renglones': len(p.move_ids),
                'dias': self._dias(p.scheduled_date or p.create_date),
                'listo': p.state == 'assigned',
            })
        return res

    @api.model
    def lista_recepciones(self):
        self._check_acceso()
        return self._lista(DOM_RECEP_PEND)

    @api.model
    def lista_entregas(self):
        self._check_acceso()
        return self._lista(DOM_ENTREGA_PEND)

    # ------------------------------------------------------------------ detalle
    @api.model
    def detalle(self, picking_id):
        self._check_acceso()
        p = self.env['stock.picking'].sudo().browse(int(picking_id))
        if not p.exists():
            return {'error': 'La recepción ya no existe.'}
        renglones = []
        es_salida = p.picking_type_code == 'outgoing'
        for m in p.move_ids:
            if m.state == 'cancel':
                continue
            renglones.append({
                'move_id': m.id,
                'producto': m.product_id.display_name,
                'codigo': m.product_id.default_code or '',
                'demanda': round(m.product_uom_qty, 2),
                'cantidad': round(m.product_uom_qty, 2),  # compat
                'unidad': m.product_uom.name or '',
                # Solo en salidas: cuánto hay, para avisar si no alcanza.
                'disponible': round(m.product_id.qty_available, 2) if es_salida else None,
            })
        return {
            'id': p.id,
            'name': p.name,
            'socio': p.partner_id.display_name or '—',
            'proyecto': self._proyecto(p),
            'origen': p.origin or '',
            'dias': self._dias(p.scheduled_date or p.create_date),
            'listo': p.state == 'assigned',
            'ya_firmado': bool(p.x_studio_firma_de_responsable) if 'x_studio_firma_de_responsable' in p._fields else False,
            'renglones': renglones,
        }

    # ------------------------------------------------------------ recibir/entregar
    def _procesar(self, picking_id, firma, accion, lineas=None, novedades=None):
        """Valida un picking (accion 'recibir' = entrada, 'entregar' = salida) con
        firma. Soporta PARCIALES: si `lineas` trae cantidades menores a la demanda,
        Odoo crea un backorder con el resto (reaparece en pendientes). Robusto: la
        validación del stock va PRIMERO; firma, banderas, nota y novedades son
        best-effort y nunca revierten una operación ya hecha.

        lineas: lista opcional [{'move_id': id, 'cantidad': n}, ...]. Sin ella se
                procesa TODO (cantidad hecha = demanda), como antes.
        novedades: lista opcional de textos a dejar como nota (dañado, etc.).
        """
        self._check_acceso()
        es_recibir = accion == 'recibir'
        fem = 'recibida' if es_recibir else 'entregada'
        verbo = 'Recibido' if es_recibir else 'Entregado'
        p = self.env['stock.picking'].sudo().browse(int(picking_id))
        if not p.exists():
            return {'ok': False, 'msg': 'El documento ya no existe.'}
        if p.state == 'done':
            return {'ok': False, 'msg': 'Este documento ya estaba %s.' % fem}
        if p.state not in ('assigned', 'confirmed', 'waiting'):
            return {'ok': False, 'msg': 'Este documento no se puede procesar ahora.'}

        # Mapa move_id -> cantidad marcada (parciales). Sin 'lineas' => TODO.
        pedir = {}
        if lineas:
            for it in lineas:
                try:
                    pedir[int(it.get('move_id'))] = max(0.0, float(it.get('cantidad') or 0))
                except Exception:
                    continue

        total_proc = 0.0
        total_dem = 0.0
        try:
            # 1) Asegura disponibilidad y fija la cantidad hecha de cada renglón.
            if p.state in ('confirmed', 'waiting'):
                p.action_assign()
            for m in p.move_ids:
                if m.state in ('done', 'cancel'):
                    continue
                dem = m.product_uom_qty
                total_dem += dem
                # Cantidad a procesar: la marcada para este renglón, o la demanda
                # completa si no vino en 'lineas'.
                cant = pedir.get(m.id, dem) if lineas else dem
                m.quantity = cant
                m.picked = bool(cant > 0)
                total_proc += cant
            if total_proc <= 0:
                return {'ok': False, 'msg': 'No marcaste ninguna cantidad para %s.' % ('recibir' if es_recibir else 'entregar')}
            # 2) Validar el picking (operación CRÍTICA): mueve el stock.
            #    skip_backorder=True no abre el asistente; si quedó faltante, Odoo
            #    crea el backorder automáticamente con el resto.
            p.with_context(skip_backorder=True, skip_sms=True).button_validate()
            if p.state != 'done':
                extra = '' if es_recibir else ' ¿Hay existencia suficiente?'
                return {'ok': False, 'msg': 'No se completó (quedó en %s).%s Avisa a un supervisor.' % (p.state, extra)}
        except Exception as e:
            _logger.warning("Almacén Fácil: error al %s %s: %s", accion, p.name, e)
            return {'ok': False, 'msg': str(e)}

        # 3) Firma, banderas, nota y novedades: best-effort. NUNCA revierten la
        #    operación (el stock ya se movió). Cada paso en su propio try/except.
        flags = {}
        if 'x_studio_aprobar' in p._fields:
            flags['x_studio_aprobar'] = True
        if 'x_studio_estatus' in p._fields:
            flags['x_studio_estatus'] = '1'
        if flags:
            try:
                p.write(flags)
            except Exception as e:
                _logger.warning("Almacén Fácil: no se marcaron banderas en %s: %s", p.name, e)

        firma_b64 = firma.split(',')[-1] if firma else None
        if firma_b64 and 'x_studio_firma_de_responsable' in p._fields:
            try:
                p.write({'x_studio_firma_de_responsable': firma_b64})
            except Exception as e:
                _logger.warning("Almacén Fácil: no se guardó la firma en el campo de %s: %s", p.name, e)

        # Nota en el chatter (plana, siempre confiable).
        try:
            p.message_post(body="%s desde <b>Almacén</b> por %s." % (verbo, self.env.user.name))
        except Exception as e:
            _logger.warning("Almacén Fácil: no se publicó la nota en %s: %s", p.name, e)

        # Novedades reportadas por el almacenista (dañado, incorrecto, sobrante…).
        if novedades:
            try:
                items = "".join("<li>%s</li>" % n for n in novedades if n)
                if items:
                    p.message_post(body="<b>Novedades</b> (%s):<ul>%s</ul>" % (self.env.user.name, items))
            except Exception as e:
                _logger.warning("Almacén Fácil: no se registraron novedades en %s: %s", p.name, e)

        # Firma como adjunto DIRECTO del picking (ir.attachment): queda ligada y
        # visible en el botón de adjuntos. Su try/except no afecta la operación.
        if firma_b64:
            try:
                self.env['ir.attachment'].sudo().create({
                    'name': 'Firma %s.png' % (p.name or ''),
                    'datas': firma_b64,
                    'res_model': 'stock.picking',
                    'res_id': p.id,
                    'mimetype': 'image/png',
                })
            except Exception as e:
                _logger.warning("Almacén Fácil: no se guardó la firma (adjunto) en %s: %s", p.name, e)

        # ¿Quedó un backorder (recepción/entrega parcial)? Avisar al usuario.
        backorder = ''
        try:
            bo = self.env['stock.picking'].sudo().search(
                [('backorder_id', '=', p.id)], order='id desc', limit=1)
            backorder = bo.name or ''
        except Exception:
            pass
        parcial = bool(backorder)

        msg = '%s: %s' % (verbo, p.name)
        if parcial:
            msg += ' — parcial; el resto quedó pendiente'
            if backorder:
                msg += ' (%s)' % backorder
        return {'ok': True, 'name': p.name, 'msg': msg, 'parcial': parcial,
                'backorder': backorder, 'faltante': round(max(0.0, total_dem - total_proc), 2)}

    @api.model
    def recibir(self, picking_id, firma=None, lineas=None, novedades=None):
        return self._procesar(picking_id, firma, 'recibir', lineas, novedades)

    @api.model
    def entregar(self, picking_id, firma=None, lineas=None, novedades=None):
        return self._procesar(picking_id, firma, 'entregar', lineas, novedades)

    # ------------------------------------------------------------------ buscar
    @api.model
    def buscar(self, texto):
        self._check_acceso()
        texto = (texto or '').strip()
        if len(texto) < 2:
            return {'productos': [], 'ordenes': []}
        Prod = self.env['product.product'].sudo()
        prods = Prod.search(['|', ('default_code', 'ilike', texto), ('name', 'ilike', texto)], limit=25)
        productos = [{
            'id': pr.id,
            'nombre': pr.display_name,
            'codigo': pr.default_code or '',
            'existencia': round(pr.qty_available, 2),
            'unidad': pr.uom_id.name or '',
        } for pr in prods]
        P = self.env['stock.picking'].sudo()
        picks = P.search(['|', ('name', 'ilike', texto), ('origin', 'ilike', texto)], limit=25, order='id desc')
        tipo_txt = {'incoming': 'Recepción', 'outgoing': 'Entrega', 'internal': 'Traslado'}
        ok = {'done': 'Hecho', 'assigned': 'Listo', 'confirmed': 'Esperando', 'waiting': 'Esperando',
              'draft': 'Borrador', 'cancel': 'Cancelado'}
        ordenes = [{
            'id': pk.id,
            'name': pk.name,
            'origen': pk.origin or '',
            'estado': ok.get(pk.state, pk.state),
            'tipo': tipo_txt.get(pk.picking_type_code, pk.picking_type_code or ''),
        } for pk in picks]
        return {'productos': productos, 'ordenes': ordenes}
