import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Eventos que prueban que habia una PERSONA, no solo una maquina encendida.
#
# La distincion la habilita un hecho del despliegue, no una corazonada: la tarea
# del agente se registra con `-AtLogOn` (setup_tasks.ps1), asi que solo arranca
# cuando alguien inicia sesion. Un reinicio de Windows Update a las 3 a.m. deja
# el equipo en la pantalla de bloqueo, sin sesion y sin agente: hay 'encendido'
# pero NO hay 'agente_inicio'.
#
# Sin esto, ese reinicio marcaba el inicio de la jornada a las 3 a.m. y el dia
# se leia como de veinte horas.
CON_PERSONA = ('agente_inicio', 'agente_fin', 'desbloqueo', 'bloqueo',
               'llamada_inicio', 'llamada_fin')

# 'apagado_solicitado' (evento 1074) es el caso mixto: lo puede pedir la persona
# o un servicio. Se resuelve mirando QUIEN lo pidio, que el propio evento trae.

# Como se llama cada tramo de la cinta del dia.
BARRA = chr(92)   # la barra invertida de las rutas de Windows

SEGMENTOS = ('activo', 'llamada', 'sin_input', 'bloqueado', 'apagado',
             'suspendido', 'sin_explicar')

# Un hueco offline se explica con el evento que ocurrio DENTRO de el. No hay
# umbral ni margen: o el apagado cae entre el inicio y el fin del hueco, o no lo
# explica. Preferir "sin explicar" a una explicacion aproximada es el punto.
EXPLICA = {
    'apagado': 'apagado',
    'apagado_inesperado': 'apagado',
    'apagado_solicitado': 'apagado',
    'suspendido': 'suspendido',
}


