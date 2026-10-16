import json
import logging
import re
from datetime import datetime, time, timedelta

import pytz
from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools import formataddr

from .foco_workday import ADHERENCIA_ETQ, ADH_AUSENCIA

_logger = logging.getLogger(__name__)

# Hechos de integridad que SI van a "requiere atencion". Los demas (activo sin
# teclear, pantalla sin cambio, justificado como "Otro"...) le salen a casi
# todo el mundo casi todos los dias -medido: 14 de 16 personas el 9-oct- y en
# la lista de atencion serian ruido; siguen en la fila de cada persona.
HECHOS_FUERTES = ('sintetico_sin_input', 'agente_reinicio', 'dispositivo_nuevo',
                  'justificacion_repetida', 'navegador_cerrado', 'navegador_desconocido',
                  'sin_senal_tras_checar', 'admin_local')

DIAS_ES = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
MESES_ES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
            'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def _hm(horas):
    """6.53 -> '6h 32m'; 0.4 -> '24m'."""
    m = int(round((horas or 0.0) * 60))
    if m >= 60:
        return '%dh %02dm' % (m // 60, m % 60)
    return '%dm' % m


def _hhmm(h):
    """8.5 -> '08:30'."""
    m = int(round(float(h or 0.0) * 60))
    return '%02d:%02d' % (min(m // 60, 24), m % 60)


def _fecha_es(d):
    return '%s %d de %s' % (DIAS_ES[d.weekday()], d.day, MESES_ES[d.month - 1])


DIAS_CORTOS = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']

# Los tramos de la barra "Como viene cada quien", en el orden y con los tonos
# del tablero (foco_dashboard.scss). En LISO: el correo no dibuja degradados
# (Outlook usa el motor de Word), asi que lo que en el tablero va rayado o
# punteado aqui es un tono mas claro del mismo color.
TRAMOS = {
    'productiva': ('Productivo', '#16a34a'),
    'aprobado': ('Aprobado por ti (cuenta como productivo)', '#5dbb82'),
    'otro': ('Neutral y navegador', '#64748b'),
    'distraccion': ('Distracción', '#dc3545'),
    'sin': ('Sin clasificar', '#cbd3df'),
    'justificado': ('Justificado (de su bolsa de %d min)', '#9ad3b1'),
    'vacaciones': ('Vacaciones o permiso', '#0d9488'),
    'decidir': ('Por decidir, espera tu veredicto', '#e8a04a'),
    'sinexp': ('Sin justificar', '#e87272'),
    'rechazado': ('Rechazado por ti', '#881337'),
    'sinact_fuera': ('Sin actividad, probado fuera del equipo', '#4a515d'),
    'sinact_presente': ('Sin actividad, probado en el equipo', '#8cc7a5'),
    'sinact': ('Sin actividad, sin prueba', '#d9dde4'),
    'comida': ('Comida (fuera de la jornada)', '#4bbbef'),
}
# Lo que queda antes de la meta sin trabajo medido, y despues de lo dibujado.
HUECO, RESTO = '#eef1f6', '#ffffff'
# Debajo de esto un tramo no se dibuja: en un correo una celda de 1 px se
# pierde o, peor, Outlook la ensancha y empuja la marca de la meta.
TRAMO_MIN_PCT = 0.4


def _nombre_corto(nombre):
    """Nombre y primer apellido, para la columna angosta de la grafica:
    'Jose Maximiliano Alvarado Guevara' -> 'Jose Alvarado'. Con cuatro palabras
    o mas, el primer apellido es la penultima; con tres, la segunda. Los que
    vienen en MAYUSCULAS en RRHH se escriben normal."""
    partes = (nombre or '').split()
    if len(partes) >= 4:
        partes = [partes[0], partes[-2]]
    elif len(partes) == 3:
        partes = partes[:2]
    corto = ' '.join(partes)
    return corto.title() if corto.isupper() else corto


def _color_indice(i):
    """El mismo semaforo de las tarjetas: verde, ambar o rojo."""
    if i is None:
        return '#c4cbd6'
    return '#15803d' if i >= 70 else ('#b45309' if i >= 40 else '#c0341d')


class FocoDailyReport(models.Model):
    """El reporte del dia que recibe quien revisa la operacion a las 6 PM.

    Es el MISMO dato del tablero, armado una vez al dia y guardado: los
    numeros del correo son los de la pantalla a esa hora, y quedan fijos para
    poder volver a ellos. Cada etiqueta lleva su evidencia (hora, minutos,
    fuente) y lo que no se sabe se dice como "sin dato", nunca como cero.
    """

    _name = 'foco.daily.report'
    _description = 'Reporte diario de Foco'
    _order = 'date desc'
    _rec_name = 'name'

    _uniq = models.Constraint('unique(date)', 'Ya existe el reporte de ese dia.')

    date = fields.Date(string='Dia', required=True, index=True)
    name = fields.Char(string='Nombre', compute='_compute_name', store=True)
    resumen = fields.Char(string='Resumen', readonly=True)
    html = fields.Html(string='Reporte', readonly=True, sanitize=False)
    datos = fields.Text(string='Datos (JSON)', readonly=True)
    generated_at = fields.Datetime(string='Generado', readonly=True)
    corte = fields.Char(string='Corte', readonly=True,
                        help='Hora local a la que se tomaron los numeros.')
    state = fields.Selection(
        [('borrador', 'Generado'), ('enviado', 'Enviado')],
        string='Estado', default='borrador', readonly=True)
    sent_at = fields.Datetime(string='Enviado el', readonly=True)
    sent_to = fields.Char(string='Enviado a', readonly=True)

    @api.depends('date')
    def _compute_name(self):
        for r in self:
            r.name = ('Reporte del %s' % _fecha_es(r.date)) if r.date else 'Reporte'

    # ------------------------------------------------------------ zona/hora
    @api.model
    def _zona(self):
        nombre = (self.env.company.resource_calendar_id.tz
                  or self.env.company.partner_id.tz or 'America/Mazatlan')
        try:
            return pytz.timezone(nombre)
        except Exception:
            return pytz.timezone('America/Mazatlan')

    # ------------------------------------------------------------ los datos
    @api.model
    def armar_datos(self, dia):
        """El reporte como dict (serializable). Publico a proposito: desde
        fuera se puede pedir y contrastar contra el tablero."""
        dia = fields.Date.to_date(dia)
        return self._armar(dia)

    @api.model
    def _numeros_dia(self, dia, empleados, categorias):
        """Uso del dia por persona y del equipo, con la misma aritmetica que
        el tablero (foco_dashboard.js): indice = productivo / META (7 h
        configurables), que PUEDE pasar del 100%; lo justificado NO entra. Sin
        meta (dia de descanso), productivo / activo. Cambio del 12-oct-2026."""
        Usage = self.env['foco.usage'].sudo()
        usos = Usage.search_read(
            [('date', '=', dia), ('employee_id', 'in', empleados.ids)],
            ['employee_id', 'app_id', 'site_id', 'category_id', 'fg_active', 'fg_idle',
             'active_hours', 'productive_hours', 'call_hours', 'injected_hours'])
        resumen = self.env['foco.absence'].sudo().dashboard_summary(dia, dia) or {}
        por = {}
        for emp in empleados:
            por[emp.id] = {'activo': 0.0, 'prod': 0.0, 'distr': 0.0, 'idle': 0.0,
                           'llamada': 0.0, 'inyectado': 0.0, 'sin_clasificar': 0.0,
                           'apps': {}, 'distrs': {}, 'con_dato': False}
        distr_equipo = {}
        for r in usos:
            eid = r['employee_id'][0] if r['employee_id'] else 0
            e = por.get(eid)
            if e is None:
                continue
            e['con_dato'] = True
            cat = categorias.get(r['category_id'][0]) if r['category_id'] else None
            es_distr = bool(cat) and not cat['is_system'] and (cat['weight'] or 0.0) <= 0.0
            e['activo'] += r['active_hours'] or 0.0
            e['prod'] += r['productive_hours'] or 0.0
            e['idle'] += r['fg_idle'] or 0.0
            e['llamada'] += r['call_hours'] or 0.0
            e['inyectado'] += r['injected_hours'] or 0.0
            if not cat:
                e['sin_clasificar'] += r['active_hours'] or 0.0
            sitio = r['site_id'][1] if r['site_id'] else ''
            app = r['app_id'][1] if r['app_id'] else ''
            if es_distr:
                e['distr'] += r['active_hours'] or 0.0
                nombre = sitio or app or '?'
                e['distrs'][nombre] = e['distrs'].get(nombre, 0.0) + (r['fg_active'] or 0.0)
                d = distr_equipo.setdefault(nombre, {'nombre': nombre, 'horas': 0.0, 'personas': set()})
                d['horas'] += r['fg_active'] or 0.0
                d['personas'].add(eid)
            if app:
                e['apps'][app] = e['apps'].get(app, 0.0) + (r['fg_active'] or 0.0)
        meta_h = self.env['foco.settings'].sudo().get_settings().meta_productiva_h or 7.0
        equipo = {'cubierto': 0.0, 'esperado': 0.0, 'prod': 0.0, 'activo': 0.0,
                  'distr': 0.0, 'justificado': 0.0, 'sin_explicar': 0.0, 'con_dato': 0,
                  'idle': 0.0, 'meta': 0.0, 'aprobado': 0.0, 'sin_clasificar': 0.0}
        for emp in empleados:
            e = por[emp.id]
            res = resumen.get(str(emp.id)) or {}
            e['esperado'] = res.get('expected') or 0.0
            e['justificado'] = res.get('justified') or 0.0
            e['sin_explicar'] = res.get('unexplained_h') or 0.0
            e['pendientes'] = res.get('pending') or 0
            e['lag_min'] = res.get('lag_min')
            e['sin_salida'] = res.get('sin_salida') or 0
            e['usa_checador'] = bool(res.get('usa_checador'))
            e['checado'] = res.get('checado_h') or 0.0
            e['extra'] = res.get('extra_h') or 0.0
            e['faltante'] = res.get('faltante_h') or 0.0
            # Un dia sin checar salida no se mide (10-oct): se dice, no se
            # deja un cero que se lee como "no checo".
            e['checado_no_medible'] = res.get('checado_no_medible') or 0
            # Pausas sin teclear dentro de la jornada, una sola linea de tiempo;
            # el `fg_idle` sumado por renglon se encima entre ventanas.
            e['idle'] = res.get('sin_teclear_h') or 0.0
            # Lo que el admin acepto pasada la bolsa cuenta como PRODUCTIVO
            # (12-oct-2026): suma al indice igual que el tiempo medido.
            e['aprobado'] = res.get('aprobado_h') or 0.0
            # El desglose completo de la jornada, el mismo que dibuja la barra de
            # "Como viene cada quien" del tablero (29.9.0): lo arma el servidor
            # en dashboard_summary y aqui solo se reparte.
            e['por_decidir'] = res.get('por_decidir_h') or 0.0
            e['vacaciones'] = res.get('vacaciones_h') or 0.0
            e['rechazado'] = res.get('rechazado_h') or 0.0
            e['sin_actividad'] = res.get('sin_actividad_h') or 0.0
            e['sinact_fuera'] = res.get('sinact_fuera_h') or 0.0
            e['sinact_presente'] = res.get('sinact_presente_h') or 0.0
            e['comida'] = res.get('comida_h') or 0.0
            e['jornada'] = res.get('jornada_h') or 0.0
            e['cubierto'] = e['prod'] + e['justificado'] + e['aprobado']
            # La meta de ESTA persona ese dia: cuenta solo si tenia jornada.
            e['meta'] = meta_h if e['esperado'] > 0 else 0.0
            if e['meta'] > 0:
                e['indice'] = int(round((e['prod'] + e['aprobado']) / e['meta'] * 100))
            elif e['activo'] > 0:
                e['indice'] = int(round(e['prod'] / e['activo'] * 100))
            else:
                e['indice'] = 0
            equipo['meta'] += e['meta']
            equipo['aprobado'] += e['aprobado']
            equipo['cubierto'] += e['cubierto']
            equipo['esperado'] += e['esperado']
            equipo['prod'] += e['prod']
            equipo['activo'] += e['activo']
            equipo['distr'] += e['distr']
            equipo['sin_clasificar'] += e['sin_clasificar']
            equipo['idle'] += e['idle']
            equipo['justificado'] += e['justificado']
            equipo['sin_explicar'] += e['sin_explicar']
            equipo['con_dato'] += 1 if e['con_dato'] else 0
        if equipo['meta'] > 0:
            equipo['indice'] = int(round((equipo['prod'] + equipo['aprobado'])
                                         / equipo['meta'] * 100))
        elif equipo['activo'] > 0:
            equipo['indice'] = int(round(equipo['prod'] / equipo['activo'] * 100))
        else:
            equipo['indice'] = 0
        equipo['distr_pct'] = int(round(equipo['distr'] / equipo['activo'] * 100)) if equipo['activo'] else 0
        distracciones = sorted(distr_equipo.values(), key=lambda d: -d['horas'])
        return por, equipo, [{'nombre': d['nombre'], 'horas': round(d['horas'], 3),
                              'personas': len(d['personas'])} for d in distracciones[:6]]

    # ------------------------------------------------------------ graficas
    @api.model
    def _barras_jornada(self, filas, meta_h, bolsa_h):
        """La barra de "Como viene cada quien" del tablero, persona por persona
        (foco_dashboard.js, paso 4). Dos zonas separadas por la META:
        a la izquierda lo medido -productivo, aprobado, neutral, distraccion,
        sin clasificar- recortado a la meta; a la derecha todo lo que NO suma
        -justificado, vacaciones, por decidir, sin justificar, rechazado, sin
        actividad, comida-. UNA escala para todas las filas (la mas larga
        manda), para que la linea de la meta caiga en el mismo sitio en cada
        renglon. Anchos en % listos para celdas de tabla."""
        personas = [f for f in filas if f['con_dato'] and f['esperado'] > 0]

        def zona2(f):
            sinact = max(0.0, f['sin_actividad'] - f['sinact_fuera'] - f['sinact_presente'])
            return [('justificado', f['justificado']), ('vacaciones', f['vacaciones']),
                    ('decidir', f['por_decidir']), ('sinexp', f['sin_explicar']),
                    ('rechazado', f['rechazado']), ('sinact_fuera', f['sinact_fuera']),
                    ('sinact_presente', f['sinact_presente']), ('sinact', sinact),
                    ('comida', f['comida'])]

        escala = max([0.01] + [meta_h + max(bolsa_h - f['justificado'], 0.0)
                               + sum(max(0.0, v) for _, v in zona2(f)) for f in personas])
        meta_pct = round(meta_h / escala * 100, 2)
        bolsa_min = int(round(bolsa_h * 60))

        def etiqueta(k):
            e = TRAMOS[k][0]
            return e % bolsa_min if '%d' in e else e

        usados, barras = set(), []
        for f in sorted(personas, key=lambda f: (-f['indice'], f['nombre'])):
            segs, x = [], 0.0
            otro = max(f['activo'] - f['prod'] - f['distr'] - f['sin_clasificar'], 0.0)
            queda = meta_h
            for k, v in (('productiva', f['prod']), ('aprobado', f['aprobado']), ('otro', otro),
                         ('distraccion', f['distr']), ('sin', f['sin_clasificar'])):
                h = min(max(v, 0.0), queda)
                queda -= h
                w = round(h / escala * 100, 2)
                if w >= TRAMO_MIN_PCT:
                    segs.append({'k': k, 'w': w, 'c': TRAMOS[k][1], 't': '%s %s' % (etiqueta(k), _hm(v))})
                    usados.add(k)
                    x += w
            if meta_pct - x >= TRAMO_MIN_PCT:
                segs.append({'k': 'hueco', 'w': round(meta_pct - x, 2), 'c': HUECO, 't': ''})
            x = meta_pct
            antes_de_meta = len(segs)
            for k, v in zona2(f):
                w = round(max(v, 0.0) / escala * 100, 2)
                if w >= TRAMO_MIN_PCT:
                    segs.append({'k': k, 'w': w, 'c': TRAMOS[k][1], 't': '%s %s' % (etiqueta(k), _hm(v))})
                    usados.add(k)
                    x += w
            if 100 - x >= TRAMO_MIN_PCT:
                segs.append({'k': 'resto', 'w': round(100 - x, 2), 'c': RESTO, 't': ''})
            sobra = round(sum(s['w'] for s in segs) - 100, 2)
            if sobra > 0 and segs:
                segs[-1]['w'] = round(max(0.0, segs[-1]['w'] - sobra), 2)
            # La linea de la meta: el borde izquierdo del primer tramo de la
            # zona 2 (o el derecho del ultimo, si la meta cae al final).
            if antes_de_meta < len(segs):
                segs[antes_de_meta]['marca'] = 'izq'
            elif segs:
                segs[-1]['marca'] = 'der'
            barras.append({
                'nombre': f['nombre'], 'corto': _nombre_corto(f['nombre']),
                'indice': f['indice'], 'color': _color_indice(f['indice']),
                'prod': round(f['prod'] + f['aprobado'], 3), 'segs': segs,
                'justificado': round(f['justificado'], 3), 'sin_explicar': round(f['sin_explicar'], 3),
            })
        orden = ['productiva', 'aprobado', 'otro', 'distraccion', 'sin', 'justificado', 'vacaciones',
                 'decidir', 'sinexp', 'rechazado', 'sinact_fuera', 'sinact_presente', 'sinact', 'comida']
        return {
            'barras': barras, 'meta_pct': meta_pct, 'meta_txt': _hm(meta_h),
            'bolsa_txt': '%d min' % bolsa_min,
            'leyenda': [{'t': etiqueta(k), 'c': TRAMOS[k][1]} for k in orden if k in usados],
        }

    @api.model
    def _serie_indice(self, dia, n=10):
        """El indice del equipo en los ultimos `n` dias CON META (laborables),
        con la formula unica de Foco (`foco.usage._indice`): la misma de la
        chispa del tablero, pero sin los fines de semana, que con dos horas de
        alguien que entro a revisar el correo darian puntos que no dicen nada."""
        desde = dia - timedelta(days=max(21, n * 2))
        Usage = self.env['foco.usage'].sudo()
        por_dia = {}
        for d, activo, productivo in Usage._read_group(
                [('date', '>=', desde), ('date', '<=', dia)],
                ['date:day'], ['active_hours:sum', 'productive_hours:sum']):
            por_dia[d] = (activo or 0.0, productivo or 0.0)
        cub = self.env['foco.absence'].sudo().cubierto_por_dia(desde, dia).get('dias') or {}
        puntos, d = [], desde
        while d <= dia:
            k = fields.Date.to_string(d)
            c = cub.get(k) or {}
            if (c.get('meta') or 0.0) > 0:
                activo, productivo = por_dia.get(d, (0.0, 0.0))
                idx = Usage._indice(productivo, c, activo)
                puntos.append({'fecha': k, 'dia': DIAS_CORTOS[d.weekday()], 'num': d.day,
                               'indice': int(round(idx)) if idx is not None else None,
                               'es_dia': d == dia})
            d += timedelta(days=1)
        return {'puntos': puntos[-n:]}

    @api.model
    def _reparto_activo(self, equipo):
        """En que se fue el tiempo ACTIVO del equipo: las cuatro clases que suman
        exactamente el activo (lo neutral y el navegador se derivan, como en la
        dona del tablero)."""
        act = equipo['activo']
        if act <= 0:
            return []
        otro = max(act - equipo['prod'] - equipo['distr'] - equipo['sin_clasificar'], 0.0)
        partes = [('productiva', equipo['prod']), ('otro', otro),
                  ('distraccion', equipo['distr']), ('sin', equipo['sin_clasificar'])]
        salida = []
        for k, v in partes:
            pct = v / act * 100
            if pct >= TRAMO_MIN_PCT:
                salida.append({'t': TRAMOS[k][0], 'c': TRAMOS[k][1], 'w': round(pct, 2),
                               'pct': int(round(pct)), 'h': _hm(v)})
        return salida

    @api.model
    def _dia_previo_con_dato(self, dia, empleados, max_atras=7):
        """El ultimo dia anterior con uso registrado (salta fines de semana y
        festivos sin inventar nada)."""
        Usage = self.env['foco.usage'].sudo()
        for i in range(1, max_atras + 1):
            d = dia - timedelta(days=i)
            if Usage.search_count([('date', '=', d), ('employee_id', 'in', empleados.ids)], limit=1):
                return d
        return None

    @api.model
    def _armar(self, dia):
        zona = self._zona()
        ahora = datetime.now(zona)
        es_hoy = dia == ahora.date()
        computers = self.env['foco.computer'].sudo().search([('employee_id', '!=', False)])
        empleados = computers.mapped('employee_id').sorted(lambda e: e.name or '')
        categorias = {c.id: {'name': c.name, 'weight': c.weight, 'is_system': c.is_system}
                      for c in self.env['foco.category'].sudo().search([])}

        por, equipo, distracciones = self._numeros_dia(dia, empleados, categorias)
        adh = self.env['foco.workday'].sudo().adherencia(dia, dia)
        personas_adh = adh.get('personas') or {}
        salud = self.env['foco.computer'].sudo().health_summary() if es_hoy else {}
        analitica = self.env['foco.usage'].sudo().analitica(dia, dia) or {}
        extra_por = {e['id']: e for e in (analitica.get('empleados') or [])}

        # Comparativos: el dia previo con dato y el mismo dia de la semana pasada.
        previo = self._dia_previo_con_dato(dia, empleados)
        _, eq_prev, _ = self._numeros_dia(previo, empleados, categorias) if previo else (None, None, None)
        sem = dia - timedelta(days=7)
        _, eq_sem, _ = self._numeros_dia(sem, empleados, categorias)

        # Justificaciones: devueltas (rebote vivo) y marcadas a revisar sin ver.
        Abs = self.env['foco.absence'].sudo()
        devueltas = Abs.search([('employee_id', 'in', empleados.ids), ('rebote_pendiente', '=', True)])
        a_revisar = Abs.search([('employee_id', 'in', empleados.ids),
                                ('ai_requiere_revision', '=', True), ('review_visto', '=', False)])
        # Veredictos de la IA del dia que no parecen trabajo y nadie ha revisado.
        ini_utc = zona.localize(datetime.combine(dia, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
        fin_utc = zona.localize(datetime.combine(dia, time.max)).astimezone(pytz.UTC).replace(tzinfo=None, microsecond=0)
        ia_sin_revisar = self.env['foco.integrity.verdict'].sudo().search(
            [('employee_id', 'in', empleados.ids), ('state', '=', 'analizado'),
             ('review_outcome', '=', 'pendiente'),
             ('started_at', '>=', ini_utc), ('started_at', '<=', fin_utc)])
        ia_sosp = ia_sin_revisar.filtered('sospechoso')
        # Salidas sin cerrar de AYER (hoy una abierta es normal).
        sin_salida_ayer = self.env['foco.workday'].sudo().search(
            [('employee_id', 'in', empleados.ids), ('date', '=', dia - timedelta(days=1)),
             ('check_out_missing', '=', True)])
        sin_clasificar = self.env['foco.usage'].sudo().sitios_por_clasificar(1 if es_hoy else 7, 6)

        filas = []
        for emp in empleados:
            e = por[emp.id]
            a = personas_adh.get(str(emp.id)) or {}
            s = (salud or {}).get(str(emp.id)) or {}
            x = extra_por.get(emp.id) or {}
            apps = sorted(e['apps'].items(), key=lambda kv: -kv[1])
            distrs = sorted(e['distrs'].items(), key=lambda kv: -kv[1])
            hechos = x.get('hechos') or []
            fuertes = [h for h in hechos if h.get('kind') in HECHOS_FUERTES]
            ia = x.get('ia') or {}
            estado = a.get('estado') or ''
            filas.append({
                'id': emp.id, 'nombre': emp.name, 'depto': emp.department_id.name or '',
                'puesto': emp.job_title or '',
                'con_dato': e['con_dato'],
                'estado': estado, 'estado_txt': a.get('texto') or ADHERENCIA_ETQ.get(estado, ''),
                'fuente': a.get('fuente') or '',
                'entrada': a.get('entrada') or '', 'salida': a.get('salida') or '',
                'hora_entrada': _hhmm(a['hora_entrada']) if a.get('hora_entrada') else '',
                'hora_salida': _hhmm(a['hora_salida']) if a.get('hora_salida') else '',
                'tarde_min': a.get('tarde_min') or 0, 'antes_min': a.get('antes_min') or 0,
                'salio_antes': bool(a.get('salio_antes')), 'no_checo': bool(a.get('no_checo')),
                'permiso': a.get('permiso') or '',
                'usa_checador': e['usa_checador'], 'checado_no_medible': e['checado_no_medible'],
                'checado': round(e['checado'], 3), 'extra': round(e['extra'], 3),
                'faltante': round(e['faltante'], 3),
                'esperado': round(e['esperado'], 3), 'cubierto': round(e['cubierto'], 3),
                'justificado': round(e['justificado'], 3), 'sin_explicar': round(e['sin_explicar'], 3),
                'activo': round(e['activo'], 3), 'prod': round(e['prod'], 3),
                'distr': round(e['distr'], 3), 'idle': round(e['idle'], 3),
                'llamada': round(e['llamada'], 3), 'sin_clasificar': round(e['sin_clasificar'], 3),
                'aprobado': round(e['aprobado'], 3), 'por_decidir': round(e['por_decidir'], 3),
                'vacaciones': round(e['vacaciones'], 3), 'rechazado': round(e['rechazado'], 3),
                'sin_actividad': round(e['sin_actividad'], 3),
                'sinact_fuera': round(e['sinact_fuera'], 3),
                'sinact_presente': round(e['sinact_presente'], 3),
                'comida': round(e['comida'], 3), 'jornada': round(e['jornada'], 3),
                'indice': e['indice'],
                'pendientes': e['pendientes'], 'lag_min': e['lag_min'],
                'top_app': apps[0][0] if apps else '', 'top_app_h': round(apps[0][1], 3) if apps else 0.0,
                'top_distr': distrs[0][0] if distrs else '',
                'top_distr_h': round(distrs[0][1], 3) if distrs else 0.0,
                'presencia': s.get('presence') or '', 'presencia_txt': s.get('presence_label') or '',
                'salud': s.get('health') or '',
                'hechos_n': x.get('hechos_n') or 0,
                'hechos_txt': '; '.join('%s: %s' % (h['etiqueta'], h['texto']) for h in hechos[:3]),
                'hechos_fuertes_n': len(fuertes),
                'hechos_fuertes_txt': '; '.join('%s: %s' % (h['etiqueta'], h['texto']) for h in fuertes[:3]),
                'ia_sospechosos': ia.get('sospechosos') or 0, 'ia_sin_revisar': ia.get('sin_revisar') or 0,
                'devueltas': len(devueltas.filtered(lambda r, i=emp.id: r.employee_id.id == i)),
                'sin_salida_ayer': bool(sin_salida_ayer.filtered(lambda w, i=emp.id: w.employee_id.id == i)),
            })
        # Orden: primero quien tiene dato, por indice; al final los que no.
        filas.sort(key=lambda f: (not f['con_dato'], -f['indice'], -f['activo']))

        def nombres(lista, fmt):
            return [fmt(f) for f in lista]

        tarde = [f for f in filas if f['estado'] == 'tarde']
        faltaron = [f for f in filas if f['estado'] in ADH_AUSENCIA]
        permiso = [f for f in filas if f['estado'] in ('permiso', 'festivo')]
        salio_antes = [f for f in filas if f['salio_antes']]
        sin_checada = [f for f in filas if f['estado'] == 'sin_checada']
        no_checo = [f for f in filas if f['no_checo']]
        a_tiempo = [f for f in filas if f['estado'] == 'a_tiempo']
        en_curso = [f for f in filas if f['estado'] in ('no_iniciado', 'sin_entrada')]
        sin_dato = [f for f in filas if f['estado'] == 'sin_dato']
        sin_senal_hoy = [f for f in filas if f['salud'] in ('stale', 'never')] if es_hoy else []

        atencion = []
        if tarde:
            atencion.append({'clave': 'tarde', 'titulo': 'Llegaron tarde', 'n': len(tarde),
                             'detalle': nombres(tarde, lambda f: '%s %s (+%d min, su hora %s)' % (
                                 f['nombre'], f['entrada'], f['tarde_min'], f['hora_entrada']))})
        if faltaron:
            atencion.append({'clave': 'falto', 'titulo': 'Sin registro en todo el dia', 'n': len(faltaron),
                             'detalle': nombres(faltaron, lambda f: '%s (%s)' % (
                                 f['nombre'], 'ni checada ni senal de su equipo' if f['estado'] == 'falto'
                                 else 'no usa checador; su equipo no dio senal de persona'))})
        if salio_antes:
            atencion.append({'clave': 'salio_antes', 'titulo': 'Salieron antes de su horario', 'n': len(salio_antes),
                             'detalle': nombres(salio_antes, lambda f: '%s checo salida %s (%d min antes de las %s)' % (
                                 f['nombre'], f['salida'], f['antes_min'], f['hora_salida']))})
        if no_checo:
            atencion.append({'clave': 'no_checo', 'titulo': 'Estuvieron pero no checaron entrada', 'n': len(no_checo),
                             'detalle': nombres(no_checo, lambda f: '%s (primera senal en su equipo %s)' % (
                                 f['nombre'], f['entrada']))})
        if sin_checada:
            atencion.append({'clave': 'sin_checada', 'titulo': 'Checada abierta de otro dia', 'n': len(sin_checada),
                             'detalle': nombres(sin_checada, lambda f: '%s: una checada anterior sigue abierta; '
                                                'no se puede afirmar si vino' % f['nombre'])})
        if sin_salida_ayer:
            atencion.append({'clave': 'sin_salida', 'titulo': 'Ayer no cerraron salida', 'n': len(sin_salida_ayer),
                             'detalle': [w.employee_id.name for w in sin_salida_ayer]})
        if devueltas:
            atencion.append({'clave': 'devueltas', 'titulo': 'Justificaciones devueltas por la IA, esperando respuesta',
                             'n': len(devueltas),
                             'detalle': ['%s: "%s" (%d vez)' % (r.employee_id.name, (r.nota_rechazada or '')[:40], r.rebotes)
                                         for r in devueltas[:8]]})
        if a_revisar:
            atencion.append({'clave': 'revisar', 'titulo': 'Justificaciones vagas por revisar (acumulado)',
                             'n': len(a_revisar),
                             'detalle': ['%s (%d)' % (emp.name, n) for emp, n in
                                         self.env['foco.absence'].sudo()._read_group(
                                             [('id', 'in', a_revisar.ids)], ['employee_id'], ['__count'])][:8]})
        if ia_sosp:
            atencion.append({'clave': 'ia', 'titulo': 'Episodios que la IA no vio como trabajo, sin revisar',
                             'n': len(ia_sosp),
                             'detalle': ['%s: %s (%d min)' % (v.employee_id.name, v.app_name or v.exe or '',
                                                              int(round(v.duration_min or 0)))
                                         for v in ia_sosp[:8]]})
        hechos_hoy = [f for f in filas if f['hechos_fuertes_n']]
        if hechos_hoy:
            atencion.append({'clave': 'hechos', 'titulo': 'Hechos de integridad que piden mirar', 'n': len(hechos_hoy),
                             'detalle': nombres(hechos_hoy, lambda f: '%s: %s' % (f['nombre'], f['hechos_fuertes_txt']))})
        if sin_senal_hoy:
            atencion.append({'clave': 'sin_senal', 'titulo': 'Equipos sin senal en horario (dato incompleto)',
                             'n': len(sin_senal_hoy),
                             'detalle': nombres(sin_senal_hoy, lambda f: '%s (%s)' % (
                                 f['nombre'], 'nunca reporto' if f['salud'] == 'never' else 'dejo de reportar'))})
        distr_1h = [f for f in filas if f['distr'] >= 1.0]
        if distr_1h:
            atencion.append({'clave': 'distraccion', 'titulo': 'Mas de una hora en distraccion', 'n': len(distr_1h),
                             'detalle': nombres(distr_1h, lambda f: '%s %s (%s %s)' % (
                                 f['nombre'], _hm(f['distr']), f['top_distr'], _hm(f['top_distr_h'])))})

        con_dato = [f for f in filas if f['con_dato']]
        mejores = [f for f in con_dato if f['esperado'] > 0][:3]
        peores = [f for f in reversed(con_dato) if f['esperado'] > 0 and f['salud'] not in ('stale', 'never')][:3]

        gracia = adh.get('gracia') or 0
        # El indice del tablero cuenta la jornada esperada de TODOS, incluida la
        # gente con permiso validado (medido el 9-oct: las 8:30 de una persona
        # de vacaciones bajaban el indice del equipo de 49 a 46). Aqui se dice
        # ademas el indice SIN esas jornadas; el del tablero no se toca hasta
        # que se defina la utilizacion neta.
        esperado_permisos = sum(f['esperado'] for f in permiso)
        esperado_neto = equipo['esperado'] - esperado_permisos
        indice_neto = (min(100, int(round(equipo['cubierto'] / esperado_neto * 100)))
                       if esperado_neto > 0 else equipo['indice'])
        resumen = 'Indice %d%% · %d a tiempo · %d tarde · %d sin registro · %d con permiso' % (
            equipo['indice'], len(a_tiempo), len(tarde), len(faltaron), len(permiso))
        ajustes = self.env['foco.settings'].sudo().get_settings()
        meta_h = ajustes.meta_productiva_h or 7.0
        bolsa_h = (ajustes.justify_gracia_min or 0) / 60.0
        serie = self._serie_indice(dia)
        # El punto de HOY es la cifra de la tarjeta: misma formula, pero el
        # tablero y este reporte cuentan solo a los equipos con persona; asi la
        # columna del dia y el numero grande no se contradicen.
        for p in serie['puntos']:
            if p['es_dia']:
                p['indice_serie'] = p['indice']
                p['indice'] = equipo['indice']
        # Columnas de hasta 84 px; la linea del 100% a la misma altura en todas.
        tope = max([100] + [p['indice'] or 0 for p in serie['puntos']])
        for p in serie['puntos']:
            p['alto'] = int(round((p['indice'] or 0) / tope * 84)) if p['indice'] else 0
            p['color'] = _color_indice(p['indice'])
        serie['alto_meta'] = int(round(100 / tope * 84))
        datos = {
            'fecha': fields.Date.to_string(dia), 'fecha_txt': _fecha_es(dia),
            'es_hoy': es_hoy, 'corte': ahora.strftime('%H:%M'),
            'generado': ahora.strftime('%Y-%m-%d %H:%M'), 'zona': str(zona),
            'equipo': {
                'monitoreados': len(empleados), 'con_dato': equipo['con_dato'],
                'indice': equipo['indice'], 'cubierto': round(equipo['cubierto'], 3),
                'esperado': round(equipo['esperado'], 3), 'prod': round(equipo['prod'], 3),
                'activo': round(equipo['activo'], 3), 'distr': round(equipo['distr'], 3),
                'distr_pct': equipo['distr_pct'], 'idle': round(equipo['idle'], 3),
                'justificado': round(equipo['justificado'], 3),
                'sin_explicar': round(equipo['sin_explicar'], 3),
                'esperado_permisos': round(esperado_permisos, 3),
                'indice_neto': indice_neto,
                'previo': fields.Date.to_string(previo) if previo else '',
                'previo_txt': _fecha_es(previo) if previo else '',
                'indice_previo': eq_prev['indice'] if eq_prev else None,
                'delta_previo': (equipo['indice'] - eq_prev['indice']) if eq_prev else None,
                'semana': fields.Date.to_string(sem), 'semana_txt': _fecha_es(sem),
                'indice_semana': eq_sem['indice'] if eq_sem and eq_sem['con_dato'] else None,
                'delta_semana': (equipo['indice'] - eq_sem['indice']) if eq_sem and eq_sem['con_dato'] else None,
                'en_linea': len([f for f in filas if f['salud'] == 'ok']) if es_hoy else None,
                'sin_senal': len(sin_senal_hoy),
                'meta': round(equipo['meta'], 3), 'aprobado': round(equipo['aprobado'], 3),
                'sin_clasificar': round(equipo['sin_clasificar'], 3),
                'meta_h': meta_h, 'bolsa_min': int(round(bolsa_h * 60)),
            },
            # Las graficas (16-oct-2026): la barra de la jornada del tablero, la
            # tendencia de los dias laborables y el reparto del tiempo activo.
            'grafica': self._barras_jornada(filas, meta_h, bolsa_h),
            'serie': serie,
            'reparto': self._reparto_activo(equipo),
            'asistencia': {
                'gracia': gracia,
                'a_tiempo': len(a_tiempo), 'tarde': len(tarde), 'falto': len(faltaron),
                'permiso': len(permiso), 'salio_antes': len(salio_antes),
                'sin_checada': len(sin_checada), 'no_checo': len(no_checo),
                'en_curso': len(en_curso), 'sin_dato': len(sin_dato),
                'tarde_min_total': sum(f['tarde_min'] for f in tarde),
                'por_equipo': len([f for f in filas if f['fuente'] == 'equipo' and f['estado'] in ('a_tiempo', 'tarde')]),
            },
            'atencion': atencion,
            'personas': filas,
            'mejores': [{'nombre': f['nombre'], 'indice': f['indice']} for f in mejores],
            'peores': [{'nombre': f['nombre'], 'indice': f['indice']} for f in peores],
            'distracciones': distracciones,
            'sin_clasificar': [{'nombre': s['host'], 'horas': round(s['hours'], 3)} for s in sin_clasificar],
            'resumen': resumen,
        }
        return datos

    # ------------------------------------------------------------- generar
    @api.model
    def generar(self, dia):
        """Arma (o rearma) el reporte de ese dia y lo guarda."""
        dia = fields.Date.to_date(dia)
        datos = self._armar(dia)
        html = self.env['ir.qweb'].sudo()._render(
            'foco_monitor.reporte_diario_html', {'d': datos, 'hm': _hm})
        # Sin la sangria de la plantilla: en HTML un salto de linea con espacios
        # se ve igual que un espacio, y la sangria es casi la mitad del peso.
        # Importa porque Gmail recorta el correo pasando ~102 KB ("Mensaje
        # recortado") y con las graficas de 16 personas se pasaba.
        html = Markup(re.sub(r'\s*\n\s*', ' ', str(html)))
        vals = {'date': dia, 'html': html, 'datos': json.dumps(datos, ensure_ascii=False),
                'resumen': datos['resumen'], 'generated_at': fields.Datetime.now(),
                'corte': datos['corte']}
        rec = self.sudo().search([('date', '=', dia)], limit=1)
        if rec:
            rec.write(vals)
        else:
            rec = self.sudo().create(vals)
        return rec

    def action_regenerar(self):
        for r in self:
            self.generar(r.date)
        return True

    # -------------------------------------------------------------- enviar
    def _destinatarios(self):
        ajustes = self.env['foco.settings'].sudo().get_settings()
        correos = []
        for u in ajustes.reporte_diario_user_ids:
            if u.email and u.email not in correos:
                correos.append(u.email)
        for c in (ajustes.reporte_diario_emails or '').split(','):
            c = c.strip()
            if '@' in c and c not in correos:
                correos.append(c)
        return correos

    def enviar(self):
        """Manda el reporte a los destinatarios configurados. Devuelve cuantos."""
        self.ensure_one()
        correos = self._destinatarios()
        if not correos:
            _logger.warning('Foco: reporte diario de %s sin destinatarios', self.date)
            return 0
        base = self.get_base_url()
        datos = json.loads(self.datos or '{}')
        eq = datos.get('equipo') or {}
        # El resumen ya empieza con el indice: antes iba dos veces en el asunto.
        asunto = 'Foco · %s · %s' % (datos.get('fecha_txt', self.date),
                                     self.resumen or 'indice %s%%' % eq.get('indice', 0))
        # Con un remitente configurado, sale por SU servidor (ver
        # foco.settings.reporte_diario_remitente: sin el, Odoo lo reenviaba por
        # el de marketing con otro remitente y el proveedor lo rebotaba).
        propio = (self.env['foco.settings'].sudo().get_settings().reporte_diario_remitente or '').strip()
        remitente = (formataddr(('Foco · FERBA', propio)) if propio else
                     (self.env.company.partner_id.email_formatted
                      or self.env.user.email_formatted or False))
        # Markup: `self.html` es un campo Html (Markup) y sumarle un str lo
        # ESCAPA; el pie llegaba como texto "<p style=..." (correo del 15-oct).
        pie = Markup('<p style="font-family:Arial,sans-serif;font-size:11px;color:#8b95a6;">'
                     'Generado por Foco a las %s · <a href="%s/odoo/action-foco_monitor.foco_dashboard_action" '
                     'style="color:#2f6fed;">Abrir el tablero</a></p>') % (self.corte or '', base)
        mail = self.env['mail.mail'].sudo().create({
            'subject': asunto,
            'body_html': Markup(self.html or '') + pie,
            'email_to': ', '.join(correos),
            'email_from': remitente,
            'auto_delete': False,
        })
        mail.send(raise_exception=False)
        if mail.state == 'exception':
            _logger.warning('Foco: el reporte diario de %s no salio: %s', self.date, mail.failure_reason)
        self.sudo().write({'state': 'enviado', 'sent_at': fields.Datetime.now(),
                           'sent_to': ', '.join(correos)})
        _logger.info('Foco: reporte diario de %s enviado a %s', self.date, correos)
        return len(correos)

    def action_enviar(self):
        for r in self:
            r.enviar()
        return True

    @api.model
    def _cron_enviar(self):
        """Cada 15 minutos: si ya es la hora configurada y hoy no se ha
        enviado, arma el de hoy y lo manda. Una vez por dia."""
        ajustes = self.env['foco.settings'].sudo().get_settings()
        if not ajustes.reporte_diario_activo:
            return False
        zona = self._zona()
        ahora = datetime.now(zona)
        hoy = ahora.date()
        if ajustes.reporte_diario_ultimo == hoy:
            return False
        if ahora.hour + ahora.minute / 60.0 < (ajustes.reporte_diario_hora or 0.0):
            return False
        rep = self.generar(hoy)
        datos = json.loads(rep.datos or '{}')
        if not (datos.get('equipo') or {}).get('esperado'):
            # Nadie tenia jornada hoy (domingo, festivo general): no hay que
            # reportar nada, pero se marca para no volver a intentar.
            ajustes.write({'reporte_diario_ultimo': hoy})
            return False
        rep.enviar()
        ajustes.write({'reporte_diario_ultimo': hoy})
        return True
