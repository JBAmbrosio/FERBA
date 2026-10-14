import json
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

# ------------------------------------------------------------------ la regla
# En que caja cae cada minuto del gris (fase 1 de la propuesta del 14-oct-2026,
# C:\dev\Ferba\docs\foco-sin-actividad-con-evidencia-propuesta-2026-10-14.md).
# Foco solo afirma lo que puede probar: sin una senal medida, el minuto queda
# "sin prueba" y se dice asi. Nada pasa de caja por deduccion.
REGLA = 'v1 · 14-oct-2026'
CAJAS = {
    'presente': 'Probado: en el equipo',
    'fuera': 'Probado: fuera del equipo o en otra cosa',
    'sin_prueba': 'Sin prueba',
}
# Bytes distintos (de los 1,024 de la huella del agente) para decir que la
# pantalla se movio entre dos huellas. A esa escala el cursor y el reloj de la
# barra no llegan; un video o un plano que gira cambian decenas.
PANT_MOV_BYTES = 8
# Que parte de las muestras de una pausa tiene que tener la senal.
FRACCION = 0.5
# Eventos del registro de Windows que prueban que el equipo no estaba prendido.
EVENTOS_EQUIPO = ('apagado', 'apagado_inesperado', 'apagado_solicitado', 'suspendido',
                  'encendido', 'reanudado')
# Por que cae un minuto en su caja: (caja, frase).
MOTIVOS = {
    'checador_antes': ('fuera', 'Aún no checaba entrada'),
    'checador_despues': ('fuera', 'Ya había checado salida'),
    'checador_fuera': ('fuera', 'Fuera de lo checado'),
    'bloqueo': ('fuera', 'Sesión bloqueada'),
    'apagado': ('fuera', 'Equipo apagado o suspendido'),
    'sonido': ('fuera', 'Sonaba una distracción'),
    'video': ('fuera', 'Pantalla en movimiento con una distracción al frente'),
    'quieta': ('sin_prueba', 'Pantalla quieta, sin tocar nada'),
    'movimiento': ('sin_prueba', 'Pantalla en movimiento, sin tocar nada'),
    'corta': ('sin_prueba', 'Sin tocar nada (muy corta para medir la pantalla)'),
    'sin_medir': ('sin_prueba', 'Sin tocar nada (agente sin medición de pantalla y sonido)'),
    'sin_agente': ('sin_prueba', 'Sin agente corriendo y sin evento de apagado'),
    'antes': ('sin_prueba', 'Antes de su primera actividad'),
    'despues': ('sin_prueba', 'Después de su última actividad'),
}


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


def _distraccion(cat):
    """La misma definicion que el tablero: peso <= 0 y no es ruido del sistema."""
    return bool(cat) and cat.weight <= 0 and not cat.is_system


