"""Viaticos, gasolina y vehiculos SOBRE Foco (propuesta 23-oct-2026).

Apartado de GASTOS para los vendedores de campo: el vendedor justifica todo con
una FOTO (ticket, odometro, caseta), la IA saca los datos, y Foco calcula el
rendimiento de combustible por vehiculo y lo CRUZA con el GPS que ya guarda
(`foco.location`) para cazar el robo de gasolina. Mas el mantenimiento semanal
de las camionetas (aceite/llantas, con foto, los lunes).

FASE 1 (en desarrollo, 23-oct-2026): modelo de datos + alta de gastos por foto con
OCR + presupuesto semanal, piloto Watanabe. Reusa lo que Foco ya tiene: multiempresa
+ monedas, la IA de vision (OCR del ticket), la cola movil, y la telemetria GPS
(`foco.location`) para el cruce de km. El cruce con GPS y las banderas de robo ya
estan en el modelo; las pantallas de vehiculos/mantenimiento son Fase 3.

Grounding: reunion "Foco AI Tools & Field Team Controls Review" (22-oct-2026).
"""

import calendar
from datetime import date
from math import asin, cos, radians, sin, sqrt

from odoo import api, fields, models
from odoo.exceptions import UserError

# Umbral de bandera por caida de rendimiento vs el esperado del vehiculo. Es un
# arranque razonable; el Ing. decide el valor fino (decision pendiente en la
# propuesta). Vive aqui para cambiarlo sin tocar la logica.
CAIDA_RENDIMIENTO = 0.20        # 20% por debajo del esperado -> revisar
# Diferencia tolerada entre los km del odometro y los km del GPS, en %.
TOLERANCIA_GPS = 0.25
# Con cuantos dias de anticipacion un documento del vehiculo (seguro, tenencia,
# letra) se marca "por vencer". Vive aqui para ajustarlo sin tocar la logica.
DIAS_AVISO_DOC = 30


def _haversine_km(lat1, lon1, lat2, lon2):
    """Kilometros en linea entre dos puntos (formula del haversine)."""
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


