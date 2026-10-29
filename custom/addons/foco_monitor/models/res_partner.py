# -*- coding: utf-8 -*-
"""Cliente de ventas de campo (res.partner).

El flujo de vendedores REUTILIZA el CRM y los contactos de Odoo en vez de un
modelo paralelo: el CLIENTE es un res.partner (razon social, contacto, telefono
y su geo de base_geolocalize) marcado con `foco_cliente_ventas`, y la
OPORTUNIDAD es un crm.lead. Aqui solo se agregan los campos que la Hoja Maestra
traia y que el partner no tenia (empaque, zona, cultivo, estatus comercial,
expediente) y el metodo que arma el tablero/mapa de Vendedores en un viaje.
"""

from datetime import datetime, timedelta

from odoo import api, fields, models

from .foco_estados_mx import estado_de


ESTATUS_VENTAS = [
    ('prospecto', 'Prospecto'),
    ('activo', 'Seguimiento activo'),
    ('postergado', 'Postergado'),
    ('cerrado', 'Venta cerrada'),
]

# Color del pin en el mapa por estatus comercial. Lo consume el tablero OWL.
ESTATUS_COLOR = {
    'prospecto': '#94a3b8',
    'activo': '#2f6fed',
    'postergado': '#f59e0b',
    'cerrado': '#10b981',
}

