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

    foco_visita_ids = fields.One2many('foco.visita', 'partner_id',
                                      string='Visitas')
    foco_visita_count = fields.Integer(string='Numero de visitas',
                                       compute='_compute_foco_visita')
    foco_ultima_visita = fields.Datetime(string='Ultima visita',
                                         compute='_compute_foco_visita')

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

        filas, por_estatus, sin_geo = [], {}, 0
        for c in clientes:
            if not (c.partner_latitude or c.partner_longitude):
                sin_geo += 1
            ult, nvis = ult_visita.get(c.id, (False, 0))
            est = c.foco_estatus or 'prospecto'
            por_estatus[est] = por_estatus.get(est, 0) + 1
            filas.append({
                'id': c.id, 'name': c.display_name,
                'lat': c.partner_latitude, 'lng': c.partner_longitude,
                'estatus': est, 'color': ESTATUS_COLOR.get(est, '#94a3b8'),
                'empaque': c.foco_empaque or '', 'zona': c.foco_zona or '',
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
                'total': len(clientes), 'por_estatus': por_estatus,
                'oportunidades': total_oport, 'sin_geo': sin_geo,
                'visitas_mes': visitas_mes, 'sin_visita_30': sin_visita_30,
            },
            'filtros': {
                'zonas': zonas, 'cultivos': cultivos, 'vendedores': vendedores,
                'estatus': [{'k': k, 'v': v} for k, v in ESTATUS_VENTAS],
            },
        }