class FocoVehiculo(models.Model):
    """Una camioneta/coche de la empresa. Cambia de mano, asi que guarda QUIEN la
    trae esta semana; de ahi cuelgan las cargas, el rendimiento y las revisiones."""
    _name = 'foco.vehiculo'
    _description = 'Vehiculo de la empresa (Foco)'
    _order = 'name'

    name = fields.Char(string='Nombre', required=True)
    matricula = fields.Char(string='Matricula', required=True, index=True)
    foto = fields.Image(string='Foto del vehiculo', max_width=1024, max_height=1024)
    company_id = fields.Many2one('res.company', string='Empresa', required=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Moneda')
    active = fields.Boolean(default=True)

    conductor_id = fields.Many2one('hr.employee', string='Conductor de la semana',
                                   help='Quien trae el vehiculo ahora. Los coches cambian de mano.')
    odometro_actual = fields.Integer(string='Odometro actual (km)',
                                     help='Se actualiza con la ultima carga de gasolina registrada.')
    rendimiento_esperado = fields.Float(string='Rendimiento esperado (km/L)', digits=(6, 2),
                                        help='Linea base del vehiculo para detectar caidas.')
    # Reglas de mantenimiento (arranque simple; se amplia en la Fase 3).
    aceite_cada_km = fields.Integer(string='Cambio de aceite cada (km)', default=10000)
    odometro_ultimo_aceite = fields.Integer(string='Odometro del ultimo cambio de aceite')

    viatico_ids = fields.One2many('foco.viatico', 'vehiculo_id', string='Cargas y gastos')
    revision_ids = fields.One2many('foco.vehiculo.revision', 'vehiculo_id', string='Revisiones')

    # --- Documentos del vehiculo (Fase B, solo administracion) ---
    # Diego: "por parte de nosotros tenemos que llevar impuesto municipal, fecha y
    # seguro... la fecha de vencimiento del seguro, para no salir de coche sin
    # seguro". Esto NO lo ve el vendedor: es papeleo de la flota.
    seguro_aseguradora = fields.Char(string='Aseguradora')
    seguro_poliza = fields.Char(string='No. de poliza')
    seguro_vencimiento = fields.Date(string='Vence el seguro')
    tenencia_vencimiento = fields.Date(string='Vence tenencia / placas / impuesto')

    # --- Financiamiento: la "letra" (Diego: "pago de la letra... para hacer la
    # prediccion de los pagos"). A futuro se puede vincular a contabilidad. ---
    financiado = fields.Boolean(string='Vehiculo financiado')
    letra_monto = fields.Monetary(string='Monto de la letra')
    letra_dia_pago = fields.Integer(string='Dia de pago (del mes)',
                                    help='Dia del mes en que se paga la letra (1-31).')
    letra_pagos_total = fields.Integer(string='Letras totales (plazo)')
    letra_pagos_hechos = fields.Integer(string='Letras pagadas')

    # Alertas de vencimiento (calculadas al vuelo: usan "hoy", asi que NO se
    # almacenan; se recalculan en cada lectura y los filtros van por fecha).
    seguro_dias = fields.Integer(string='Dias para el seguro', compute='_compute_docs')
    seguro_estado = fields.Selection([('na', 'Sin dato'), ('ok', 'Vigente'),
                                      ('por_vencer', 'Por vencer'), ('vencido', 'Vencido')],
                                     string='Estado del seguro', compute='_compute_docs')
    tenencia_dias = fields.Integer(string='Dias para la tenencia', compute='_compute_docs')
    tenencia_estado = fields.Selection([('na', 'Sin dato'), ('ok', 'Vigente'),
                                        ('por_vencer', 'Por vencer'), ('vencido', 'Vencido')],
                                       string='Estado de tenencia', compute='_compute_docs')
    doc_alerta = fields.Selection([('ok', 'Al dia'), ('por_vencer', 'Por vencer'),
                                   ('vencido', 'Vencido')],
                                  string='Documentos', compute='_compute_docs')

    letra_proximo_pago = fields.Date(string='Proximo pago de letra', compute='_compute_letra')
    letra_pagos_restantes = fields.Integer(string='Letras restantes', compute='_compute_letra')
    letra_saldo = fields.Monetary(string='Saldo por pagar (letras)', compute='_compute_letra')

    # --- Costo de taller (Diego: "cada vez que vaya a taller, imputarle el costo,
    # ver cuanto nos ha costado"). La hoja de servicio / orden de mantenimiento. ---
    mantenimiento_ids = fields.One2many('foco.vehiculo.mantenimiento', 'vehiculo_id',
                                        string='Ordenes de mantenimiento')
    costo_taller_total = fields.Monetary(string='Costo de taller acumulado',
                                         compute='_compute_taller')
    mantenimiento_count = fields.Integer(string='Ordenes de taller', compute='_compute_taller')

    # El coche NO tiene dueño: va cambiando de mano. Las asignaciones son el
    # historial de quien lo tuvo y desde cuando; la activa dice donde esta ahora.
    asignacion_ids = fields.One2many('foco.vehiculo.asignacion', 'vehiculo_id', string='Asignaciones')
    asignacion_activa_id = fields.Many2one('foco.vehiculo.asignacion', compute='_compute_flota',
                                           string='Asignacion actual')
    tenedor_actual = fields.Char(compute='_compute_flota', store=True, string='Quien lo trae')
    asignado_desde = fields.Datetime(compute='_compute_flota', store=True, string='Lo trae desde')
    responsiva_pendiente = fields.Boolean(compute='_compute_flota', store=True,
                                          string='Responsiva sin firmar')

    _sql_constraints = [
        ('matricula_uniq', 'unique(matricula, company_id)',
         'Ya hay un vehiculo con esa matricula en esta empresa.'),
    ]

    @api.depends('asignacion_ids.activa', 'asignacion_ids.tenedor', 'asignacion_ids.desde',
                 'asignacion_ids.firmada', 'asignacion_ids.tipo')
    def _compute_flota(self):
        for v in self:
            act = v.asignacion_ids.filtered('activa')[:1]
            v.asignacion_activa_id = act.id if act else False
            v.tenedor_actual = (act.tenedor if act else False) or 'Sin asignar'
            v.asignado_desde = act.desde if act else False
            v.responsiva_pendiente = bool(act and act.tipo == 'empleado' and not act.firmada)

    @staticmethod
    def _estado_doc(fecha, hoy):
        """Clasifica una fecha de vencimiento: sin dato / vigente / por vencer /
        vencido, segun DIAS_AVISO_DOC dias de anticipacion."""
        if not fecha:
            return 'na'
        dias = (fecha - hoy).days
        if dias < 0:
            return 'vencido'
        if dias <= DIAS_AVISO_DOC:
            return 'por_vencer'
        return 'ok'

    @api.depends('seguro_vencimiento', 'tenencia_vencimiento')
    def _compute_docs(self):
        hoy = fields.Date.context_today(self)
        for v in self:
            v.seguro_dias = (v.seguro_vencimiento - hoy).days if v.seguro_vencimiento else 0
            v.seguro_estado = self._estado_doc(v.seguro_vencimiento, hoy)
            v.tenencia_dias = (v.tenencia_vencimiento - hoy).days if v.tenencia_vencimiento else 0
            v.tenencia_estado = self._estado_doc(v.tenencia_vencimiento, hoy)
            estados = (v.seguro_estado, v.tenencia_estado)
            v.doc_alerta = ('vencido' if 'vencido' in estados else
                            'por_vencer' if 'por_vencer' in estados else 'ok')

    def _proximo_pago_letra(self, hoy):
        """Fecha del proximo pago de la letra: el dia de pago del mes, hoy o
        despues. Si ya paso este mes, el del mes que viene. False si no aplica."""
        self.ensure_one()
        if not (self.financiado and self.letra_dia_pago and self.letra_pagos_restantes > 0):
            return False
        dia = max(1, min(self.letra_dia_pago, calendar.monthrange(hoy.year, hoy.month)[1]))
        cand = date(hoy.year, hoy.month, dia)
        if cand < hoy:
            y, m = (hoy.year + 1, 1) if hoy.month == 12 else (hoy.year, hoy.month + 1)
            dia = max(1, min(self.letra_dia_pago, calendar.monthrange(y, m)[1]))
            cand = date(y, m, dia)
        return cand

    @api.depends('financiado', 'letra_monto', 'letra_dia_pago',
                 'letra_pagos_total', 'letra_pagos_hechos')
    def _compute_letra(self):
        hoy = fields.Date.context_today(self)
        for v in self:
            v.letra_pagos_restantes = max((v.letra_pagos_total or 0) - (v.letra_pagos_hechos or 0), 0)
            v.letra_saldo = (v.letra_monto or 0.0) * v.letra_pagos_restantes
            v.letra_proximo_pago = v._proximo_pago_letra(hoy)

    @api.depends('mantenimiento_ids.costo')
    def _compute_taller(self):
        for v in self:
            v.costo_taller_total = sum(v.mantenimiento_ids.mapped('costo'))
            v.mantenimiento_count = len(v.mantenimiento_ids)

    def action_ver_mantenimiento(self):
        """Boton de estadistica: abre las ordenes de taller de este vehiculo."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Mantenimiento de %s' % self.name,
            'res_model': 'foco.vehiculo.mantenimiento',
            'view_mode': 'list,form',
            'domain': [('vehiculo_id', '=', self.id)],
            'context': {'default_vehiculo_id': self.id,
                        'default_odometro': self.odometro_actual or 0},
        }

    def action_asignar(self):
        """Abre el alta de una nueva asignacion (entregar el coche a alguien o a un
        lugar). Al guardarla se cierra sola la asignacion anterior."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Entregar vehiculo',
            'res_model': 'foco.vehiculo.asignacion',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_vehiculo_id': self.id,
                        'default_odometro_entrega': self.odometro_actual or 0},
        }

    def _toca_aceite(self):
        """True si segun el odometro ya toca cambio de aceite."""
        self.ensure_one()
        if not self.aceite_cada_km:
            return False
        return (self.odometro_actual - (self.odometro_ultimo_aceite or 0)) >= self.aceite_cada_km