# Una razon social (cliente) puede tener varias UBICACIONES fisicas: oficina,
# almacen, campos. Cada ubicacion es un res.partner HIJO (parent_id = la razon
# social) con su propio pin y esta etiqueta. El cliente principal lo deja vacio.
# Las claves deben empatar con foco.mobile.device.TIPOS_UBICACION.
TIPO_UBICACION = [
    ('oficina', 'Oficina'),
    ('almacen', 'Almacen'),
    ('campo', 'Campo'),
    ('otro', 'Otra ubicacion'),
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    foco_cliente_ventas = fields.Boolean(
        string='Cliente de ventas de campo', default=False, index=True,
        help='Marca a los clientes del flujo de vendedores (Hoja Maestra). El '
             'mapa y el tablero de Vendedores solo muestran a estos.')
    foco_empaque = fields.Char(string='Nombre del empaque')
    foco_contacto = fields.Char(string='Contacto')
    foco_zona = fields.Char(string='Zona', index=True)
    foco_cultivo = fields.Char(string='Cultivo', index=True)
    foco_estatus = fields.Selection(
        ESTATUS_VENTAS, string='Estatus comercial', default='prospecto',
        index=True)
    foco_expediente_url = fields.Char(string='Expediente (enlace)')
    foco_ubicacion_fisica = fields.Char(string='Ubicacion fisica (referencia)')
    foco_ubicacion_url = fields.Char(string='Ubicacion (Google Maps)')
    foco_geo_pendiente = fields.Boolean(
        string='Ubicacion por resolver', default=False,
        help='El enlace de Google Maps no traia coordenadas (p.ej. uno acortado): '
             'hay que geolocalizarlo o capturarlo en la primera visita.')

    # UBICACIONES bajo una misma razon social (oficina, almacen, campos): cada una
    # es un partner HIJO con esta etiqueta y su propio pin. Asi la oficina y los
    # campos de "Agricola Uromex" no son tres clientes distintos, sino ubicaciones
    # de la misma razon social. Vacio = es la razon social (cliente principal).
    foco_tipo_ubicacion = fields.Selection(
        TIPO_UBICACION, string='Tipo de ubicacion', index=True,
        help='Para las ubicaciones que cuelgan de una razon social. El cliente '
             'principal (razon social) lo deja vacio.')
    foco_es_ubicacion = fields.Boolean(
        string='Es una ubicacion', compute='_compute_foco_es_ubicacion', store=True,
        help='Verdadero cuando es una ubicacion (oficina/almacen/campo) de otra '
             'razon social, no un cliente por si mismo.')

    # La ZONA es el estado. Sale del pin (punto en el mapa de estados) y, si el
    # cliente no tiene pin, del estado de su direccion. NO del texto de la zona
    # del Excel: es un municipio y su nombre engana ("San Ignacio R.M." es San
    # Ignacio Rio Muerto, Sonora, no San Ignacio de BCS).
    foco_estado_id = fields.Many2one(
        'res.country.state', string='Zona (estado)', index=True,
        compute='_compute_foco_estado', store=True,
        help='El estado en el que esta el cliente: por su ubicacion en el mapa y, '
             'si no tiene ubicacion, por el estado de su direccion. Es la zona del '
             'mapa de Vendedores y del telefono.')
    foco_estado_origen = fields.Selection(
        [('pin', 'Por su ubicacion'), ('direccion', 'Por su direccion')],
        string='Zona tomada', compute='_compute_foco_estado', store=True)

    foco_visita_ids = fields.One2many('foco.visita', 'partner_id',
                                      string='Visitas')
    foco_visita_count = fields.Integer(string='Numero de visitas',
                                       compute='_compute_foco_visita')
    foco_ultima_visita = fields.Datetime(string='Ultima visita',
                                         compute='_compute_foco_visita')

    @api.depends('foco_cliente_ventas', 'partner_latitude', 'partner_longitude',
                 'state_id')
    def _compute_foco_estado(self):
        State = self.env['res.country.state'].sudo()
        oficiales = {}

        def oficial(code):
            if code not in oficiales:
                oficiales[code] = State.search(
                    [('country_id.code', '=', 'MX'), ('code', '=', code)], limit=1)
            return oficiales[code]

        for p in self:
            est, origen = State.browse(), False
            if p.foco_cliente_ventas:
                code, _km = estado_de(p.partner_latitude, p.partner_longitude)
                if code:
                    est, origen = oficial(code), 'pin'
                if not est and p.state_id:
                    est, origen = self._foco_estado_canonico(p.state_id), 'direccion'
            p.foco_estado_id = est
            p.foco_estado_origen = origen if est else False

    @api.model
    def _foco_estado_canonico(self, state):
        """El estado OFICIAL con el mismo nombre. En produccion hay copias
        capturadas a mano ('Baja California Sur' con codigo '23703', 'Baja
        California' con '22920'): sin esto partirian una zona en dos."""
        if not state or (state.code or '').isalpha():
            return state
        gemelos = self.env['res.country.state'].sudo().search(
            [('country_id', '=', state.country_id.id), ('name', '=ilike', state.name),
             ('id', '!=', state.id)])
        return next((s for s in gemelos if (s.code or '').isalpha()), state)

    def _compute_foco_visita(self):
        # sudo A PROPOSITO: este contador se calcula al abrir/guardar CUALQUIER
        # contacto (cliente o proveedor), tambien por usuarios que no son de Foco
        # (contabilidad). Las visitas (foco.visita) solo las ven los grupos de
        # Foco; sin sudo, crear/editar un contacto tronaba con "No puede acceder a
        # foco.visita". Aqui solo se CUENTA (no se exponen datos de la visita).
        data = {}
        if self.ids:
            for g in self.env['foco.visita'].sudo()._read_group(
                    [('partner_id', 'in', self.ids)],
                    ['partner_id'], ['__count', 'check_in:max']):
                data[g[0].id] = (g[1], g[2])
        for p in self:
            n, ult = data.get(p.id, (0, False))
            p.foco_visita_count = n
            p.foco_ultima_visita = ult

    @api.depends('parent_id', 'foco_tipo_ubicacion', 'foco_cliente_ventas')
    def _compute_foco_es_ubicacion(self):
        for p in self:
            p.foco_es_ubicacion = bool(
                p.foco_cliente_ventas and p.parent_id and p.foco_tipo_ubicacion)

    def action_foco_ver_visitas(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Visitas de %s' % self.display_name,
            'res_model': 'foco.visita',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

    @api.model
    def foco_vendedores_panel(self, filtros=None):
        """Todo lo que pinta el tablero de Vendedores en un solo viaje: los
        clientes con su geo y estatus (pines), las oportunidades por cliente,
        los catalogos de los filtros y los KPIs.

        Respeta las reglas de registro: parte de `search()` sin sudo, asi que
        quien solo tiene alcance de sus clientes solo ve los suyos."""
        filtros = filtros or {}
        dom = [('foco_cliente_ventas', '=', True)]
        if filtros.get('vendedor'):
            dom.append(('user_id', '=', int(filtros['vendedor'])))
        if filtros.get('estado'):
            dom.append(('foco_estado_id', '=', int(filtros['estado'])))
        if filtros.get('zona'):
            dom.append(('foco_zona', '=', filtros['zona']))
        if filtros.get('cultivo'):
            dom.append(('foco_cultivo', '=', filtros['cultivo']))
        if filtros.get('estatus'):
            dom.append(('foco_estatus', '=', filtros['estatus']))

        clientes = self.search(dom)

        # --- oportunidades abiertas por cliente (crm.lead) ---
        # sudo acotado: el administrador de Foco puede no estar en un grupo de
        # Ventas (crm.lead esta restringido a esos grupos). Solo leemos unos
        # campos y SIEMPRE sobre los clientes ya filtrados por alcance, asi que
        # no se expone ninguna oportunidad fuera de la cartera visible.
        Lead = self.env['crm.lead'].sudo()
        leads_por_cliente = {}
        total_oport = 0
        if clientes.ids:
            for ld in Lead.search_read(
                    [('partner_id', 'in', clientes.ids),
                     ('type', '=', 'opportunity')],
                    ['partner_id', 'name', 'stage_id', 'expected_revenue',
                     'date_deadline', 'user_id'],
                    order='create_date desc'):
                total_oport += 1
                leads_por_cliente.setdefault(ld['partner_id'][0], []).append({
                    'id': ld['id'], 'name': ld['name'] or '',
                    'stage': ld['stage_id'][1] if ld['stage_id'] else '',
                    'revenue': ld['expected_revenue'] or 0.0,
                    'deadline': (fields.Date.to_string(ld['date_deadline'])
                                 if ld['date_deadline'] else ''),
                    'user': ld['user_id'][1] if ld['user_id'] else '',
                })

        # --- ultima visita y conteo por cliente ---
        ult_visita = {}
        if clientes.ids:
            for g in self.env['foco.visita']._read_group(
                    [('partner_id', 'in', clientes.ids)],
                    ['partner_id'], ['check_in:max', '__count']):
                ult_visita[g[0].id] = (g[1], g[2])

        tipo_label = dict(TIPO_UBICACION)
        filas, por_estatus, sin_geo, n_razones = [], {}, 0, 0
        for c in clientes:
            if not (c.partner_latitude or c.partner_longitude):
                sin_geo += 1
            ult, nvis = ult_visita.get(c.id, (False, 0))
            # Una UBICACION hereda el estatus (y el color) de su razon social: un
            # almacen no es un "prospecto" aparte. Solo las razones sociales cuentan
            # para el total y el desglose por estatus.
            es_ubic = bool(c.parent_id and c.foco_tipo_ubicacion)
            razon = c.commercial_partner_id if es_ubic else c
            est = (razon.foco_estatus if es_ubic else c.foco_estatus) or 'prospecto'
            if not es_ubic:
                n_razones += 1
                por_estatus[est] = por_estatus.get(est, 0) + 1
            filas.append({
                'id': c.id, 'name': c.display_name,
                'lat': c.partner_latitude, 'lng': c.partner_longitude,
                'estatus': est, 'color': ESTATUS_COLOR.get(est, '#94a3b8'),
                'es_ubicacion': es_ubic,
                'tipo_ubicacion': c.foco_tipo_ubicacion or '',
                'tipo_ubicacion_label': tipo_label.get(c.foco_tipo_ubicacion, ''),
                'razon_social': razon.display_name if es_ubic else '',
                'parent_id': c.parent_id.id if c.parent_id else False,
                'empaque': c.foco_empaque or '', 'zona': c.foco_zona or '',
                'estado': c.foco_estado_id.name or '',
                'estado_id': c.foco_estado_id.id or False,
                'cultivo': c.foco_cultivo or '', 'contacto': c.foco_contacto or '',
                'phone': c.phone or '',
                'expediente': c.foco_expediente_url or '',
                'ubicacion_url': c.foco_ubicacion_url or '',
                'ubicacion_fisica': c.foco_ubicacion_fisica or '',
                'vendedor': c.user_id.display_name if c.user_id else '',
                'oportunidades': leads_por_cliente.get(c.id, []),
                'ultima_visita': fields.Datetime.to_string(ult) if ult else '',
                'n_visitas': nvis,
            })

        # --- catalogos para los filtros ---
        zonas = sorted({c.foco_zona for c in clientes if c.foco_zona})
        n_estado = {}
        for c in clientes:
            if c.foco_estado_id:
                n_estado[c.foco_estado_id] = n_estado.get(c.foco_estado_id, 0) + 1
        estados = [{'id': s.id, 'name': s.name, 'n': n}
                   for s, n in sorted(n_estado.items(), key=lambda x: (-x[1], x[0].name))]
        cultivos = sorted({c.foco_cultivo for c in clientes if c.foco_cultivo})
        vendedores, vistos = [], set()
        for c in clientes:
            if c.user_id and c.user_id.id not in vistos:
                vistos.add(c.user_id.id)
                vendedores.append({'id': c.user_id.id,
                                   'name': c.user_id.display_name})

        # --- KPIs ---
        hoy = fields.Date.context_today(self)
        ini_mes = datetime.combine(hoy.replace(day=1), datetime.min.time())
        limite_30 = datetime.combine(hoy, datetime.min.time()) - timedelta(days=30)
        visitas_mes = self.env['foco.visita'].search_count(
            [('partner_id', 'in', clientes.ids), ('check_in', '>=', ini_mes)]
        ) if clientes.ids else 0
        sin_visita_30 = 0
        for c in clientes:
            ult, _n = ult_visita.get(c.id, (False, 0))
            if not ult or ult < limite_30:
                sin_visita_30 += 1

        return {
            'clientes': filas,
            'estatus_labels': dict(ESTATUS_VENTAS),
            'estatus_color': ESTATUS_COLOR,
            'kpis': {
                'total': n_razones, 'por_estatus': por_estatus,
                'ubicaciones': len(clientes) - n_razones,
                'oportunidades': total_oport, 'sin_geo': sin_geo,
                'visitas_mes': visitas_mes, 'sin_visita_30': sin_visita_30,
            },
            'filtros': {
                'estados': estados,
                'zonas': zonas, 'cultivos': cultivos, 'vendedores': vendedores,
                'estatus': [{'k': k, 'v': v} for k, v in ESTATUS_VENTAS],
            },
        }