def _limpia_evid(ev):
    """Lo que manda el agente, acotado y con tipos seguros."""
    if not isinstance(ev, dict):
        return {}
    out = {}
    pant = ev.get('pant')
    if isinstance(pant, list):
        out['pant'] = [int(x) for x in pant[:60] if isinstance(x, (int, float))]
    n = ev.get('aud_n')
    if isinstance(n, (int, float)) and n > 0:
        out['aud_n'] = int(n)
        aud = ev.get('aud') if isinstance(ev.get('aud'), dict) else {}
        out['aud'] = {str(k)[:80].lower(): int(v) for k, v in list(aud.items())[:10]
                      if isinstance(v, (int, float))}
    if ev.get('host'):
        out['host'] = str(ev['host'])[:255].strip().lower()
    return out


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
    host = fields.Char(string='Sitio al frente')
    evid = fields.Text(
        string='Medido durante la pausa',
        help='Lo que el agente midio mientras duro (desde el agente 2026.10.14.2): '
             'cuantos bytes cambio la huella de pantalla entre muestras, que '
             'programas sonaron y en cuantas muestras, y el sitio al frente. '
             'Nunca imagen ni sonido.')
    seconds = fields.Integer(string='Duracion (s)', compute='_compute_seconds', store=True)

    @api.depends('start', 'stop')
    def _compute_seconds(self):
        for r in self:
            r.seconds = int((r.stop - r.start).total_seconds()) if r.start and r.stop else 0

    def _evid(self):
        self.ensure_one()
        try:
            v = json.loads(self.evid or '{}')
        except ValueError:
            return {}
        return v if isinstance(v, dict) else {}

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
            ev = _limpia_evid(p.get('evid'))
            vals_list.append({
                'computer_id': computer.id, 'start': ini, 'inactivo': ina, 'stop': fin,
                'kind': p.get('kind') if p.get('kind') in dict(KINDS_PAUSA) else 'idle',
                'app': (p.get('app') or '')[:120] or False,
                'host': ev.get('host') or False,
                'evid': json.dumps(ev) if ev else False,
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

    # ------------------------------------------------ lo que la lectura usa
    @api.model
    def _contexto(self, employees, d_ini, d_fin):
        """Todo lo que leer un dia necesita, en lecturas de bloque e indexado
        por persona y dia: el tablero lo pide para todos de una vez."""
        Settings = self.env['foco.settings'].sudo()
        settings = Settings.get_settings()
        lo = datetime.combine(d_ini - timedelta(days=1), time(0, 0))
        hi = datetime.combine(d_fin + timedelta(days=2), time(0, 0))
        ids = employees.ids
        ctx = {'pausas': {}, 'acts': {}, 'huecos': {}, 'asist': {}, 'eventos': {},
               'cal': {}, 'comps': {}}
        pausas = self.sudo().search([('employee_id', 'in', ids), ('date', '>=', d_ini),
                                     ('date', '<=', d_fin)], order='start')
        for p in pausas:
            ctx['pausas'].setdefault((p.employee_id.id, p.date), []).append(p)
        for a in self.env['foco.actividad'].sudo().search(
                [('employee_id', 'in', ids), ('date', '>=', d_ini), ('date', '<=', d_fin)]):
            ctx['acts'].setdefault((a.employee_id.id, a.date), []).append(a)
        for h in self.env['foco.absence'].sudo().search(
                [('employee_id', 'in', ids), ('start', '<', hi), ('stop', '>', lo)], order='start'):
            ctx['huecos'].setdefault(h.employee_id.id, []).append(h)
        for e in self.env['foco.event'].sudo().search(
                [('employee_id', 'in', ids), ('kind', 'in', EVENTOS_EQUIPO),
                 ('at', '>=', lo), ('at', '<', hi)], order='at'):
            ctx['eventos'].setdefault(e.computer_id.id, []).append(e)
        if 'hr.attendance' in self.env:
            for a in self.env['hr.attendance'].sudo().search(
                    [('employee_id', 'in', ids), ('check_in', '<', hi),
                     '|', ('check_out', '=', False), ('check_out', '>', lo)], order='check_in'):
                ctx['asist'].setdefault(a.employee_id.id, []).append(a)
        comps = self.env['foco.computer'].sudo().search([('employee_id', 'in', ids)])
        for c in comps:
            ctx['comps'].setdefault(c.employee_id.id, self.env['foco.computer'].sudo())
            ctx['comps'][c.employee_id.id] |= c
        ctx['etq_evento'] = dict(self.env['foco.event']._fields['kind'].selection)
        ctx['etq_estado'] = dict(self.env['foco.absence']._fields['state'].selection)
        # Musica: permitida por decision del dueno (9-oct). Lo que suena en un
        # reproductor no prueba nada ni en un sentido ni en otro.
        ctx['musica'] = [x.strip().lower() for x in (settings.vision_musica_apps or '').split(',')
                         if x.strip()]
        # Categorias: la misma resolucion que foco.usage (sitio y luego app; el
        # perfil del equipo gana sobre lo global).
        hosts, exes = set(), set()
        for p in pausas:
            if p.host:
                hosts.add(p.host)
            if p.app:
                exes.add(p.app)
            exes.update((p._evid().get('aud') or {}).keys())
        ctx['sitios'] = {s.host: s for s in self.env['foco.site'].sudo().search(
            [('host', 'in', list(hosts))])} if hosts else {}
        ctx['apps'] = {a.exe: a for a in self.env['foco.app'].sudo().search(
            [('exe', 'in', list(exes))])} if exes else {}
        try:
            default_pol = settings.default_policy_id.filtered('active')
        except Exception:
            default_pol = self.env['foco.policy']
        ctx['perfil'] = {c.id: (c.policy_id.filtered('active') or default_pol)[:1].id for c in comps}
        por_sitio, por_app = {}, {}
        pols = [x for x in set(ctx['perfil'].values()) if x]
        if pols:
            for o in self.env['foco.policy.category'].sudo().search([('policy_id', 'in', pols)]):
                if o.site_id:
                    por_sitio[(o.policy_id.id, o.site_id.id)] = o.category_id
                elif o.app_id:
                    por_app[(o.policy_id.id, o.app_id.id)] = o.category_id
        ctx['por_sitio'], ctx['por_app'] = por_sitio, por_app
        return ctx

    @api.model
    def _categoria(self, ctx, computer_id, host, exe):
        pid = ctx['perfil'].get(computer_id)
        s = ctx['sitios'].get(host) if host else None
        a = ctx['apps'].get(exe) if exe else None
        if s and pid and ctx['por_sitio'].get((pid, s.id)):
            return ctx['por_sitio'][(pid, s.id)]
        if s and s.category_id:
            return s.category_id
        if a and pid and ctx['por_app'].get((pid, a.id)):
            return ctx['por_app'][(pid, a.id)]
        if a and a.category_id:
            return a.category_id
        return None

    def _leer(self, ctx, tz):
        """(motivo, detalle) de ESTA pausa, solo con lo que se midio en ella."""
        self.ensure_one()
        p = self

        def nombre(exe):
            return (ctx['apps'][exe].name if exe in ctx['apps'] else exe) or ''

        def hm(dt):
            return pytz.UTC.localize(dt).astimezone(tz).strftime('%H:%M')

        frente = p.host or nombre(p.app)
        if p.kind == 'locked':
            return 'bloqueo', ''
        if p.kind == 'offline':
            margen = timedelta(seconds=90)
            for e in ctx['eventos'].get(p.computer_id.id, []):
                if p.start - margen <= e.at <= p.stop + margen:
                    return 'apagado', '%s a las %s' % (ctx['etq_evento'].get(e.kind, e.kind), hm(e.at))
            return 'sin_agente', ''
        ev = p._evid()
        if not ev:
            return 'sin_medir', ('%s al frente' % frente) if frente else ''
        n = int(ev.get('aud_n') or 0)
        aud = ev.get('aud') or {}
        suena = max(aud.items(), key=lambda x: x[1]) if aud else None
        musica = False
        if suena:
            exe_s, k = suena
            host_s = p.host if exe_s == (p.app or '').lower() else ''
            musica = any(m in (t or '').lower() for m in ctx['musica']
                         for t in (exe_s, host_s, nombre(exe_s)))
            if k >= FRACCION * n and not musica:
                cat = self._categoria(ctx, p.computer_id.id, host_s, exe_s)
                if _distraccion(cat):
                    return 'sonido', '%s sonó en %d de %d muestras (%s)' % (
                        host_s or nombre(exe_s), k, n, cat.name)
        difs = [d for d in (ev.get('pant') or []) if isinstance(d, int)]
        mov = sum(1 for d in difs if d >= PANT_MOV_BYTES)
        se_movio = bool(difs) and mov >= FRACCION * len(difs)
        if se_movio:
            cat = self._categoria(ctx, p.computer_id.id, p.host, p.app)
            if _distraccion(cat):
                return 'video', '%s al frente, se movió en %d de %d muestras (%s)' % (
                    frente, mov, len(difs), cat.name)
        # Sin prueba: se dice todo lo que si se sabe.
        partes = [('%s al frente' % frente) if frente else '']
        if difs:
            partes.append('la pantalla cambió en %d de %d muestras' % (mov, len(difs)))
        if suena:
            partes.append('sonó %s%s' % (nombre(suena[0]), ' (música)' if musica else ''))
        elif n:
            partes.append('sin sonido')
        detalle = ', '.join(x for x in partes if x)
        if not difs:
            return 'corta', detalle
        return ('movimiento' if se_movio else 'quieta'), detalle

    # ------------------------------------------------------------- un dia
    @api.model
    def _dia(self, emp, d, ctx, ahora):
        """La linea del dia de una persona y cada minuto del gris con su caja.

        La usan la pagina y el tablero: los dos leen el mismo minuto igual.
        """
        Settings = self.env['foco.settings'].sudo()
        tz = Settings._tzinfo_for(emp)
        base = tz.localize(datetime.combine(d, time(0, 0)))

        def u(dt_local):
            return dt_local.astimezone(pytz.UTC).replace(tzinfo=None)

        ini_dia, fin_dia = u(base), u(base + timedelta(days=1))
        es_hoy = ini_dia <= ahora < fin_dia
        corte = min(fin_dia, ahora)
        comps = ctx['comps'].get(emp.id) or self.env['foco.computer']
        vistos = [c.last_seen for c in comps if c.last_seen]
        datos_hasta = min(corte, max(vistos)) if (es_hoy and vistos) else corte

        def hl(dt):          # UTC naive -> hora local decimal del dia
            loc = pytz.UTC.localize(dt).astimezone(tz)
            dias = (loc.date() - d).days
            return round(max(0.0, min(24.0, dias * 24 + loc.hour + loc.minute / 60.0
                                      + loc.second / 3600.0)), 4)

        def hms(dt):
            return pytz.UTC.localize(dt).astimezone(tz).strftime('%H:%M:%S') if dt else None

        def hm(dt):
            return pytz.UTC.localize(dt).astimezone(tz).strftime('%H:%M') if dt else ''

        cal = emp.resource_calendar_id
        if cal.id not in ctx['cal']:
            ctx['cal'][cal.id] = (Settings.calendar_intervals(cal), Settings.break_intervals(cal))
        cal_int, cal_comida = ctx['cal'][cal.id]
        iso = d.isoweekday()

        def hora(h):
            return u(base + timedelta(hours=h))

        jornada_dia = [(hora(a), hora(b)) for a, b in cal_int.get(iso, [])]
        J = _corta(jornada_dia, [(ini_dia, corte)])
        comida = [(hora(a), hora(b)) for a, b in cal_comida.get(iso, [])]

        huecos = [h for h in ctx['huecos'].get(emp.id, []) if h.start < fin_dia and h.stop > ini_dia]
        G = [(h.start, h.stop) for h in huecos]
        acts = ctx['acts'].get((emp.id, d), [])
        primera = min([a.primera for a in acts if a.primera] or [None]) if acts else None
        ultima = max([a.ultima for a in acts if a.ultima] or [None]) if acts else None
        desde = min([a.desde for a in acts if a.desde] or [None]) if acts else None
        pausas = ctx['pausas'].get((emp.id, d), [])
        medido = bool(acts or pausas)

        # Lo checado ese dia. Solo prueba algo el dia que SI checo: "no checar
        # no prueba que no estuviera" (misma regla que expected_seconds).
        asist = [a for a in ctx['asist'].get(emp.id, [])
                 if a.check_in < fin_dia and (not a.check_out or a.check_out > ini_dia)]
        checado = sorted((max(a.check_in, ini_dia),
                          min(a.check_out or (ahora if es_hoy else fin_dia), fin_dia))
                         for a in asist)
        checado = [(x, y) for x, y in checado if y > x]
        entro = min([a.check_in for a in asist], default=None)
        salio = max(a.check_out for a in asist) if asist and all(a.check_out for a in asist) else None

        tramos = []           # para dibujar
        piezas = {'antes': 0.0, 'idle': 0.0, 'locked': 0.0, 'offline': 0.0,
                  'despues': 0.0, 'sin_datos': 0.0, 'no_medido': 0.0}
        segs = []             # cada minuto del gris con su caja

        def poner(tipo, intervalos, cuenta=True, **extra):
            for a, b in intervalos:
                tramos.append(dict(tipo=tipo, a=hl(a), b=hl(b), ini=hms(a), fin=hms(b),
                                   min=round((b - a).total_seconds() / 60.0, 2), **extra))
                if cuenta and tipo in piezas:
                    piezas[tipo] += (b - a).total_seconds() / 3600.0

        def agrega(tipo, intervalos, motivo, detalle='', pausa_id=None):
            """Los minutos del gris de una pieza. Lo que cae fuera de lo checado
            se prueba con el checador antes que con cualquier otra cosa."""
            for a, b in intervalos:
                for x, y in (_quita([(a, b)], checado) if checado else []):
                    if entro and y <= entro:
                        m, det = 'checador_antes', 'checó entrada a las %s' % hm(entro)
                    elif salio and x >= salio:
                        m, det = 'checador_despues', 'checó salida a las %s' % hm(salio)
                    else:
                        m, det = 'checador_fuera', ''
                    segs.append(dict(tipo=tipo, a=x, b=y, motivo=m, detalle=det, pausa_id=pausa_id))
                for x, y in (_corta([(a, b)], checado) if checado else [(a, b)]):
                    segs.append(dict(tipo=tipo, a=x, b=y, motivo=motivo, detalle=detalle,
                                     pausa_id=pausa_id))

        # Huecos de 15 min o mas: su propio tramo en el tablero. Aqui solo de
        # referencia, para que la linea del dia no tenga agujeros.
        for h in huecos:
            poner('hueco', _corta([(h.start, h.stop)], [(ini_dia, corte)]), cuenta=False,
                  estado=h.state, estado_label=ctx['etq_estado'].get(h.state, h.state))

        P_full = [(p.start, p.stop) for p in pausas]
        lista_pausas = []
        for p in pausas:
            inact = _quita(_corta([(p.inactivo, p.stop)], J), G)
            motivo, detalle = p._leer(ctx, tz)
            poner(p.kind, _quita(_corta([(p.start, p.stop)], [(ini_dia, corte)]), G), cuenta=False,
                  pausa_id=p.id, motivo=motivo)
            piezas[p.kind] += _seg(inact) / 3600.0
            antes_n = len(segs)
            agrega(p.kind, inact, motivo, detalle, p.id)
            fuera_chk = sum((s['b'] - s['a']).total_seconds() for s in segs[antes_n:]
                            if s['motivo'].startswith('checador'))
            app = ctx['apps'].get(p.app)
            lista_pausas.append({
                'id': p.id, 'kind': p.kind, 'kind_label': dict(KINDS_PAUSA)[p.kind],
                'ini': hms(p.start), 'inactiva': hms(p.inactivo), 'fin': hms(p.stop),
                'dur_min': round(p.seconds / 60.0, 1),
                'cuenta_min': round(_seg(inact) / 60.0, 1),
                'dentro': bool(inact),
                'app': (app.name if app else p.app) or '',
                'host': p.host or '',
                'motivo': motivo, 'caja': MOTIVOS[motivo][0],
                'motivo_label': MOTIVOS[motivo][1], 'detalle': detalle,
                # Parte de esta pausa que el checador prueba fuera.
                'fuera_chk_min': round(fuera_chk / 60.0, 1),
            })

        if medido:
            # Lo no medido: la parte del dia antes de que este agente midiera.
            # Se DIBUJA, pero no entra a ninguna caja: es una ventana sin
            # registro, no tiempo inactivo.
            if desde and desde > ini_dia:
                poner('no_medido', _quita(_corta([(ini_dia, desde)], J), G))
            if primera:
                iv = _quita(_corta([(max(ini_dia, desde or ini_dia), primera)], J), G + P_full)
                poner('antes', iv)
                agrega('antes', iv, 'antes', 'primera actividad a las %s' % hm(primera))
                # Con actividad: entre la primera y la ultima, todo lo que no es
                # pausa ni hueco. Es un HECHO, no un supuesto: el agente anota
                # TODA inactividad de 1 min o mas, asi que lo que queda tuvo
                # input (o llamada) cada menos de un minuto.
                poner('activo', _quita(_corta([(primera, ultima)], [(ini_dia, corte)]),
                                       G + P_full), cuenta=False)
                iv = _quita(_corta([(ultima, datos_hasta)], J), G + P_full)
                poner('despues', iv)
                agrega('despues', iv, 'despues', 'última actividad a las %s' % hm(ultima))
            else:
                poner('no_medido', _quita(_corta([(desde or ini_dia, datos_hasta)], J), G + P_full))
            if es_hoy and datos_hasta < corte:
                poner('sin_datos', _corta([(datos_hasta, corte)], J))
        else:
            poner('no_medido', _quita(J, G))

        cajas = {k: 0.0 for k in CAJAS}
        for s in segs:
            cajas[MOTIVOS[s['motivo']][0]] += (s['b'] - s['a']).total_seconds() / 3600.0

        return {
            'tz': tz, 'hl': hl, 'hms': hms, 'hm': hm,
            'ini_dia': ini_dia, 'fin_dia': fin_dia, 'es_hoy': es_hoy, 'corte': corte,
            'datos_hasta': datos_hasta, 'jornada_dia': jornada_dia, 'J': J, 'comida': comida,
            'huecos': huecos, 'G': G, 'pausas': pausas, 'medido': medido,
            'primera': primera, 'ultima': ultima, 'desde': desde,
            'entro': entro, 'salio': salio, 'usa_checador': bool(checado),
            'tramos': tramos, 'piezas': piezas, 'segs': segs, 'cajas': cajas,
            'lista_pausas': lista_pausas,
        }

    @api.model
    def _cajas_rango(self, employees, d_ini, d_fin):
        """{employee_id: {'fuera': h, 'presente': h}} del rango: las horas del
        gris que tienen PRUEBA. El resto del gris es "sin prueba" y el tablero
        lo obtiene restando. Solo los dias medidos: de un dia sin pausas no se
        puede partir nada."""
        if not employees:
            return {}
        ctx = self._contexto(employees, d_ini, d_fin)
        ahora = fields.Datetime.now()
        dias = sorted(set(ctx['pausas']) | set(ctx['acts']))
        por_id = {e.id: e for e in employees}
        out = {}
        for eid, d in dias:
            emp = por_id.get(eid)
            if not emp:
                continue
            r = self._dia(emp, d, ctx, ahora)
            o = out.setdefault(eid, {'fuera': 0.0, 'presente': 0.0})
            o['fuera'] += r['cajas']['fuera']
            o['presente'] += r['cajas']['presente']
        return out

    # ------------------------------------------------------------ la pagina
    @api.model
    def explicar(self, employee_id, fecha):
        """El tramo "sin actividad" del tablero, explicado SOLO con lo medido.

        Devuelve la linea del dia de la persona -con actividad, cada pausa con su
        hora, los huecos de 15 min o mas, antes de su primera actividad y despues
        de la ultima-, la cuenta del tablero tal cual, cuanto de esa cuenta
        quedo medido, y cada minuto en su caja (probado fuera / sin prueba) con
        la senal que lo pone ahi. Lo que no se midio se dice: nunca se rellena.
        """
        # Con los permisos de quien pregunta: si no ve el equipo de esa persona,
        # no ve su dia.
        comps = self.env['foco.computer'].search([('employee_id', '=', int(employee_id))])
        if not comps:
            return {'ok': False, 'error': 'sin_acceso'}
        emp = comps[0].employee_id
        d = fields.Date.to_date(fecha)
        ctx = self._contexto(emp, d, d)
        r = self._dia(emp, d, ctx, fields.Datetime.now())
        hl, hms, hm = r['hl'], r['hms'], r['hm']
        J, G, huecos, piezas, tramos = r['J'], r['G'], r['huecos'], r['piezas'], r['tramos']

        # La cuenta del tablero, con la MISMA funcion: el numero que se clico.
        res = self.env['foco.absence'].dashboard_summary(str(d), str(d)).get(str(emp.id), {})
        huecos_h = sum(res.get(k, 0.0) for k in ('justified', 'aprobado_h', 'vacaciones_h',
                                                 'por_decidir_h', 'unexplained_h', 'rechazado_h'))
        resta = {'jornada_h': res.get('jornada_h', _seg(J) / 3600.0),
                 'activo_h': res.get('activo_jornada_h', 0.0),
                 'huecos_h': huecos_h,
                 'sin_actividad_h': res.get('sin_actividad_h', 0.0)}

        # La cuenta: lo medido contra la resta del tablero. El tablero cuenta el
        # primer minuto de cada hueco DOS veces (como activo y dentro del hueco):
        # es la misma forma de medir del agente. Se calcula, no se supone.
        pausas = r['pausas']
        umbrales = sorted((p.inactivo - p.start).total_seconds() for p in pausas if p.kind == 'idle')
        umbral = umbrales[len(umbrales) // 2] if umbrales else 60.0
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

        # Cada motivo con sus minutos, para la tabla "de que esta hecho".
        lecturas = {}
        for s in r['segs']:
            caja, label = MOTIVOS[s['motivo']]
            g = lecturas.setdefault(s['motivo'], {
                'motivo': s['motivo'], 'caja': caja, 'label': label, 'h': 0.0, 'n': 0,
                'rangos': [], 'detalles': [], '_p': set()})
            g['h'] += (s['b'] - s['a']).total_seconds() / 3600.0
            g['rangos'].append('%s–%s' % (hm(s['a']), hm(s['b'])))
            clave = s['pausa_id'] or (s['a'], s['tipo'])
            if clave not in g['_p']:
                g['_p'].add(clave)
                g['n'] += 1
            if s['detalle'] and s['detalle'] not in g['detalles']:
                g['detalles'].append(s['detalle'])
        orden = list(MOTIVOS)
        lista_lect = []
        for g in sorted(lecturas.values(), key=lambda x: (list(CAJAS).index(x['caja']),
                                                          orden.index(x['motivo']))):
            g.pop('_p')
            g['h'] = round(g['h'], 4)
            g['detalles'] = g['detalles'][:4]
            g['rangos'] = g['rangos'][:6] + (['…'] if len(g['rangos']) > 6 else [])
            lista_lect.append(g)
        carril = [{'a': hl(s['a']), 'b': hl(s['b']), 'caja': MOTIVOS[s['motivo']][0],
                   'titulo': '%s · %s–%s%s' % (MOTIVOS[s['motivo']][1], hm(s['a']), hm(s['b']),
                                               (' · ' + s['detalle']) if s['detalle'] else '')}
                  for s in r['segs']]

        # Ventana para dibujar: la jornada, ampliada a lo medido, con aire.
        horas = [x for t in tramos for x in (t['a'], t['b'])] + \
                [x for a, b in r['jornada_dia'] for x in (hl(a), hl(b))]
        lo = max(0.0, (min(horas) if horas else 8.0) - 0.25)
        hi = min(24.0, (max(horas) if horas else 18.0) + 0.25)

        eventos = []
        if not r['medido']:
            etq = dict(self.env['foco.event']._fields['kind'].selection)
            for e in self.env['foco.event'].sudo().search(
                    [('employee_id', '=', emp.id), ('at', '>=', r['ini_dia']), ('at', '<', r['fin_dia'])],
                    order='at'):
                eventos.append({'h': hms(e.at), 'label': etq.get(e.kind, e.kind)})

        desde, primera, ultima = r['desde'], r['primera'], r['ultima']
        return {
            'ok': True, 'empleado': {'id': emp.id, 'name': emp.name},
            'fecha': str(d), 'es_hoy': r['es_hoy'],
            'medido': r['medido'], 'desde': hms(desde) if desde and desde > r['ini_dia'] else None,
            'datos_hasta': hms(r['datos_hasta']) if r['es_hoy'] else None,
            'primera': hms(primera), 'ultima': hms(ultima),
            'primera_h': hl(primera) if primera else None,
            'ultima_h': hl(ultima) if ultima else None,
            'checador': {'usa': r['usa_checador'], 'entro': hm(r['entro']), 'salio': hm(r['salio'])},
            'ventana': [round(lo, 2), round(hi, 2)],
            'jornada': [[hl(a), hl(b)] for a, b in r['jornada_dia']],
            'comida': [[hl(a), hl(b)] for a, b in r['comida']],
            'resta': {k: round(v, 4) for k, v in resta.items()},
            'piezas': {k: round(v, 4) for k, v in piezas.items()},
            'n': {k: sum(1 for p in pausas if p.kind == k) for k in dict(KINDS_PAUSA)},
            'cuadre': cuadre,
            'cajas': {k: round(v, 4) for k, v in r['cajas'].items()},
            'cajas_label': CAJAS,
            'lecturas': lista_lect,
            'carril': carril,
            'regla': REGLA,
            'tramos': sorted(tramos, key=lambda t: (t['a'], t['b'])),
            'pausas': r['lista_pausas'],
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