class FocoViatico(models.Model):
    """Un gasto del vendedor en campo, capturado por FOTO. La IA de vision llena
    los datos; el vendedor confirma. En GASOLINA ademas lleva litros + odometro,
    que alimentan el rendimiento y el cruce con el GPS."""
    _name = 'foco.viatico'
    _description = 'Gasto de viaticos (Foco)'
    _order = 'fecha desc, id desc'

    TIPOS = [
        ('gasolina', 'Gasolina'), ('caseta', 'Caseta'), ('hotel', 'Hotel'),
        ('comida', 'Comida'), ('otro', 'Otro'),
    ]

    tipo = fields.Selection(TIPOS, string='Tipo', required=True, default='gasolina', index=True)
    fecha = fields.Date(string='Fecha', required=True, default=fields.Date.context_today, index=True)
    employee_id = fields.Many2one('hr.employee', string='Vendedor', required=True, index=True)
    company_id = fields.Many2one('res.company', string='Empresa', required=True,
                                 default=lambda self: self.env.company, index=True)

    # Dinero, en la moneda de la empresa (Mexico MXN, Peru PEN). El tipo de cambio
    # se guarda para cuadrar el deposito en USD de Watanabe con su gasto en soles.
    monto = fields.Monetary(string='Monto', required=True)
    currency_id = fields.Many2one('res.currency', string='Moneda',
                                  default=lambda self: self.env.company.currency_id, required=True)
    tipo_cambio = fields.Float(string='Tipo de cambio aplicado', digits=(12, 6),
                               help='USD->moneda local, cuando aplica (p.ej. Peru).')

    # La evidencia: la foto del ticket SIEMPRE se conserva (es la "hoja de gastos").
    foto = fields.Image(string='Foto del ticket', max_width=1920, max_height=1920, required=True)
    foto_odometro = fields.Image(string='Foto del odometro', max_width=1920, max_height=1920,
                                 help='Solo gasolina: la foto del cuentakilometros.')
    ocr_json = fields.Text(string='Lectura de la IA (JSON)',
                           help='Lo que la vision saco de la foto, antes de que el vendedor confirme.')

    # Donde se registro (la gasolinera, la caseta): para el mapa de gastos.
    lugar = fields.Char(string='Lugar / estacion')
    lat = fields.Float(string='Latitud', digits=(10, 7))
    lon = fields.Float(string='Longitud', digits=(10, 7))

    estado = fields.Selection([('pendiente', 'Pendiente'), ('validado', 'Validado'),
                               ('rechazado', 'Rechazado')],
                              string='Estado', default='pendiente', index=True)

    # Id que pone el telefono para deduplicar: la cola movil reenvia un gasto sin
    # internet y no debe duplicarlo (mismo principio que foco.visita).
    uuid = fields.Char(string='UUID (cola movil)', index=True, copy=False)

    # --- solo gasolina ---
    vehiculo_id = fields.Many2one('foco.vehiculo', string='Vehiculo', ondelete='restrict')
    litros = fields.Float(string='Litros', digits=(10, 3))
    precio_litro = fields.Float(string='Precio por litro', digits=(10, 3))
    odometro = fields.Integer(string='Odometro (km)')

    km_recorridos = fields.Integer(string='Km desde la carga anterior',
                                   compute='_compute_rendimiento', store=True)
    rendimiento = fields.Float(string='Rendimiento (km/L)', digits=(6, 2),
                               compute='_compute_rendimiento', store=True)
    km_gps = fields.Integer(string='Km reales (GPS)', readonly=True,
                            help='Suma de la trayectoria del GPS entre esta carga y la anterior.')
    alerta = fields.Selection([('ok', 'OK'), ('rendimiento', 'Rendimiento bajo'),
                               ('gps', 'No cuadra con GPS'), ('ambas', 'Rendimiento y GPS')],
                              string='Alerta', default='ok', index=True)

    @api.depends('tipo', 'odometro', 'litros', 'vehiculo_id')
    def _compute_rendimiento(self):
        for v in self:
            v.km_recorridos = 0
            v.rendimiento = 0.0
            if v.tipo != 'gasolina' or not v.vehiculo_id or not v.odometro:
                continue
            previa = v._carga_anterior()
            if previa and previa.odometro and v.odometro > previa.odometro:
                v.km_recorridos = v.odometro - previa.odometro
                if v.litros:
                    v.rendimiento = round(v.km_recorridos / v.litros, 2)

    def _carga_anterior(self):
        """La carga de gasolina previa del MISMO vehiculo (para los km recorridos)."""
        self.ensure_one()
        if not self.vehiculo_id:
            return self.browse()
        dom = [('vehiculo_id', '=', self.vehiculo_id.id), ('tipo', '=', 'gasolina'),
               ('odometro', '>', 0), ('id', '!=', self.id or 0)]
        if self.fecha:
            dom.append(('fecha', '<=', self.fecha))
        return self.search(dom, order='fecha desc, id desc', limit=1)

    def _km_recorridos_gps(self, desde, hasta):
        """Km reales que recorrio el vendedor entre dos momentos, sumando su
        trayectoria en `foco.location`. Es la pieza que delata el robo: compara los
        km del odometro con los km que de verdad manejo. Lado servidor; no captura
        nada nuevo, solo lee los puntos que Foco ya guarda."""
        self.ensure_one()
        if not (self.employee_id and desde and hasta):
            return 0
        puntos = self.env['foco.location'].sudo().search(
            [('employee_id', '=', self.employee_id.id), ('at', '>=', desde), ('at', '<=', hasta)],
            order='at asc')
        total = 0.0
        prev = None
        for p in puntos:
            if prev is not None and p.lat and p.lon:
                total += _haversine_km(prev.lat, prev.lon, p.lat, p.lon)
            prev = p
        return int(round(total))

    def evaluar_alerta(self):
        """Marca la carga segun el rendimiento y el cruce con el GPS. Se llama al
        validar (o por cron). El umbral exacto es decision del Ing."""
        for v in self.filtered(lambda x: x.tipo == 'gasolina'):
            baja_rend = bool(v.vehiculo_id.rendimiento_esperado and v.rendimiento
                             and v.rendimiento < v.vehiculo_id.rendimiento_esperado * (1 - CAIDA_RENDIMIENTO))
            previa = v._carga_anterior()
            desde = fields.Datetime.to_datetime(previa.fecha) if previa else None
            hasta = fields.Datetime.to_datetime(v.fecha) if v.fecha else None
            v.km_gps = v._km_recorridos_gps(desde, hasta) if (desde and hasta) else 0
            mal_gps = bool(v.km_recorridos and v.km_gps
                           and abs(v.km_recorridos - v.km_gps) > v.km_recorridos * TOLERANCIA_GPS)
            v.alerta = ('ambas' if (baja_rend and mal_gps) else
                        'rendimiento' if baja_rend else 'gps' if mal_gps else 'ok')

    @api.model_create_multi
    def create(self, vals_list):
        """Evalua la alerta de robo EN CUANTO entra la carga (no solo al validar),
        para que el administrador vea la bandera en la cola de pendientes antes de
        aprobar. Solo gasolina con odometro; lee el GPS que Foco ya guarda."""
        recs = super().create(vals_list)
        porevaluar = recs.filtered(lambda x: x.tipo == 'gasolina' and x.odometro)
        if porevaluar:
            porevaluar.evaluar_alerta()
        return recs

    def action_validar(self):
        for v in self:
            if v.tipo == 'gasolina' and v.vehiculo_id and v.odometro:
                v.vehiculo_id.odometro_actual = max(v.vehiculo_id.odometro_actual or 0, v.odometro)
            v.estado = 'validado'
        self.evaluar_alerta()
        return True

    def action_rechazar(self):
        """Rechaza el gasto: no cuenta para el presupuesto (el consumido excluye
        'rechazado') y sale de la cola de pendientes. No borra la evidencia."""
        self.write({'estado': 'rechazado'})
        return True

    def action_reabrir(self):
        """Devuelve a pendiente un gasto ya resuelto, por si se aprobo o rechazo
        por error."""
        self.write({'estado': 'pendiente'})
        return True