class FocoWorkday(models.Model):
    """La jornada de una persona en un dia: a que hora empezo, a que hora termino.

    Existe porque `foco.usage` son TOTALES DEL DIA y no tienen hora: sirven para
    contestar "cuanto", nunca "cuando". Todas las preguntas que se hace un jefe
    -a que hora llego, por que hay un hueco a las once, se quedo de mas el
    martes- son preguntas de CUANDO.
    """

    _name = 'foco.workday'
    _description = 'Jornada del dia'
    _order = 'date desc, employee_id'
    _rec_name = 'date'

    _uniq = models.Constraint(
        'unique(employee_id, date)',
        'Ya existe la jornada de esa persona para ese dia.')

    employee_id = fields.Many2one(
        'hr.employee', string='Empleado', required=True,
        ondelete='cascade', index=True)
    department_id = fields.Many2one(
        related='employee_id.department_id', string='Departamento', store=True)
    date = fields.Date(string='Dia', required=True, index=True)

    first_signal = fields.Datetime(
        string='Primera senal', readonly=True,
        help='Lo primero que prueba que la persona estaba: encendio el equipo, '
             'lo desbloqueo o volvio a haber actividad. NO es la hora de '
             'entrada a la oficina: el sistema no sabe eso, sabe a que hora '
             'aparecio en su equipo.')
    last_signal = fields.Datetime(string='Ultima senal', readonly=True)
    first_kind = fields.Char(string='Como empezo', readonly=True)
    last_kind = fields.Char(string='Como termino', readonly=True)

    span_hours = fields.Float(
        string='De la primera a la ultima (h)', readonly=True,
        help='El largo del dia, huecos incluidos. No es tiempo trabajado.')
    active_hours = fields.Float(string='Activo (h)', readonly=True)
    idle_hours = fields.Float(string='Sin input (h)', readonly=True)
    call_hours = fields.Float(string='En llamada (h)', readonly=True)
    in_shift_hours = fields.Float(
        string='Dentro de jornada (h)', readonly=True)
    off_shift_hours = fields.Float(
        string='Fuera de jornada (h)', readonly=True,
        help='Tiempo activo medido fuera del horario. No es una falta ni un '
             'merito: es tiempo que antes no se registraba y ahora si, para '
             'que se pueda reconocer a quien devuelve horas o se queda de mas.')
    expected_hours = fields.Float(string='Jornada esperada (h)', readonly=True)

    gap_count = fields.Integer(string='Huecos', readonly=True)
    unexplained_minutes = fields.Integer(
        string='Sin explicar (min)', readonly=True,
        help='Huecos que no cubre ni el calendario ni un evento del equipo ni '
             'una justificacion. Es lo unico que amerita una conversacion.')
    power_events = fields.Integer(string='Eventos del equipo', readonly=True)

    state = fields.Selection(
        [('completo', 'Con horario'),
         ('solo_equipo', 'Solo actividad del equipo'),
         ('sin_detalle', 'Sin detalle de horas'),
         ('sin_dato', 'Sin dato')],
        string='Estado', default='sin_dato', readonly=True, index=True,
        help='"Sin detalle de horas" es dato viejo: se midio cuanto trabajo '
             'pero no a que hora, porque el equipo todavia no reportaba '
             'eventos. "Solo actividad del equipo" es un dia en el que consta '
             'que la maquina se encendio o se apago pero NO hay constancia de '
             'una persona. Ninguno de los dos es lo mismo que no haber '
             'trabajado.')

    # ----------------------------------------------------------- utilidades
    @api.model
    def _tz(self, employee):
        return self.env['foco.settings'].sudo()._tzinfo_for(employee)

    @api.model
    def _limites_utc(self, dia, zona):
        """El dia LOCAL de la persona, expresado en UTC naive (como guarda Odoo)."""
        ini = zona.localize(datetime.combine(dia, time.min))
        fin = zona.localize(datetime.combine(dia, time.max))
        return (ini.astimezone(pytz.UTC).replace(tzinfo=None),
                fin.astimezone(pytz.UTC).replace(tzinfo=None))

    @api.model
    def _hora_local(self, dt_utc, zona, dia):
        """UTC naive -> hora decimal local (0..24) dentro de ese dia.

        Lo que cae antes del dia se acota a 0 y lo que cae despues a 24: un
        hueco que empezo anoche entra al dia de hoy por el borde, que es
        exactamente lo que se quiere dibujar.
        """
        local = pytz.UTC.localize(dt_utc).astimezone(zona)
        delta = (local.date() - dia).days
        if delta < 0:
            return 0.0
        if delta > 0:
            return 24.0
        return local.hour + local.minute / 60.0 + local.second / 3600.0

    # ------------------------------------------------------------ recalculo
    @api.model
    def rebuild(self, employees, dias):
        """Rehace la jornada de esas personas en esos dias.

        Se llama al recibir un envio (barato: una persona, uno o dos dias) y
        desde el proceso nocturno. Es un recalculo COMPLETO del dia, no un
        acumulado: asi un reenvio del agente no duplica nada.
        """
        if not employees or not dias:
            return self.browse()
        Usage = self.env['foco.usage'].sudo()
        Event = self.env['foco.event'].sudo()
        Absence = self.env['foco.absence'].sudo()
        Ajustes = self.env['foco.settings'].sudo()
        ajustes = Ajustes.get_settings()
        salida = self.browse()

        for emp in employees:
            zona = self._tz(emp)
            for dia in dias:
                ini_utc, fin_utc = self._limites_utc(dia, zona)
                usos = Usage.search([('employee_id', '=', emp.id),
                                     ('date', '=', dia)])
                eventos = Event.search([('employee_id', '=', emp.id),
                                        ('date', '=', dia)], order='at')
                # Huecos que TOCAN el dia, aunque hayan empezado la vispera.
                huecos = Absence.search([
                    ('employee_id', '=', emp.id),
                    ('start', '<=', fin_utc), ('stop', '>=', ini_utc)],
                    order='start')

                vals = {
                    'active_hours': sum(usos.mapped('fg_active')),
                    'idle_hours': sum(usos.mapped('fg_idle')),
                    'call_hours': sum(usos.mapped('call_hours')),
                    'in_shift_hours': sum(
                        u.fg_active for u in usos if u.shift != 'off'),
                    'off_shift_hours': sum(
                        u.fg_active for u in usos if u.shift == 'off'),
                    'expected_hours': ajustes.expected_seconds(
                        emp, ini_utc, fin_utc) / 3600.0,
                    'gap_count': len(huecos),
                    'unexplained_minutes': int(round(sum(
                        h.duration for h in huecos if h.state == 'pendiente') * 60)),
                    'power_events': len(eventos),
                }

                primero, primero_k, primero_p = self._primera_senal(
                    eventos, huecos, ini_utc, fin_utc)
                ultimo, ultimo_k, ultimo_p = self._ultima_senal(
                    emp, eventos, huecos, ini_utc, fin_utc)
                vals.update({
                    'first_signal': primero, 'first_kind': primero_k,
                    'last_signal': ultimo, 'last_kind': ultimo_k,
                    'span_hours': ((ultimo - primero).total_seconds() / 3600.0
                                   if primero and ultimo and ultimo > primero else 0.0),
                })
                if primero and ultimo and (primero_p or ultimo_p or usos):
                    vals['state'] = 'completo'
                elif primero and ultimo:
                    # Consta lo que hizo la maquina, no que hubiera alguien.
                    # Decirlo "completo" seria afirmar una jornada que nadie
                    # trabajo; decirlo "sin dato" negaria unos hechos que si
                    # tenemos.
                    vals['state'] = 'solo_equipo'
                elif usos:
                    # Hay horas medidas pero nada que las situe en el reloj. Es
                    # dato de antes de que el agente reportara eventos, y decirlo
                    # asi evita que se confunda con no haber trabajado.
                    vals['state'] = 'sin_detalle'
                else:
                    vals['state'] = 'sin_dato'

                jornada = self.search([('employee_id', '=', emp.id),
                                       ('date', '=', dia)], limit=1)
                if jornada:
                    jornada.write(vals)
                elif vals['state'] == 'sin_dato':
                    # Un dia del que no se sabe absolutamente nada no merece un
                    # renglon: crearlos llenaria la base de vacios cada vez que
                    # alguien mire un mes hacia atras.
                    continue
                else:
                    vals.update({'employee_id': emp.id, 'date': dia})
                    jornada = self.create(vals)
                salida |= jornada
        return salida

    @api.model
    def _momentos(self, eventos, huecos, ini_utc, fin_utc):
        """Todos los instantes del dia en que consta que algo pasaba en el equipo.

        Un solo conjunto para el principio y para el final, y no dos listas de
        eventos "de apertura" y "de cierre". Separarlas parecia mas fino y daba
        jornadas que terminaban ANTES de empezar: un dia cuyo unico registro es
        un reinicio -apago 17:09, encendio 17:14- tiene su cierre antes que su
        apertura, y los dos numeros son ciertos por separado.

        Con un solo conjunto, el primero y el ultimo salen del mismo orden y
        `ultima >= primera` se cumple siempre. Lo que fue cada extremo lo dice
        su etiqueta, asi que una jornada que "empieza" con un apagado se explica
        sola en la pantalla.
        """
        momentos = [(e.at, e.kind, self._hay_persona(e)) for e in eventos
                    if ini_utc <= e.at <= fin_utc]
        momentos += self._bordes_de_huecos(huecos, ini_utc, fin_utc)
        return momentos

    @api.model
    def _hay_persona(self, evento):
        """Este evento prueba que habia alguien, o solo que la maquina hizo algo?"""
        if evento.kind in CON_PERSONA:
            return True
        if evento.kind != 'apagado_solicitado':
            return False
        # Quien pidio el apagado. Se compara contra el usuario de Windows con el
        # que se enrolo el equipo, y NO contra una lista de cuentas de servicio:
        # una lista habria que mantenerla, y ademas el nombre de esas cuentas se
        # escribe distinto en cada idioma de Windows.
        quien = (evento.user_name or '').replace('/', BARRA)
        quien = quien.rsplit(BARRA, 1)[-1].strip().lower()
        suyo = (evento.computer_id.windows_user or '').strip().lower()
        return bool(quien and suyo and quien == suyo)

    @api.model
    def _extremo(self, momentos, fn):
        """El primero o el ultimo, prefiriendo SIEMPRE los momentos con persona.

        Los de maquina quedan de respaldo para los dias en que no hay otra cosa:
        decir "encendido 03:04" es mas honesto que decir que no se sabe nada,
        siempre que la pantalla deje claro que no hay constancia de nadie.
        """
        humanos = [m for m in momentos if m[2]]
        elegidos = humanos or momentos
        if not elegidos:
            return False, False, False
        return fn(elegidos, key=lambda c: c[0])

    @api.model
    def _primera_senal(self, eventos, huecos, ini_utc, fin_utc):
        return self._extremo(
            self._momentos(eventos, huecos, ini_utc, fin_utc), min)

    @api.model
    def _bordes_de_huecos(self, huecos, ini_utc, fin_utc):
        """Momentos de un hueco que prueban que la PERSONA estaba.

        No todos los extremos valen lo mismo, y la diferencia importa:

        - En un hueco de inactividad o de bloqueo el equipo siguio encendido,
          asi que los DOS extremos son momentos en que alguien interactuo: dejo
          de teclear, o volvio a teclear.
        - En uno offline el equipo estaba apagado. Su inicio si prueba presencia
          -es el ultimo latido, con la persona ahi-, pero su fin solo prueba que
          la MAQUINA volvio. Que un equipo se encienda a las 23:50 por una
          actualizacion de Windows no es alguien trabajando, y tomarlo como
          "ultima senal" alargaria la jornada con una hora que nadie trabajo.
          Cuando de verdad vuelve una persona, hay un encendido o un desbloqueo
          que lo dice, y esos ya cuentan por su cuenta.
        """
        bordes = []
        for h in huecos:
            if h.kind == 'offline':
                # El ultimo latido antes de que el equipo se fuera: prueba que
                # el agente corria, no que hubiera alguien tecleando.
                momentos = [(h.start, 'ultimo_latido', False)]
            else:
                momentos = [(h.start, 'actividad', True),
                            (h.stop, 'actividad', True)]
            for momento, etiqueta, persona in momentos:
                if momento and ini_utc <= momento <= fin_utc:
                    bordes.append((momento, etiqueta, persona))
        return bordes

    @api.model
    def _ultima_senal(self, emp, eventos, huecos, ini_utc, fin_utc):
        momentos = self._momentos(eventos, huecos, ini_utc, fin_utc)
        # El ultimo latido prueba que el AGENTE seguia corriendo, no que hubiera
        # alguien delante: un equipo encendido toda la noche late hasta la
        # medianoche. Entra como momento de maquina, asi que solo decide el
        # final de un dia en el que no hay ninguna senal de persona.
        for pc in self.env['foco.computer'].sudo().search(
                [('employee_id', '=', emp.id)]):
            if pc.last_seen and ini_utc <= pc.last_seen <= fin_utc:
                momentos.append((pc.last_seen, 'latido', False))
        return self._extremo(momentos, max)

    # ------------------------------------------------- dentro o fuera del horario
    @api.model
    def jornada_serie(self, desde, hasta):
        """Horas DENTRO y FUERA del horario, por dia y para todo el equipo.

        Es la pregunta que ningun tablero generico contesta y que a un
        administrador le importa mas que el total: no cuanto se trabajo, sino
        si cayo donde debia. Un equipo que rinde de noche y descansa de dia
        suma las mismas horas que uno en horario, y los dos casos piden
        conversaciones opuestas.

        No lleva juicio: devuelve las dos cantidades y deja la lectura a quien
        mira. Trabajar fuera de horario puede ser una urgencia atendida o una
        carga mal repartida, y el sistema no puede distinguirlas.
        """
        desde = fields.Date.to_date(desde)
        hasta = fields.Date.to_date(hasta)
        if not desde or not hasta or hasta < desde:
            return []
        por_dia = {}
        # `date:day` y no `date`: Odoo 19 exige la granularidad al agrupar por
        # una fecha.
        for dia, dentro, fuera in self._read_group(
                [('date', '>=', desde), ('date', '<=', hasta)],
                ['date:day'], ['in_shift_hours:sum', 'off_shift_hours:sum']):
            por_dia[dia] = (round(dentro or 0.0, 3), round(fuera or 0.0, 3))
        salida = []
        d = desde
        while d <= hasta:
            dentro, fuera = por_dia.get(d, (0.0, 0.0))
            salida.append({'date': fields.Date.to_string(d),
                           'dentro': dentro, 'fuera': fuera})
            d += timedelta(days=1)
        return salida

    # -------------------------------------------------------------- la cinta
    @api.model
    def cintas(self, employee_ids, desde, hasta):
        """Los dias listos para dibujar, uno por renglon.

        Devuelve tambien la ventana horaria COMUN del periodo. Que todas las
        cintas compartan escala es lo unico que hace valiosa una pila de ellas:
        si cada renglon se ajustara a sus propios datos ganaria resolucion y se
        perderia lo que de verdad importa, que las columnas se alineen y la
        forma de la semana aparezca sola.
        """
        desde = fields.Date.to_date(desde)
        hasta = fields.Date.to_date(hasta)
        if not desde or not hasta or hasta < desde:
            return {'dias': [], 'ventana': [8.0, 20.0]}
        empleados = self.env['hr.employee'].browse(employee_ids or []).exists()
        if not empleados:
            return {'dias': [], 'ventana': [8.0, 20.0]}

        Event = self.env['foco.event'].sudo()
        Absence = self.env['foco.absence'].sudo()
        Ajustes = self.env['foco.settings'].sudo()
        ajustes = Ajustes.get_settings()

        jornadas = self.search([('employee_id', 'in', empleados.ids),
                                ('date', '>=', desde), ('date', '<=', hasta)])
        por_clave = {(j.employee_id.id, j.date): j for j in jornadas}

        # Dias con dato pero sin jornada armada: se arman al mirarlos. Pasa con
        # todo lo anterior a este cambio y con lo que quede fuera de la ventana
        # del proceso nocturno; sin esto, un dia con eventos se mostraria como
        # "sin dato" para siempre, que es justo la confusion que hay que evitar.
        faltantes = set()
        for modelo, campo in (('foco.event', 'date'), ('foco.usage', 'date')):
            for emp, dia in self.env[modelo].sudo()._read_group(
                    [('employee_id', 'in', empleados.ids),
                     (campo, '>=', desde), (campo, '<=', hasta)],
                    ['employee_id', campo + ':day'], []):
                d = dia.date() if hasattr(dia, 'date') else dia
                if (emp.id, d) not in por_clave:
                    faltantes.add((emp.id, d))
        if faltantes:
            for emp_id in {f[0] for f in faltantes}:
                nuevas = self.sudo().rebuild(
                    self.env['hr.employee'].browse(emp_id),
                    sorted(d for e, d in faltantes if e == emp_id))
                for j in nuevas:
                    por_clave[(j.employee_id.id, j.date)] = j

        dias = []
        d = desde
        fechas = []
        while d <= hasta:
            fechas.append(d)
            d += timedelta(days=1)

        # Una sola lectura por modelo para todo el periodo, no una por dia.
        ini_global = min(self._limites_utc(fechas[0], self._tz(e))[0] for e in empleados)
        fin_global = max(self._limites_utc(fechas[-1], self._tz(e))[1] for e in empleados)
        eventos_all = Event.search([('employee_id', 'in', empleados.ids),
                                    ('at', '>=', ini_global),
                                    ('at', '<=', fin_global)], order='at')
        huecos_all = Absence.search([('employee_id', 'in', empleados.ids),
                                     ('start', '<=', fin_global),
                                     ('stop', '>=', ini_global)], order='start')

        lo, hi = 24.0, 0.0
        for emp in empleados:
            zona = self._tz(emp)
            evs = eventos_all.filtered(lambda e: e.employee_id.id == emp.id)
            hcs = huecos_all.filtered(lambda h: h.employee_id.id == emp.id)
            franjas = self._franjas_jornada(ajustes, emp)
            for dia in fechas:
                jornada = por_clave.get((emp.id, dia))
                fila = self._una_cinta(emp, dia, zona, jornada, evs, hcs, franjas)
                dias.append(fila)
                for seg in fila['segments']:
                    lo = min(lo, seg['a'])
                    hi = max(hi, seg['b'])
                for f in fila['shift']:
                    lo = min(lo, f[0])
                    hi = max(hi, f[1])

        if hi <= lo:
            lo, hi = 8.0, 20.0
        # Media hora de aire a cada lado para que las marcas de los extremos no
        # queden pegadas al borde, y un minimo de 8 h para que un dia corto no
        # se dibuje estirado y parezca otra cosa.
        lo = max(0.0, lo - 0.5)
        hi = min(24.0, hi + 0.5)
        if hi - lo < 8.0:
            centro = (lo + hi) / 2.0
            lo = max(0.0, centro - 4.0)
            hi = min(24.0, lo + 8.0)
            lo = max(0.0, hi - 8.0)
        return {'dias': dias, 'ventana': [round(lo, 2), round(hi, 2)]}

    @api.model
    def _franjas_jornada(self, ajustes, emp):
        """Las franjas de trabajo por dia ISO, para dibujar la jornada de fondo."""
        conf = ajustes.schedule_for(emp)
        if not conf.get('enabled'):
            return {}
        crudo = conf.get('intervals') or {}
        salida = {}
        for k, spans in crudo.items():
            try:
                salida[int(k)] = [(float(a), float(b)) for a, b in spans]
            except (TypeError, ValueError):
                continue
        if salida:
            return salida
        a, b = float(conf.get('from') or 0.0), float(conf.get('to') or 24.0)
        return {int(d): [(a, b)] for d in (conf.get('days') or [])}

    @api.model
    def _una_cinta(self, emp, dia, zona, jornada, eventos, huecos, franjas):
        ini_utc, fin_utc = self._limites_utc(dia, zona)
        evs = eventos.filtered(lambda e: ini_utc <= e.at <= fin_utc)
        hcs = huecos.filtered(lambda h: h.start <= fin_utc and h.stop >= ini_utc)

        primero = jornada.first_signal if jornada else False
        ultimo = jornada.last_signal if jornada else False
        h_ini = self._hora_local(primero, zona, dia) if primero else None
        h_fin = self._hora_local(ultimo, zona, dia) if ultimo else None

        segmentos = []
        if h_ini is not None and h_fin is not None and h_fin > h_ini:
            # Los huecos son los tramos CERRADOS; lo que queda entre ellos es
            # presencia. Se construye por complemento y no al reves porque de
            # los huecos se sabe la hora exacta, y de la actividad no.
            cerrados = []
            for h in hcs:
                a = max(h_ini, self._hora_local(h.start, zona, dia))
                b = min(h_fin, self._hora_local(h.stop, zona, dia))
                if b <= a:
                    continue
                cerrados.append({'a': a, 'b': b, 'k': self._clase_hueco(h, evs),
                                 'motivo': h.reason or '', 'estado': h.state,
                                 'id': h.id})
            cerrados.sort(key=lambda c: c['a'])
            cursor = h_ini
            for c in cerrados:
                if c['a'] > cursor:
                    segmentos.append({'a': cursor, 'b': c['a'], 'k': 'activo'})
                segmentos.append(c)
                cursor = max(cursor, c['b'])
            if cursor < h_fin:
                segmentos.append({'a': cursor, 'b': h_fin, 'k': 'activo'})

            # Las llamadas van ENCIMA de la actividad, no en su lugar: estar en
            # una junta es trabajo, y la cinta tiene que decirlo con el mismo
            # peso visual que el teclado, no con una nota al pie.
            for a, b in self._tramos_llamada(evs, zona, dia, h_ini, h_fin):
                segmentos.append({'a': a, 'b': b, 'k': 'llamada'})

        marcas = [{
            'h': round(self._hora_local(e.at, zona, dia), 3),
            'k': e.kind,
            'fuente': e.source,
            'texto': self._texto_marca(e, zona),
        } for e in evs if e.kind not in ('llamada_inicio', 'llamada_fin')]

        return {
            'employee_id': emp.id,
            'employee': emp.name,
            'date': fields.Date.to_string(dia),
            'dow': dia.isoweekday(),
            'laborable': bool(franjas.get(dia.isoweekday())) if franjas else True,
            'shift': [[round(a, 3), round(b, 3)]
                      for a, b in franjas.get(dia.isoweekday(), [])],
            'first': round(h_ini, 3) if h_ini is not None else None,
            'last': round(h_fin, 3) if h_fin is not None else None,
            'first_kind': (jornada.first_kind if jornada else '') or '',
            'last_kind': (jornada.last_kind if jornada else '') or '',
            'state': (jornada.state if jornada else 'sin_dato'),
            'active': round(jornada.active_hours, 3) if jornada else 0.0,
            'in_shift': round(jornada.in_shift_hours, 3) if jornada else 0.0,
            'off_shift': round(jornada.off_shift_hours, 3) if jornada else 0.0,
            'call': round(jornada.call_hours, 3) if jornada else 0.0,
            'idle': round(jornada.idle_hours, 3) if jornada else 0.0,
            'expected': round(jornada.expected_hours, 3) if jornada else 0.0,
            'unexplained': jornada.unexplained_minutes if jornada else 0,
            'segments': [{'a': round(s['a'], 3), 'b': round(s['b'], 3),
                          'k': s['k'], 'motivo': s.get('motivo', ''),
                          'estado': s.get('estado', ''), 'id': s.get('id', 0)}
                         for s in segmentos if s['b'] > s['a']],
            'pins': marcas,
        }

    @api.model
    def _clase_hueco(self, hueco, eventos):
        """De que fue el hueco. 'offline' se resuelve con el evento que cae DENTRO.

        Si ningun evento lo explica, se queda en 'sin_explicar' a proposito: una
        explicacion aproximada seria peor que decir que no se sabe, porque
        taparia justo el caso que hay que mirar.
        """
        if hueco.kind == 'locked':
            return 'bloqueado'
        if hueco.kind == 'idle':
            return 'sin_input'
        for e in eventos:
            if hueco.start <= e.at <= hueco.stop and e.kind in EXPLICA:
                return EXPLICA[e.kind]
        return 'sin_explicar'

    @api.model
    def _tramos_llamada(self, eventos, zona, dia, h_ini, h_fin):
        """Pares inicio/fin de llamada, acotados al dia dibujado."""
        tramos = []
        abierto = None
        for e in eventos:
            if e.kind == 'llamada_inicio' and abierto is None:
                abierto = self._hora_local(e.at, zona, dia)
            elif e.kind == 'llamada_fin' and abierto is not None:
                tramos.append((abierto, self._hora_local(e.at, zona, dia)))
                abierto = None
        if abierto is not None:
            # Llamada sin cierre: se dibuja hasta la ultima senal del dia, no
            # hasta la medianoche, que inventaria horas de junta.
            tramos.append((abierto, h_fin))
        return [(max(h_ini, a), min(h_fin, b)) for a, b in tramos
                if min(h_fin, b) > max(h_ini, a)]

    @api.model
    def _texto_marca(self, evento, zona):
        etiquetas = dict(self.env['foco.event']._fields['kind'].selection)
        hora = fields.Datetime.context_timestamp(evento, evento.at).strftime('%H:%M')
        partes = ['%s %s' % (hora, etiquetas.get(evento.kind, evento.kind))]
        if evento.os_word:
            partes.append('"%s"' % evento.os_word)
        if evento.process:
            partes.append(evento.process)
        partes.append('Registro de Windows' if evento.source == 'os' else 'Agente')
        return ' - '.join(partes)

    # ---------------------------------------------------------------- cron
    @api.model
    def _cron_rebuild(self, dias_atras=2):
        """Rehace los ultimos dias por si algo llego tarde.

        Dos dias y no uno porque un envio puede cruzar la medianoche y cerrar
        ayer con datos de hoy.
        """
        hoy = fields.Date.context_today(self)
        fechas = [hoy - timedelta(days=i) for i in range(dias_atras + 1)]
        empleados = self.env['foco.computer'].sudo().search(
            [('employee_id', '!=', False)]).mapped('employee_id')
        if empleados:
            self.sudo().rebuild(empleados, fechas)
        _logger.info('Foco: jornadas recalculadas para %s personas y %s dias',
                     len(empleados), len(fechas))
        return True
