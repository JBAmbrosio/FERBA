"""Tablero operativo de FERBA (datos).

Junta en un viaje el status del flujo que FERBA maneja con Studio: ventas y
proyectos, el embudo de compras (solicitud -> requisicion -> RFQ -> orden de
compra), almacen (recepciones y traslados) y pagos. Cada metrica trae su
`model` + `domain` para que el tablero sea CLICKABLE (abre los registros detras).

Notas del dato real (medido en produccion y validado por RPC 7-nov-2026):
- Los estatus de Studio se guardan como CODIGOS ('0','1','2'); la etiqueta se
  resuelve con fields_get.
- `purchase.order.x_studio_monto_en_uso`/`x_studio_presupuesto` son related NO
  almacenados: no se suman por SQL. Para montos de compra se usa amount_total.
- Empresa activa: los modelos nativos se acotan por `company_id`; los modelos de
  Studio (solicitudes, requisiciones, rfq, pagos) NO tienen `company_id` pero SI
  `x_studio_company_id` (m2o a res.company, 100% poblado) -> se acotan por el.
  Asi todo el tablero respeta el selector de empresa de forma consistente.
- "Ventas" = ordenes CONFIRMADAS (state in sale/done). La dona por tipo y el KPI
  de ventas de proyecto antes sumaban TODOS los estados (incluidas cotizaciones
  y canceladas), inflando el total; ahora cuadran con el KPI "Ordenes de venta".
  El bloque "Ventas por etapa" si muestra el desglose por estado a proposito.

Visibilidad: TODOS los usuarios internos, con montos. Lecturas con sudo().
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

# Estados que cuentan como venta CONFIRMADA (no cotizacion, no cancelada).
VENTA_CONFIRMADA = [('state', 'in', ['sale', 'done'])]


class FerbaOperativoDashboard(models.AbstractModel):
    _name = 'ferba.operativo.dashboard'
    _description = 'Tablero operativo FERBA (datos)'

    # ------------------------------------------------------------------ utiles
    def _existe(self, model):
        return model in self.env

    def _campo(self, model, field):
        try:
            return field in self.env[model]._fields
        except Exception:
            return False

    def _labels(self, model, field):
        try:
            info = self.env[model].sudo().fields_get([field]).get(field) or {}
            return dict(info.get('selection') or [])
        except Exception:
            return {}

    def _dom_empresa(self, model):
        """Acota a las empresas activas, por `company_id` (nativos) o, si no
        existe, por `x_studio_company_id` (modelos de Studio)."""
        ids = self.env.companies.ids
        if self._campo(model, 'company_id'):
            return [('company_id', 'in', ids)]
        if self._campo(model, 'x_studio_company_id'):
            return [('x_studio_company_id', 'in', ids)]
        return []

    def _contar(self, model, domain=None):
        if not self._existe(model):
            return 0
        try:
            return self.env[model].sudo().search_count((domain or []) + self._dom_empresa(model))
        except Exception:
            return 0

    def _suma(self, model, campo, domain=None):
        """Suma un campo numerico almacenado. 0.0 si no se puede (p. ej. related)."""
        if not self._existe(model) or not self._campo(model, campo):
            return 0.0
        try:
            g = self.env[model].sudo()._read_group(
                (domain or []) + self._dom_empresa(model), [], ['%s:sum' % campo])
            return (g[0][0] if g else 0.0) or 0.0
        except Exception:
            return 0.0

    def _por_estado(self, model, field, montos=None, extra_domain=None):
        """[{code,label,count,<monto>,model,domain}] por un campo selection.
        `extra_domain` filtra el universo (p. ej. solo ventas confirmadas) y se
        hereda en el `domain` de cada fila para que el drill-down coincida."""
        if not self._existe(model) or not self._campo(model, field):
            return []
        montos = montos or []
        extra = extra_domain or []
        labels = self._labels(model, field)
        aggs = ['__count'] + ['%s:sum' % m for m in montos]
        try:
            filas = self.env[model].sudo()._read_group(
                self._dom_empresa(model) + extra, [field], aggs)
        except Exception as e:
            _logger.warning('Tablero: no se pudo agrupar %s.%s (%s)', model, field, e)
            return []
        out = []
        for fila in filas:
            code = fila[0]
            reg = {'code': code or '', 'label': labels.get(code, code or 'Sin estado'),
                   'count': fila[1] or 0, 'model': model,
                   'domain': extra + [(field, '=', code if code else False)]}
            for i, m in enumerate(montos):
                reg[m] = fila[2 + i] or 0.0
            out.append(reg)
        return out

    def _ventas_por_mes(self, meses=12):
        """Monto de ventas CONFIRMADAS por mes (ultimos N). N consultas chicas,
        robusto entre versiones (no depende del groupby por periodo)."""
        if not self._existe('sale.order'):
            return []
        try:
            from dateutil.relativedelta import relativedelta
        except Exception:
            return []
        primero = fields.Date.context_today(self).replace(day=1)
        comp = self._dom_empresa('sale.order')
        MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
                 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
        out = []
        for i in range(meses - 1, -1, -1):
            ini = primero - relativedelta(months=i)
            fin = ini + relativedelta(months=1)
            dom = VENTA_CONFIRMADA + [('date_order', '>=', str(ini)),
                                      ('date_order', '<', str(fin))] + comp
            try:
                g = self.env['sale.order'].sudo()._read_group(dom, [], ['amount_total:sum'])
                monto = (g[0][0] if g else 0.0) or 0.0
            except Exception:
                monto = 0.0
            out.append({'mes': MESES[ini.month - 1], 'anio': ini.year,
                        'parcial': i == 0, 'monto': monto})
        return out

    # -------------------------------------------------------- pendientes (hoy)
    def _mas_viejo(self, model, field, domain=None):
        """El registro mas antiguo por `field` (para medir antiguedad)."""
        if not self._existe(model):
            return None
        try:
            rec = self.env[model].sudo().search(
                (domain or []) + self._dom_empresa(model), order='%s asc' % field, limit=1)
            return rec[field] if rec else None
        except Exception:
            return None

    def _hace(self, valor):
        """'hace N dias/meses/anios' desde una fecha (date/datetime/str)."""
        if not valor:
            return ''
        try:
            d = fields.Date.to_date(valor)
        except Exception:
            return ''
        if not d:
            return ''
        dias = (fields.Date.context_today(self) - d).days
        if dias < 1:
            return 'hoy'
        if dias < 60:
            return 'hace %d días' % dias
        if dias < 730:
            return 'hace %d meses' % round(dias / 30.0)
        return 'hace %d años' % round(dias / 365.0)

    def _anio(self, valor):
        try:
            d = fields.Date.to_date(valor)
            return d.year if d else ''
        except Exception:
            return ''

    def _mc(self, v, sym):
        """Monto compacto (Mex$ 33.1M / 416k) para textos armados en servidor.
        Espeja moneyC del front para que las frases cuadren con los numeros."""
        v = v or 0.0
        a = abs(v)
        if a >= 1e6:
            return '%s %sM' % (sym, ('%.1f' % (v / 1e6)).rstrip('0').rstrip('.'))
        if a >= 1e3:
            return '%s %sk' % (sym, ('%.1f' % (v / 1e3)).rstrip('0').rstrip('.'))
        return '%s %s' % (sym, format(int(round(v)), ','))

    def _pendientes(self):
        """'Que atender': por cada flujo, lo RECIENTE/accionable arriba y el
        rezago historico (estatus que nadie limpia) como nota al pie. Cada
        tarjeta trae su `domain` para abrir justo ese subconjunto."""
        try:
            from dateutil.relativedelta import relativedelta
        except Exception:
            return []
        hoy = fields.Date.context_today(self)
        hoy_s = str(hoy)
        d30 = str(hoy - relativedelta(days=30))
        d90 = str(hoy - relativedelta(days=90))
        sym = self.env.company.currency_id.symbol or '$'
        cards = []

        # 1) Pagos por autorizar (urgente = vencidos) -------------------------
        MP = 'x_gestion_de_pagos'
        if self._existe(MP):
            base = [('x_studio_selection_field_1MYHO', '=', '0')]
            venc = base + [('x_fecha_para_pago', '!=', False),
                           ('x_fecha_para_pago', '<', hoy_s)]
            total_n = self._contar(MP, base)
            venc_n = self._contar(MP, venc)
            urg = venc if venc_n else base
            monto = self._suma(MP, 'x_studio_monto_de_pago', urg)
            viejo = self._mas_viejo(MP, 'x_fecha_para_pago', urg)
            if total_n:
                cards.append({
                    'key': 'pagos', 'icon': 'fa-money', 'tono': 'alto',
                    'titulo': 'Pagos por autorizar',
                    'num': venc_n if venc_n else total_n,
                    'num_lbl': 'vencidos' if venc_n else 'pendientes',
                    'monto': monto,
                    'sub': (('de %s pendientes · el más viejo %s'
                             % (format(total_n, ','), self._hace(viejo))).strip(' ·')
                            if venc_n else ('el más viejo %s' % self._hace(viejo)).strip()),
                    'rezago': '',
                    'model': MP, 'domain': urg,
                })

        # 2) Cotizaciones por cerrar (urgente = recientes, aun tibias) --------
        if self._existe('sale.order'):
            base = [('state', 'in', ['draft', 'sent'])]
            rec = base + [('date_order', '>=', d30)]
            rez = base + [('date_order', '<', d90)]
            rec_n = self._contar('sale.order', rec)
            rec_monto = self._suma('sale.order', 'amount_total', rec)
            rez_n = self._contar('sale.order', rez)
            rez_monto = self._suma('sale.order', 'amount_total', rez)
            viejo = self._mas_viejo('sale.order', 'date_order', rez)
            if rec_n or rez_n:
                cards.append({
                    'key': 'cot', 'icon': 'fa-file-text-o', 'tono': 'medio',
                    'titulo': 'Cotizaciones por cerrar',
                    'num': rec_n, 'num_lbl': 'nuevas (≤30 días)',
                    'monto': rec_monto,
                    'sub': 'abiertas y aún tibias para perseguir',
                    'rezago': ('%s en rezago · %s dormidos · desde %s'
                               % (format(rez_n, ','), self._mc(rez_monto, sym), self._anio(viejo))) if rez_n else '',
                    'model': 'sale.order', 'domain': rec if rec_n else base,
                })

        # 3) Recepciones por firmar (urgente = recientes atrasadas) -----------
        if self._existe('stock.picking'):
            base = [('picking_type_id.code', '=', 'incoming'), ('x_studio_estatus', '=', '0')]
            rec = base + [('scheduled_date', '>=', d30)]
            rez = base + [('scheduled_date', '<', d90)]
            rec_n = self._contar('stock.picking', rec)
            rez_n = self._contar('stock.picking', rez)
            viejo = self._mas_viejo('stock.picking', 'scheduled_date', rez)
            if rec_n or rez_n:
                cards.append({
                    'key': 'rec', 'icon': 'fa-inbox', 'tono': 'medio',
                    'titulo': 'Recepciones por firmar',
                    'num': rec_n, 'num_lbl': 'recientes atrasadas',
                    'monto': None,
                    'sub': 'entradas al almacén esperando firma',
                    'rezago': ('%s en rezago · desde %s'
                               % (format(rez_n, ','), self._anio(viejo))) if rez_n else '',
                    'model': 'stock.picking', 'domain': rec if rec_n else base,
                })

        return cards

    # ------------------------------------------------------------------ data
    @api.model
    def data(self):
        # Visibilidad configurable (Ajustes -> Tablero Operativo): si el usuario
        # no esta en el grupo, ni por URL puede leer los datos.
        if not self.env.user.has_group('ferba_tablero_operativo.group_ferba_operativo_user'):
            raise AccessError(
                "No tienes acceso al Tablero Operativo. "
                "Solicítalo en Ajustes → Tablero Operativo.")
        inc = [('picking_type_id.code', '=', 'incoming')]
        out_pick = [('picking_type_id.code', '=', 'outgoing')]

        # ---- VENTAS Y PROYECTOS ---------------------------------------------
        # Dona + KPI de proyecto: SOLO ventas confirmadas (cuadra con OV).
        ventas_tipo = self._por_estado('sale.order', 'x_studio_tipo_de_orden',
                                       montos=['amount_total'], extra_domain=VENTA_CONFIRMADA)
        # Desglose por estado (aqui si entran cotizaciones y canceladas).
        ventas_estado = self._por_estado('sale.order', 'state', montos=['amount_total'])
        pe = {f['code']: f for f in ventas_estado}
        ov = pe.get('sale', {})
        cot_monto = pe.get('draft', {}).get('amount_total', 0.0) + pe.get('sent', {}).get('amount_total', 0.0)
        cot_n = pe.get('draft', {}).get('count', 0) + pe.get('sent', {}).get('count', 0)
        proyectos = next((t for t in ventas_tipo if t['label'] == 'Proyecto'), {})
        por_mes = self._ventas_por_mes()

        # ---- COMPRAS --------------------------------------------------------
        n_sol = self._contar('x_solicitudes')
        n_req = self._contar('x_requisiciones')
        n_rfq = self._contar('x_rfq')
        n_oc = self._contar('purchase.order')
        oc_monto = self._suma('purchase.order', 'amount_total')
        flujo = [
            {'etapa': 'Solicitud', 'desc': 'Alguien pide material', 'count': n_sol,
             'model': 'x_solicitudes', 'domain': [], 'accent': 'ventas'},
            {'etapa': 'Requisicion', 'desc': 'Se formaliza qué comprar', 'count': n_req,
             'model': 'x_requisiciones', 'domain': [], 'accent': 'compras'},
            {'etapa': 'RFQ', 'desc': 'Se cotiza con proveedores', 'count': n_rfq,
             'model': 'x_rfq', 'domain': [], 'accent': 'compras'},
            {'etapa': 'Orden de compra', 'desc': 'Se autoriza y compra', 'count': n_oc,
             'model': 'purchase.order', 'domain': [], 'accent': 'compras'},
            {'etapa': 'Recepción', 'desc': 'Entra al almacén (con firma)',
             'count': self._contar('stock.picking', inc), 'model': 'stock.picking',
             'domain': inc, 'accent': 'almacen'},
            {'etapa': 'Pago', 'desc': 'Se autoriza y paga al proveedor',
             'count': self._contar('x_gestion_de_pagos'), 'model': 'x_gestion_de_pagos',
             'domain': [], 'accent': 'pagos'},
        ]
        requisiciones = self._por_estado('x_requisiciones', 'x_studio_selection_field_PHzT6')
        rfq = self._por_estado('x_rfq', 'x_studio_estatus_rfq')

        # ---- ALMACEN --------------------------------------------------------
        almacen = [
            {'label': 'Recepciones por aprobar', 'value': self._contar('stock.picking', inc + [('x_studio_estatus', '=', '0')]),
             'accent': 'almacen', 'attn': True, 'model': 'stock.picking', 'domain': inc + [('x_studio_estatus', '=', '0')]},
            {'label': 'Recepciones aprobadas', 'value': self._contar('stock.picking', inc + [('x_studio_estatus', '=', '1')]),
             'accent': 'ok', 'model': 'stock.picking', 'domain': inc + [('x_studio_estatus', '=', '1')]},
            {'label': 'Entregas pendientes', 'value': self._contar('stock.picking', out_pick + [('state', 'not in', ('done', 'cancel'))]),
             'model': 'stock.picking', 'domain': out_pick + [('state', 'not in', ('done', 'cancel'))]},
            {'label': 'Traslados internos', 'value': self._contar('x_traslados_internos'),
             'model': 'x_traslados_internos', 'domain': []},
        ]

        # ---- PAGOS ----------------------------------------------------------
        gestion = self._por_estado('x_gestion_de_pagos', 'x_studio_selection_field_1MYHO',
                                   montos=['x_studio_monto_de_pago', 'x_studio_monto_aprobado'])
        por_autorizar = next((g for g in gestion if g['code'] == '0'), {})
        solicitudes_pago = self._por_estado('x_solicitud_de_pago', 'x_studio_estatus')

        # ---- KPIs (lista uniforme + clickable) ------------------------------
        n_vent = ov.get('count', 0)
        monto_vent = ov.get('amount_total', 0.0)
        kpis = [
            {'key': 'ov', 'icon': 'fa-shopping-cart', 'accent': 'ventas',
             'value': n_vent, 'label': 'Órdenes de venta', 'money': monto_vent,
             'model': 'sale.order', 'domain': [('state', '=', 'sale')]},
            {'key': 'proy', 'icon': 'fa-briefcase', 'accent': 'ventas',
             'value': proyectos.get('count', 0), 'label': 'Ventas de proyecto',
             'money': proyectos.get('amount_total', 0.0),
             'model': 'sale.order',
             'domain': VENTA_CONFIRMADA + [('x_studio_tipo_de_orden', '=', 'Proyecto')]},
            {'key': 'cot', 'icon': 'fa-file-text-o', 'accent': 'ventas',
             'value': cot_n, 'label': 'Cotizaciones abiertas', 'money': cot_monto,
             'model': 'sale.order', 'domain': [('state', 'in', ['draft', 'sent'])]},
            {'key': 'oc', 'icon': 'fa-shopping-bag', 'accent': 'compras',
             'value': n_oc, 'label': 'Órdenes de compra', 'money': oc_monto,
             'model': 'purchase.order', 'domain': []},
            {'key': 'rec', 'icon': 'fa-inbox', 'accent': 'almacen', 'attn': True,
             'value': almacen[0]['value'], 'label': 'Recepciones por aprobar', 'money': None,
             'model': 'stock.picking', 'domain': almacen[0]['domain']},
            {'key': 'pag', 'icon': 'fa-money', 'accent': 'pagos', 'attn': True,
             'value': por_autorizar.get('count', 0), 'label': 'Pagos por autorizar',
             'money': por_autorizar.get('x_studio_monto_de_pago', 0.0),
             'model': 'x_gestion_de_pagos', 'domain': [('x_studio_selection_field_1MYHO', '=', '0')]},
        ]
        ticket = (monto_vent / n_vent) if n_vent else 0.0

        return {
            'meta': {'empresas': self.env.companies.mapped('name'),
                     'moneda': self.env.company.currency_id.symbol or '$',
                     'ticket_promedio': ticket},
            'pendientes': self._pendientes(),
            'kpis': kpis,
            'flujo': flujo,
            'ventas': {'por_tipo': ventas_tipo, 'por_estado': ventas_estado, 'por_mes': por_mes},
            'compras': {'requisiciones': requisiciones, 'rfq': rfq},
            'almacen': almacen,
            'pagos': {'gestion': gestion, 'solicitudes': solicitudes_pago},
        }