class FocoVehiculoRevision(models.Model):
    """La revision semanal del lunes: aceite y presion de llantas, con foto. La
    hace quien trae el coche esa semana; queda registro de quien, cuando y evidencia."""
    _name = 'foco.vehiculo.revision'
    _description = 'Revision semanal de vehiculo (Foco)'
    _order = 'fecha desc, id desc'

    vehiculo_id = fields.Many2one('foco.vehiculo', string='Vehiculo', required=True,
                                  ondelete='cascade', index=True)
    employee_id = fields.Many2one('hr.employee', string='Quien reviso', required=True)
    company_id = fields.Many2one(related='vehiculo_id.company_id', store=True, string='Empresa')
    fecha = fields.Date(string='Fecha', required=True, default=fields.Date.context_today, index=True)
    semana = fields.Char(string='Semana', help='AAAA-Www, para agrupar el checklist del lunes.')
    tipo = fields.Selection([('aceite', 'Nivel de aceite'), ('llantas', 'Presion de llantas'),
                             ('general', 'General')], string='Tipo', required=True)
    resultado = fields.Selection([('ok', 'OK'), ('alerta', 'Necesita atencion')],
                                 string='Resultado', default='ok')
    foto = fields.Image(string='Foto evidencia', max_width=1920, max_height=1920, required=True)
    nota = fields.Char(string='Nota')


