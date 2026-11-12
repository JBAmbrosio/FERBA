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
        }

    # ------------------------------------------------------------------ lista
    @api.model
    def lista_recepciones(self):
        self._check_acceso()
        P = self.env['stock.picking'].sudo()
        pickings = P.search(DOM_RECEP_PEND, order='scheduled_date asc, id asc')
        res = []
        for p in pickings:
            piezas = sum(p.move_ids.mapped('product_uom_qty'))
            dias = self._dias(p.scheduled_date or p.create_date)
            res.append({
                'id': p.id,
                'name': p.name,
                'socio': p.partner_id.display_name or '—',
                'proyecto': self._proyecto(p),
                'origen': p.origin or '',
                'piezas': round(piezas, 2),
                'renglones': len(p.move_ids),
                'dias': dias,
                'listo': p.state == 'assigned',
            })
        return res

    # ------------------------------------------------------------------ detalle
    @api.model
    def detalle(self, picking_id):
        self._check_acceso()
        p = self.env['stock.picking'].sudo().browse(int(picking_id))
        if not p.exists():
            return {'error': 'La recepción ya no existe.'}
        renglones = []
        for m in p.move_ids:
            if m.state == 'cancel':
                continue
            renglones.append({
                'producto': m.product_id.display_name,
                'codigo': m.product_id.default_code or '',
                'cantidad': round(m.product_uom_qty, 2),
                'unidad': m.product_uom.name or '',
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

    # ------------------------------------------------------------------ recibir
    @api.model
    def recibir(self, picking_id, firma=None):
        self._check_acceso()
        p = self.env['stock.picking'].sudo().browse(int(picking_id))
        if not p.exists():
            return {'ok': False, 'msg': 'La recepción ya no existe.'}
        if p.state == 'done':
            return {'ok': False, 'msg': 'Esta recepción ya estaba recibida.'}
        if p.state not in ('assigned', 'confirmed', 'waiting'):
            return {'ok': False, 'msg': 'Esta recepción no se puede recibir ahora.'}
        try:
            # Asegura disponibilidad antes de recibir.
            if p.state in ('confirmed', 'waiting'):
                p.action_assign()
            # Recibir TODO: cantidad hecha = demanda en cada movimiento.
            for m in p.move_ids:
                if m.state in ('done', 'cancel'):
                    continue
                m.quantity = m.product_uom_qty
                m.picked = True
            # Firma + banderas del flujo custom.
            vals = {}
            if firma and 'x_studio_firma_de_responsable' in p._fields:
                vals['x_studio_firma_de_responsable'] = firma.split(',')[-1]
            if 'x_studio_aprobar' in p._fields:
                vals['x_studio_aprobar'] = True
            if 'x_studio_estatus' in p._fields:
                vals['x_studio_estatus'] = '1'
            if vals:
                p.write(vals)
            # Validar sin pedir backorder (se recibe todo) ni SMS.
            p.with_context(skip_backorder=True, skip_sms=True).button_validate()
            if p.state == 'done':
                p.message_post(body="Recibido desde <b>Almacén Fácil</b> por %s." % self.env.user.name)
                return {'ok': True, 'name': p.name, 'msg': 'Recibido: ' + p.name}
            return {'ok': False, 'msg': 'No se completó (quedó en %s). Avisa a un supervisor.' % p.state}
        except Exception as e:
            _logger.warning("Almacén Fácil: error al recibir %s: %s", p.name, e)
            return {'ok': False, 'msg': str(e)}

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
