import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Que tipo de pausa. La llave es la misma que manda el agente.
KINDS_PAUSA = [
    ('idle', 'Sin teclado ni mouse'),
    ('locked', 'Sesion bloqueada'),
    # Sin agente corriendo: el equipo apagado, reiniciando, o el agente detenido.
    # El agente no puede distinguirlo desde dentro; los eventos del equipo si.
    ('offline', 'Equipo apagado o agente detenido'),
]


def _corta(intervalos, ventana):
    """Interseccion de dos listas de intervalos (inicio, fin)."""
    out = []
    for a, b in intervalos:
        for c, d in ventana:
            x, y = max(a, c), min(b, d)
            if y > x:
                out.append((x, y))
    return sorted(out)


def _quita(intervalos, quitar):
    """Lo que queda de `intervalos` despues de quitarle `quitar`."""
    out = list(intervalos)
    for c, d in quitar:
        nuevo = []
        for a, b in out:
            if d <= a or c >= b:
                nuevo.append((a, b))
                continue
            if c > a:
                nuevo.append((a, c))
            if d < b:
                nuevo.append((d, b))
        out = nuevo
    return sorted(out)


def _seg(intervalos):
    return sum((b - a).total_seconds() for a, b in intervalos)


class FocoPausa(models.Model):
    """Una pausa CORTA: un periodo sin actividad que no llego al umbral de hueco.

    Hasta el 14-oct-2026 el agente las detectaba y las tiraba, y en el tablero
    quedaban como una RESTA ("sin actividad") que nadie podia explicar: el dueno
    pidio ver ese tiempo con su hora, sin suposiciones. No se le preguntan a
    nadie: son hechos, no periodos a justificar.
    """

    _name = 'foco.pausa'
    _description = 'Pausa corta'
    _order = 'start'

    _uniq = models.Constraint(
        'unique(computer_id, start)',
        'Esa pausa ya estaba registrada para el equipo.')

    computer_id = fields.Many2one('foco.computer', string='Equipo', required=True,
                                  ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='computer_id.employee_id', string='Empleado',
                                  store=True, index=True)
    date = fields.Date(string='Dia', index=True, help='El dia LOCAL de la persona.')
    start = fields.Datetime(string='Paro', required=True,
                            help='El ultimo input antes de la pausa.')
    inactivo = fields.Datetime(
        string='Inactiva desde', required=True,
        help='Desde cuando el agente la cuenta inactiva. Lo de antes (el primer '
             'minuto sin input) todavia cuenta como activo: asi mide el agente.')
    stop = fields.Datetime(string='Volvio', required=True, help='El siguiente input.')
    kind = fields.Selection(KINDS_PAUSA, string='Tipo', required=True, default='idle')
    app = fields.Char(string='Al frente', help='Lo que estaba al frente cuando paro.')
    seconds = fields.Integer(string='Duracion (s)', compute='_compute_seconds', store=True)

    @api.depends('start', 'stop')
    def _compute_seconds(self):
        for r in self:
            r.seconds = int((r.stop - r.start).total_seconds()) if r.start and r.stop else 0

    @api.model
    def record_pausas(self, computer, pausas):
        """Guarda las pausas que manda el agente. Idempotente por (equipo, inicio)."""
        if not computer or not pausas:
            return 0
        Absence = self.env['foco.absence'].sudo()
        employee = computer.employee_id
        vals_list, vistos = [], set()
        for p in pausas:
            if not isinstance(p, dict):
                continue
            ini = Absence._to_utc(p.get('start'), employee)
            fin = Absence._to_utc(p.get('end'), employee)
            ina = Absence._to_utc(p.get('inactivo'), employee) or ini
            if not ini or not fin or fin <= ini:
                continue
            ina = min(max(ina, ini), fin)
            if (ini in vistos):
                continue
            vistos.add(ini)
            vals_list.append({
                'computer_id': computer.id, 'start': ini, 'inactivo': ina, 'stop': fin,
                'kind': p.get('kind') if p.get('kind') in dict(KINDS_PAUSA) else 'idle',
                'app': (p.get('app') or '')[:120] or False,
                'date': Absence._dia_local(employee, ini) if employee else ini.date(),
            })
        if not vals_list:
            return 0
        ya = set(self.sudo().search([('computer_id', '=', computer.id),
                                     ('start', 'in', [v['start'] for v in vals_list])]).mapped('start'))
        nuevos = [v for v in vals_list if v['start'] not in ya]
        if nuevos:
            self.sudo().create(nuevos)
        return len(vals_list)

    # ------------------------------------------------------------ la pagina
    @api.model
    def explicar(self, employee_id, fecha):
        """El tramo "sin actividad" del tablero, explicado SOLO con lo medido.

        Devuelve la linea del dia de la persona -con actividad, cada pausa con su
        hora, los huecos de 15 min o mas, antes de su primera actividad y despues
        de la ultima-, la cuenta del tablero tal cual, y cuanto de esa cuenta
        quedo medido. Lo que no se midio se dice: nunca se rellena.
        """
        # Con los permisos de quien pregunta: si no ve el equipo de esa persona,
        # no ve su dia.
        comps = self.env['foco.computer'].search([('employee_id', '=', int(employee_id))])
        if not comps:
            return {'ok': False, 'error': 'sin_acceso'}
        emp = comps[0].employee_id
        Settings = self.env['foco.settings'].sudo()
        tz = Settings._tzinfo_for(emp)
        d = fields.Date.to_date(fecha)
        base = tz.localize(datetime.combine(d, time(0, 0)))
        u = lambda dt_local: dt_local.astimezone(pytz.UTC).replace(tzinfo=None)
        ini_dia, fin_dia = u(base), u(base + timedelta(days=1))
        ahora = fields.Datetime.now()
        es_hoy = ini_dia <= ahora < fin_dia
        corte = min(fin_dia, ahora)
        ultimo_envio = max([c.last_seen for c in comps.sudo() if c.last_seen] or [None]) \
            if es_hoy else None
        datos_hasta = min(corte, ultimo_envio) if (es_hoy and ultimo_envio) else corte

        def hl(dt):          # UTC naive -> hora local decimal del dia
            loc = pytz.UTC.localize(dt).astimezone(tz)
            dias = (loc.date() - d).days
            return round(max(0.0, min(24.0, dias * 24 + loc.hour + loc.minute / 60.0
                                      + loc.second / 3600.0)), 4)

        def hms(dt):
            return pytz.UTC.localize(dt).astimezone(tz).strftime('%H:%M:%S') if dt else None

        cal = emp.resource_calendar_id
        iso = d.isoweekday()
        hora = lambda h: u(base + timedelta(hours=h))
        jornada_dia = [(hora(a), hora(b)) for a, b in Settings.calendar_intervals(cal).get(iso, [])]
        J = _corta(jornada_dia, [(ini_dia, corte)])
        comida = [(hora(a), hora(b)) for a, b in Settings.break_intervals(cal).get(iso, [])]

        # La cuenta del tablero, con la MISMA funcion: el numero que se clico.
        res = self.env['foco.absence'].dashboard_summary(str(d), str(d)).get(str(emp.id), {})
        huecos_h = sum(res.get(k, 0.0) for k in ('justified', 'aprobado_h', 'vacaciones_h',
                                                 'por_decidir_h', 'unexplained_h', 'rechazado_h'))
        resta = {'jornada_h': res.get('jornada_h', _seg(J) / 3600.0),
                 'activo_h': res.get('activo_jornada_h', 0.0),
                 'huecos_h': huecos_h,
                 'sin_actividad_h': res.get('sin_actividad_h', 0.0)}

        huecos = self.env['foco.absence'].sudo().search(
            [('employee_id', '=', emp.id), ('start', '<', fin_dia), ('stop', '>', ini_dia)],
            order='start')
        G = [(h.start, h.stop) for h in huecos]
        acts = self.env['foco.actividad'].sudo().search(
            [('employee_id', '=', emp.id), ('date', '=', d)])
        primera = min(acts.mapped('primera')) if acts else None
        ultima = max(acts.mapped('ultima')) if acts else None
        desde = min(acts.mapped('desde')) if acts else None
        pausas = self.sudo().search([('employee_id', '=', emp.id), ('date', '=', d)], order='start')
        medido = bool(acts or pausas)

        nombres = {}
        if pausas:
            for a in self.env['foco.app'].sudo().search(
                    [('exe', 'in', list({p.app for p in pausas if p.app}))]):
                nombres[a.exe] = a.name

        tramos = []           # para dibujar
        piezas = {'antes': 0.0, 'idle': 0.0, 'locked': 0.0, 'offline': 0.0,
                  'despues': 0.0, 'sin_datos': 0.0, 'no_medido': 0.0}

        def poner(tipo, intervalos, cuenta=True, **extra):
            for a, b in intervalos:
                tramos.append(dict(tipo=tipo, a=hl(a), b=hl(b), ini=hms(a), fin=hms(b),
                                   min=round((b - a).total_seconds() / 60.0, 2), **extra))
                if cuenta and tipo in piezas:
                    piezas[tipo] += (b - a).total_seconds() / 3600.0

        # Huecos de 15 min o mas: su propio tramo en el tablero. Aqui solo de
        # referencia, para que la linea del dia no tenga agujeros.
        etq_estado = dict(self.env['foco.absence']._fields['state'].selection)
        for h in huecos:
            poner('hueco', _corta([(h.start, h.stop)], [(ini_dia, corte)]), cuenta=False,
                  estado=h.state, estado_label=etq_estado.get(h.state, h.state))

        P_full = [(p.start, p.stop) for p in pausas]
        lista_pausas = []
        for p in pausas:
            inact = _quita(_corta([(p.inactivo, p.stop)], J), G)
            poner(p.kind, _quita(_corta([(p.start, p.stop)], [(ini_dia, corte)]), G), cuenta=False,
                  pausa_id=p.id)
            piezas[p.kind] += _seg(inact) / 3600.0
            lista_pausas.append({
                'id': p.id, 'kind': p.kind, 'kind_label': dict(KINDS_PAUSA)[p.kind],
                'ini': hms(p.start), 'inactiva': hms(p.inactivo), 'fin': hms(p.stop),
                'dur_min': round(p.seconds / 60.0, 1),
                'cuenta_min': round(_seg(inact) / 60.0, 1),
                'dentro': bool(inact),
                'app': nombres.get(p.app, p.app) or '',
            })

        if medido:
            # Lo no medido: la parte del dia antes de que este agente midiera.
            if desde and desde > ini_dia:
                poner('no_medido', _quita(_corta([(ini_dia, desde)], J), G))
            if primera:
                poner('antes', _quita(_corta([(max(ini_dia, desde or ini_dia), primera)], J),
                                      G + P_full))
                # Con actividad: entre la primera y la ultima, todo lo que no es
                # pausa ni hueco. Es un HECHO, no un supuesto: el agente anota
                # TODA inactividad de 1 min o mas, asi que lo que queda tuvo
                # input (o llamada) cada menos de un minuto.
                poner('activo', _quita(_corta([(primera, ultima)], [(ini_dia, corte)]),
                                       G + P_full), cuenta=False)
                poner('despues', _quita(_corta([(ultima, datos_hasta)], J), G + P_full))
            else:
                poner('no_medido', _quita(_corta([(desde or ini_dia, datos_hasta)], J), G + P_full))
            if es_hoy and datos_hasta < corte:
                poner('sin_datos', _corta([(datos_hasta, corte)], J))
        else:
            poner('no_medido', _quita(J, G))

        # La cuenta: lo medido contra la resta del tablero. El tablero cuenta el
        # primer minuto de cada hueco DOS veces (como activo y dentro del hueco):
        # es la misma forma de medir del agente. Se calcula, no se supone.
        umbrales = sorted((p.inactivo - p.start).total_seconds() for p in pausas if p.kind == 'idle')
        umbral = umbrales[len(umbrales) // 2] if umbrales else 60.0
        # El primer minuto de cada hueco, si cae en la jornada: el tablero lo
        # resta como activo Y como parte del hueco.
        dobles = sum(_seg(_corta([(g0, g0 + timedelta(seconds=umbral))], J)) for g0, _g1 in G) / 3600.0
        # La parte de un hueco que el tablero NO resta: el hueco guarda solo lo
        # que cae dentro de lo checado (con checador, despues de checar salida
        # no es jornada), pero en la linea del dia si es hueco.
        no_restado = sum(max(0.0, _seg(_corta([(h.start, h.stop)], J)) - (h.expected_seconds or 0.0))
                         for h in huecos) / 3600.0
        medido_h = sum(piezas[k] for k in ('antes', 'idle', 'locked', 'offline', 'despues'))
        # Lo no medido NO se suma: es una ventana sin registro, no tiempo
        # inactivo. Cuanto de ella fue inactividad no se sabe, y no se adivina:
        # lo que el tablero cuente de mas queda como "sin hora".
        explicado = medido_h + piezas['sin_datos'] - dobles + no_restado
        cuadre = {
            'resta_h': round(resta['sin_actividad_h'], 4),
            'medido_h': round(medido_h, 4),
            'sin_datos_h': round(piezas['sin_datos'], 4),
            'no_medido_h': round(piezas['no_medido'], 4),
            'dobles_h': round(dobles, 4), 'n_huecos': len(G), 'umbral_s': int(umbral),
            'no_restado_h': round(no_restado, 4),
            # Lo que la resta del tablero dice y lo medido no explica. Se muestra
            # tal cual: si no es cero, no se inventa la causa.
            'diferencia_h': round(resta['sin_actividad_h'] - max(0.0, explicado), 4),
        }

        # Ventana para dibujar: la jornada, ampliada a lo medido, con aire.
        horas = [x for t in tramos for x in (t['a'], t['b'])] + \
                [x for a, b in jornada_dia for x in (hl(a), hl(b))]
        lo = max(0.0, (min(horas) if horas else 8.0) - 0.25)
        hi = min(24.0, (max(horas) if horas else 18.0) + 0.25)

        eventos = []
        if not medido:
            etq = dict(self.env['foco.event']._fields['kind'].selection)
            for e in self.env['foco.event'].sudo().search(
                    [('employee_id', '=', emp.id), ('at', '>=', ini_dia), ('at', '<', fin_dia)],
                    order='at'):
                eventos.append({'h': hms(e.at), 'label': etq.get(e.kind, e.kind)})

        return {
            'ok': True, 'empleado': {'id': emp.id, 'name': emp.name},
            'fecha': str(d), 'es_hoy': es_hoy,
            'medido': medido, 'desde': hms(desde) if desde and desde > ini_dia else None,
            'datos_hasta': hms(datos_hasta) if es_hoy else None,
            'primera': hms(primera), 'ultima': hms(ultima),
            'primera_h': hl(primera) if primera else None,
            'ultima_h': hl(ultima) if ultima else None,
            'ventana': [round(lo, 2), round(hi, 2)],
            'jornada': [[hl(a), hl(b)] for a, b in jornada_dia],
            'comida': [[hl(a), hl(b)] for a, b in comida],
            'resta': {k: round(v, 4) for k, v in resta.items()},
            'piezas': {k: round(v, 4) for k, v in piezas.items()},
            'n': {k: sum(1 for p in pausas if p.kind == k) for k in dict(KINDS_PAUSA)},
            'cuadre': cuadre,
            'tramos': sorted(tramos, key=lambda t: (t['a'], t['b'])),
            'pausas': lista_pausas,
            'eventos': eventos,
        }


class FocoActividad(models.Model):
    """Primera y ultima actividad REAL del dia de cada equipo (input o llamada),
    y desde cuando el agente la mide ese dia. Sin `desde`, el dia en que se
    instala una version a mediodia diria "llego a las 12:00"."""

    _name = 'foco.actividad'
    _description = 'Actividad del dia por equipo'
    _order = 'date desc'

    _uniq = models.Constraint('unique(computer_id, date)',
                              'Ese dia ya estaba registrado para el equipo.')

    computer_id = fields.Many2one('foco.computer', string='Equipo', required=True,
                                  ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='computer_id.employee_id', string='Empleado',
                                  store=True, index=True)
    date = fields.Date(string='Dia', required=True, index=True)
    desde = fields.Datetime(string='Medido desde')
    primera = fields.Datetime(string='Primera actividad')
    ultima = fields.Datetime(string='Ultima actividad')

    @api.model
    def record_actividad(self, computer, filas):
        if not computer or not filas:
            return 0
        Absence = self.env['foco.absence'].sudo()
        employee = computer.employee_id
        n = 0
        for f in filas:
            if not isinstance(f, dict) or not f.get('day'):
                continue
            dia = fields.Date.to_date(f.get('day'))
            vals = {k: Absence._to_utc(f.get(k), employee) for k in ('desde', 'primera', 'ultima')}
            if not dia or not vals['primera']:
                continue
            rec = self.sudo().search([('computer_id', '=', computer.id), ('date', '=', dia)], limit=1)
            if rec:
                cambia = {}
                if vals['desde'] and (not rec.desde or vals['desde'] < rec.desde):
                    cambia['desde'] = vals['desde']
                if not rec.primera or vals['primera'] < rec.primera:
                    cambia['primera'] = vals['primera']
                if vals['ultima'] and (not rec.ultima or vals['ultima'] > rec.ultima):
                    cambia['ultima'] = vals['ultima']
                if cambia:
                    rec.write(cambia)
            else:
                self.sudo().create(dict(computer_id=computer.id, date=dia,
                                        desde=vals['desde'] or vals['primera'],
                                        primera=vals['primera'],
                                        ultima=vals['ultima'] or vals['primera']))
            n += 1
        return n