class FocoViaticoPresupuesto(models.Model):
    """El presupuesto de la semana (los $8,000): lo asignado por vendedor y periodo.
    Lo consumido se suma de los viaticos; el vendedor ve cuanto le queda."""
    _name = 'foco.viatico.presupuesto'
    _description = 'Presupuesto semanal de viaticos (Foco)'
    _order = 'desde desc'

    employee_id = fields.Many2one('hr.employee', string='Vendedor', required=True, index=True)
    company_id = fields.Many2one('res.company', string='Empresa', required=True,
                                 default=lambda self: self.env.company)
    desde = fields.Date(string='Desde', required=True)
    hasta = fields.Date(string='Hasta', required=True)
    monto = fields.Monetary(string='Monto asignado', required=True)
    currency_id = fields.Many2one('res.currency', string='Moneda',
                                  default=lambda self: self.env.company.currency_id, required=True)
    consumido = fields.Monetary(string='Consumido', compute='_compute_consumido')
    restante = fields.Monetary(string='Restante', compute='_compute_consumido')

    @api.depends('employee_id', 'desde', 'hasta', 'monto')
    def _compute_consumido(self):
        Viatico = self.env['foco.viatico']
        for p in self:
            gastos = Viatico.search([
                ('employee_id', '=', p.employee_id.id), ('estado', '!=', 'rechazado'),
                ('fecha', '>=', p.desde), ('fecha', '<=', p.hasta)]) if p.employee_id else Viatico
            p.consumido = sum(gastos.mapped('monto'))
            p.restante = (p.monto or 0.0) - p.consumido

    @api.constrains('desde', 'hasta')
    def _check_fechas(self):
        for p in self:
            if p.desde and p.hasta and p.hasta < p.desde:
                raise UserError('La fecha "hasta" no puede ser antes de "desde".')


