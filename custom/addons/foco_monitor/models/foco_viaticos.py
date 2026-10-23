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

from math import asin, cos, radians, sin, sqrt

from odoo import api, fields, models
from odoo.exceptions import UserError

# Umbral de bandera por caida de rendimiento vs el esperado del vehiculo. Es un
# arranque razonable; el Ing. decide el valor fino (decision pendiente en la
# propuesta). Vive aqui para cambiarlo sin tocar la logica.
CAIDA_RENDIMIENTO = 0.20        # 20% por debajo del esperado -> revisar
# Diferencia tolerada entre los km del odometro y los km del GPS, en %.
TOLERANCIA_GPS = 0.25


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
    foto = fields.Image(string='Foto', max_width=1024, max_height=1024)
    company_id = fields.Many2one('res.company', string='Empresa', required=True,
                                 default=lambda self: self.env.company)
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

    _sql_constraints = [
        ('matricula_uniq', 'unique(matricula, company_id)',
         'Ya hay un vehiculo con esa matricula en esta empresa.'),
    ]

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

    def action_validar(self):
        for v in self:
            if v.tipo == 'gasolina' and v.vehiculo_id and v.odometro:
                v.vehiculo_id.odometro_actual = max(v.vehiculo_id.odometro_actual or 0, v.odometro)
            v.estado = 'validado'
        self.evaluar_alerta()
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