class FocoVehiculoAsignacion(models.Model):
    """Quien trae cada coche y desde cuando. El coche NO tiene dueno: va cambiando
    de mano (un vendedor, el almacen, otra ciudad). Cada asignacion es un tramo con
    fecha de entrega; al cambiar de mano se cierra (se le pone 'hasta'). El historial
    dice 'de tal dia a tal dia lo tuvo Pepe'. Cuando el tenedor es una PERSONA, la
    recepcion se firma en el telefono como carta responsiva."""
    _name = 'foco.vehiculo.asignacion'
    _description = 'Asignacion de vehiculo (quien lo trae)'
    _order = 'desde desc, id desc'

    vehiculo_id = fields.Many2one('foco.vehiculo', string='Vehiculo', required=True,
                                  ondelete='cascade', index=True)
    company_id = fields.Many2one(related='vehiculo_id.company_id', store=True, string='Empresa')
    tipo = fields.Selection([('empleado', 'Persona'), ('ubicacion', 'Lugar')],
                            string='Tenedor', required=True, default='empleado')
    employee_id = fields.Many2one('hr.employee', string='Persona')
    ubicacion = fields.Char(string='Lugar', help='Almacen, ciudad u otro sitio donde esta el coche.')
    desde = fields.Datetime(string='Desde', required=True, default=fields.Datetime.now, index=True)
    hasta = fields.Datetime(string='Hasta', index=True, help='Vacio = lo trae ahora.')
    activa = fields.Boolean(string='Activa', compute='_compute_activa', store=True, index=True)
    tenedor = fields.Char(string='Quien lo trae', compute='_compute_tenedor', store=True)
    odometro_entrega = fields.Integer(string='Odometro a la entrega')
    nota = fields.Char(string='Nota')
    # Carta responsiva (solo cuando el tenedor es persona).
    firmada = fields.Boolean(string='Responsiva firmada', default=False, index=True)
    firma = fields.Image(string='Firma', max_width=1024, max_height=512)
    firma_fecha = fields.Datetime(string='Fecha de firma')

    @api.depends('hasta')
    def _compute_activa(self):
        for a in self:
            a.activa = not a.hasta

    @api.depends('tipo', 'employee_id', 'ubicacion')
    def _compute_tenedor(self):
        for a in self:
            a.tenedor = (a.employee_id.name if a.tipo == 'empleado' else a.ubicacion) or ''

    @api.constrains('tipo', 'employee_id', 'ubicacion')
    def _check_tenedor(self):
        for a in self:
            if a.tipo == 'empleado' and not a.employee_id:
                raise UserError('Elige la persona que recibe el vehiculo.')
            if a.tipo == 'ubicacion' and not (a.ubicacion or '').strip():
                raise UserError('Escribe el lugar donde queda el vehiculo.')

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for a in recs:
            if not a.hasta:
                a._cerrar_otras_y_sincronizar()
        return recs

    def _cerrar_otras_y_sincronizar(self):
        """Al abrir una asignacion activa, cierra las demas abiertas del mismo coche
        y refleja al tenedor actual en el vehiculo."""
        self.ensure_one()
        otras = self.search([('vehiculo_id', '=', self.vehiculo_id.id),
                             ('id', '!=', self.id), ('hasta', '=', False)])
        if otras:
            otras.write({'hasta': self.desde or fields.Datetime.now()})
        veh = self.vehiculo_id
        veh.conductor_id = self.employee_id.id if self.tipo == 'empleado' else False
        if self.odometro_entrega:
            veh.odometro_actual = max(veh.odometro_actual or 0, self.odometro_entrega)

    def action_cerrar(self):
        """Devolver el coche: cierra el tramo actual."""
        ahora = fields.Datetime.now()
        for a in self:
            a.hasta = ahora
            if a.tipo == 'empleado' and a.vehiculo_id.conductor_id \
               and a.vehiculo_id.conductor_id.id == a.employee_id.id:
                a.vehiculo_id.conductor_id = False
        return True

    def firmar(self, firma_b64):
        """Registra la firma de la carta responsiva (desde el telefono)."""
        self.ensure_one()
        self.write({'firma': firma_b64, 'firmada': True,
                    'firma_fecha': fields.Datetime.now()})
        return True


class FocoVehiculoMantenimiento(models.Model):
    """Una visita al taller: la hoja de servicio / orden de mantenimiento. Diego:
    'cada vez que vaya el coche a taller, imputarle el costo, ver cuanto nos ha
    costado'. Por un lado el DINERO (costo) y por otro el FUNCIONAMIENTO del coche
    (que se hizo, proximo servicio). Es papeleo de administracion, no del vendedor."""
    _name = 'foco.vehiculo.mantenimiento'
    _description = 'Orden de mantenimiento / hoja de servicio (Foco)'
    _order = 'fecha desc, id desc'

    vehiculo_id = fields.Many2one('foco.vehiculo', string='Vehiculo', required=True,
                                  ondelete='cascade', index=True)
    company_id = fields.Many2one(related='vehiculo_id.company_id', store=True, string='Empresa')
    currency_id = fields.Many2one(related='vehiculo_id.currency_id', string='Moneda')
    fecha = fields.Date(string='Fecha', required=True, default=fields.Date.context_today, index=True)
    tipo = fields.Selection([('preventivo', 'Preventivo'), ('correctivo', 'Correctivo'),
                             ('otro', 'Otro')], string='Tipo', default='preventivo')
    taller = fields.Char(string='Taller')
    odometro = fields.Integer(string='Odometro (km)')
    concepto = fields.Char(string='Concepto', required=True,
                           help='Resumen de lo que se hizo (p.ej. afinacion, frenos).')
    descripcion = fields.Text(string='Detalle')
    costo = fields.Monetary(string='Costo', required=True)
    proximo_servicio_km = fields.Integer(string='Proximo servicio a los (km)')
    foto = fields.Image(string='Hoja de servicio', max_width=1920, max_height=1920,
                        help='Foto de la hoja de servicio / factura del taller.')
