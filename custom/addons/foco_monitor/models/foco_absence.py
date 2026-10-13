import json
import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# --------------------------------------------------- revision por IA
# La IA lee la justificacion que escribio el empleado y decide si EXPLICA la
# ausencia o si es vaga y hay que revisarla con la persona. Automatica (cron),
# sin boton, con la llave de OpenAI que vive en Odoo. Nace de que la gente
# justifica con "Otro" y una nota de "." o basura para saltarse la regla de
# "Otro exige texto" (que solo mira que NO este vacio).
# La IA hace DOS cosas con la justificacion: (A) dice si el texto da alguna
# razon (veredicto) y (B) la ordena en una categoria medible. Lo que decide si
# algo "es creible" NO lo adivina el modelo: lo mide el sistema con el dato (si
# el hueco cayo dentro o fuera de la jornada). Asi no hay falsos positivos.
# Criterio del 11-oct-2026 (decision del dueno y del usuario): la justificacion
# se la lee el PATRON y tiene que traer la informacion suficiente para entender
# que paso sin preguntar. "Trabajo" o "Reunion" a secas ya no alcanzan: la IA
# devuelve el periodo con UNA pregunta puntual ("Trabajo, en que proyecto?").
# Antes (8-oct) bastaba cualquier palabra con sentido; con eso el registro 562
# de Teodolo paso de "." a "Trabajo" y la IA lo acepto escribiendo ella misma
# "sin dar mas detalle de la tarea".
SISTEMA_JUST = (
    "Eres quien revisa las justificaciones de ausencia de los empleados de {contexto} "
    "El empleado eligio el motivo 'Otro' y escribio un TEXTO LIBRE para explicar por que su "
    "computadora estuvo un rato sin actividad. Te doy ese texto, cuanto duro, a que hora y "
    "cuantos minutos del hueco caian DENTRO de su jornada. La justificacion se la va a leer el "
    "patron: tiene que darle la informacion suficiente para entender QUE paso sin tener que "
    "preguntar. Haz tres cosas:\n"
    "(A) VEREDICTO sobre el texto:\n"
    " - 'adecuada' si el patron entiende que hizo la persona con el detalle minimo que ese motivo "
    "necesita: una actividad con su objeto (que proyecto, cliente, pedido, maquina o tarea), una "
    "reunion o llamada con quien o de que, un lugar con para que, o una razon que se explica sola "
    "(bano, comida, cita medica, tramite en el banco, permiso, apoyo a un area diciendo el tema). "
    "Puede ser breve, informal o en mayusculas. 'fuera de horario laboral' y 'sin actividades "
    "asignadas' SON adecuadas: afirman algo que el sistema verifica con el dato.\n"
    " - 'insuficiente' si da una razon pero le FALTA el dato que el patron necesita: 'trabajo', "
    "'trabajando', 'reunion', 'junta', 'taller', 'proveedor', 'pendientes', 'apoyo', 'llamada', "
    "'cliente', 'almacen', 'revisando' sin decir en que, con quien, de que o para que.\n"
    " - 'vaga' cuando el texto no aporta NADA: vacio, un punto, comas, 'x', 'asdf', letras o "
    "numeros sueltos, o repetir 'otro'/'na'/'nada'.\n"
    " - 'indeterminada' si de verdad no puedes decidir.\n"
    "(B) PREGUNTA: si el veredicto es 'insuficiente' o 'vaga', escribe UNA pregunta corta y directa "
    "en espanol de Mexico, tuteando, que pida exactamente lo que falta retomando su palabra: "
    "'Trabajo, en que proyecto o tarea?', 'Reunion, con quien y de que?', 'Taller, a que fuiste?'. "
    "Maximo 15 palabras, sin regano. Si el veredicto es 'adecuada' o 'indeterminada', cadena vacia.\n"
    "NO castigues que alguien diga que estaba fuera de su horario: si eso es cierto o no lo "
    "verifica el SISTEMA con el dato de la jornada, no tu. Tu solo dices si el texto trae la "
    "informacion suficiente.\n"
    "(C) CATEGORIA: elige de la lista del esquema la que mejor describa el texto. Si dice que "
    "estaba fuera de horario, de fin de semana, dormido o en casa, usa 'fuera_horario'. Si no "
    "encaja en ninguna, 'otra'.\n"
    "'motivo' RESUME el texto en maximo 12 palabras, sin nombres de personas."
)
# Motivos del catalogo que explican la ausencia por SI SOLOS: si el empleado
# eligio uno de estos (no "Otro"), no hay nada que revisar ni se llama a la IA.
MOTIVOS_EXPLICAN = {'comida', 'medico', 'escuela', 'permiso', 'tramite', 'personal'}
# Veredicto del texto. 'sin_relacion' queda solo por compatibilidad con filas
# viejas (ya no se asigna ni se marca a revisar): ahora lo que no es creible se
# decide por el dato, no por el texto.
VEREDICTOS_JUST = [
    ('adecuada', 'Justificacion adecuada'),
    ('insuficiente', 'Le falta informacion'),
    ('vaga', 'Vaga o sin explicacion'),
    ('sin_relacion', 'No parece razon de trabajo (historico)'),
    ('indeterminada', 'Indeterminada'),
]
# Categorias medibles. Las seis primeras son las del catalogo (un motivo elegido
# cae directo en la suya). El catalogo puede crecer desde el dato real.
CATEGORIAS_JUST = [
    ('comida', 'Comida'),
    ('medico', 'Cita medica'),
    ('escuela', 'Escuela'),
    ('permiso', 'Permiso'),
    ('tramite', 'Tramite'),
    ('personal', 'Asunto personal'),
    ('pausa', 'Pausa breve'),
    ('apoyo_otra_area', 'Apoyo a otra area'),
    ('reunion', 'Reunion o junta'),
    ('atencion_cliente', 'Atencion a cliente o visita'),
    ('capacitacion', 'Capacitacion'),
    ('fuera_horario', 'Declaro fuera de su horario'),
    ('sin_tarea', 'Sin tarea asignada'),
    ('otra', 'Otra'),
]
ESQUEMA_JUST = {
    'name': 'revision_justificacion', 'strict': True,
    'schema': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'veredicto': {'type': 'string', 'enum': ['adecuada', 'insuficiente', 'vaga', 'indeterminada']},
            'pregunta': {'type': 'string'},
            'categoria': {'type': 'string', 'enum': [c for c, _ in CATEGORIAS_JUST]},
            'motivo': {'type': 'string'},
            'confianza': {'type': 'number'},
        },
        'required': ['veredicto', 'pregunta', 'categoria', 'motivo', 'confianza'],
    },
}
# Veredictos que DEVUELVEN el periodo al empleado, con la pregunta de la IA.
VEREDICTOS_DEVUELVEN = ('vaga', 'insuficiente')
AI_MAX_INTENTOS = 4
AI_DIAS_ATRAS = 45
# Menos de esto DENTRO de la jornada = el hueco cae fuera del turno: no hay nada
# que justificar. Es un hecho medido (expected_seconds), no una conjetura.
OFF_SHIFT_SEGUNDOS = 60
# Rebote de justificaciones vagas al empleado (enforcement, 9-oct-2026): cuando
# la IA dice que el texto NO explica nada, en vez de solo dejarlo para el admin
# se REABRE el periodo para que lo rehaga (la ventana del agente vuelve con un
# aviso). Tope de seguridad: tras estos rebotes se deja de reabrir y queda solo
# para revision del admin, para no dejar un equipo atascado si la IA se equivoca.
REBOTES_MAX = 5

REASONS = [
    ('comida', 'Comida'),
    ('medico', 'Cita medica'),
    ('escuela', 'Escuela'),
    ('permiso', 'Permiso'),
    ('tramite', 'Tramite'),
    ('personal', 'Asunto personal'),
    ('otro', 'Otro'),
]

KINDS = [
    ('idle', 'Sin actividad'),
    ('locked', 'Equipo bloqueado'),
    ('offline', 'Equipo apagado'),
]

# Se le muestran al empleado como DATO NEUTRO que le ayuda a recordar,
# nunca como reclamo.
KIND_FRASE = {
    'idle': 'No hubo actividad en el equipo',
    'locked': 'El equipo estuvo bloqueado',
    'offline': 'El equipo estuvo apagado',
}

DIAS = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
         'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']

# Si dentro del hueco se esperaba menos que esto, el calendario ya lo explica.
MIN_EXPECTED_SECS = 300


class FocoAbsence(models.Model):
    _name = 'foco.absence'
    _description = 'Periodo sin actividad'
    _order = 'start desc'

    _periodo_uniq = models.Constraint(
        'unique(computer_id, start)',
        'Ese periodo ya estaba registrado para el equipo.')

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True,
        ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', string='Empleado',
        store=True, index=True)
    start = fields.Datetime(string='Inicio', required=True, index=True)
    stop = fields.Datetime(string='Fin', required=True)
    duration = fields.Float(
        string='Duracion (h)', compute='_compute_duration', store=True)
    kind = fields.Selection(KINDS, string='Tipo', default='idle')
    expected_seconds = fields.Float(
        string='Jornada esperada dentro del periodo (s)', readonly=True,
        help='Cuanto de este periodo caia dentro de la jornada del empleado. '
             'Si es casi cero, el calendario ya lo explica.')
    reason = fields.Selection(REASONS, string='Motivo')
    note = fields.Char(string='Nota')
    # Regla de los 90 minutos (11-oct-2026, decision del dueno): cada persona
    # tiene al dia una bolsa de minutos justificables sin que nadie decida (90,
    # fijos para todos). Lo que justifica y la IA acepta se carga a esa bolsa en
    # el orden en que lo justifica. Cuando la bolsa no alcanza, el periodo se
    # PARTE (lo que cabe queda justificado; el resto espera al admin) y cuando
    # se agota, todo lo que justifique despues queda "Por decidir" con Aceptar /
    # Rechazar. Solo lo justificado consume la bolsa: las apps neutras o de
    # distraccion no la tocan. Lo justificado antes de la regla sigue contando
    # tal cual (cubierta_por vacio).
    state = fields.Selection(
        [('auto', 'Explicado por vacaciones'),
         ('pendiente', 'Por justificar'),
         ('justificada', 'Justificada'),
         ('por_decidir', 'Por decidir (admin)'),
         ('rechazada', 'Rechazada por el admin')],
        string='Estado', default='pendiente', index=True, required=True)
    cubierta_por = fields.Selection(
        [('gracia', 'De sus 90 min'),
         ('admin', 'Aceptada por el admin')],
        string='Cuenta por', readonly=True, index=True,
        help='Por que esta justificacion cuenta como cubierta: cabe en los minutos '
             'justificables del dia, o un administrador la acepto. Vacio en lo '
             'justificado antes de la regla (cuenta tal cual).')
    gracia_min = fields.Integer(
        string='De sus 90 (min)', readonly=True, default=0,
        help='Minutos de este periodo cargados a la bolsa justificable del dia.')
    gracia_restante_min = fields.Integer(
        string='Le quedan (min)', readonly=True, default=0,
        help='Minutos justificables que le quedaban ese dia despues de este periodo.')
    origen_id = fields.Many2one(
        'foco.absence', string='Partido de', readonly=True, index=True, ondelete='set null',
        help='Este periodo es el resto de uno que se partio porque la bolsa del dia '
             'no alcanzaba para cubrirlo completo.')
    decidido_por = fields.Many2one('res.users', string='Decidio', readonly=True)
    decidido_at = fields.Datetime(string='Decidido el', readonly=True)
    decision_nota = fields.Char(string='Nota de la decision')
    notified = fields.Boolean(
        string='Avisado por correo', default=False, readonly=True,
        help='Se avisa UNA vez. Si el empleado no contesta, el periodo queda '
             'visible para RR.HH., pero no se le insiste todos los dias.')
    answered_at = fields.Datetime(string='Contestado', readonly=True)
    answered_via = fields.Selection(
        [('agente', 'Ventana del agente'),
         ('web', 'Liga por correo'),
         ('backend', 'Odoo')], string='Contestado desde', readonly=True)

    # ---- revision por IA de la justificacion (automatica, sin boton) --------
    ai_estado = fields.Selection(
        [('sin_analizar', 'Sin analizar'), ('analizado', 'Analizado'), ('error', 'Error')],
        string='Analisis IA', index=True,
        help='La IA lee la justificacion y decide si explica la ausencia o si es '
             'vaga. Corre sola en un proceso periodico, no hay boton.')
    ai_veredicto = fields.Selection(VEREDICTOS_JUST, string='Veredicto IA')
    ai_categoria = fields.Selection(
        CATEGORIAS_JUST, string='Categoria IA', index=True,
        help='En que cae la justificacion, para poder medirla: minutos por '
             'categoria, por persona y por area. La asigna la IA (o el motivo del '
             'catalogo si se eligio uno).')
    ai_requiere_revision = fields.Boolean(
        string='A revisar', index=True,
        help='La IA la marco como VAGA (no da ninguna razon: vacio, un punto, '
             'basura) o INSUFICIENTE (da una razon sin el dato que el patron '
             'necesita: "trabajo", "reunion"). Es lo que llega a "Periodos a '
             'revisar".')
    ai_incoherente = fields.Boolean(
        string='No coincide con su horario', index=True,
        help='El empleado declaro que estaba fuera de su horario, pero el hueco '
             'cae DENTRO de su jornada configurada. Es un DATO medido, no una '
             'acusacion: puede ser una evasion o que el horario configurado no sea '
             'el real (lo afina la jornada por empleado). No entra a "a revisar".')
    ai_motivo = fields.Char(string='Que vio la IA')
    ai_pregunta = fields.Char(
        string='Pregunta de la IA', readonly=True,
        help='Lo que la IA le pregunto al empleado al devolverle la justificacion '
             'por falta de informacion ("Trabajo, en que proyecto o tarea?").')
    ai_confianza = fields.Float(string='Confianza IA', digits=(3, 2))
    ai_modelo = fields.Char(string='Modelo IA')
    ai_tokens = fields.Integer(string='Tokens IA')
    ai_at = fields.Datetime(string='Analizada el', readonly=True)
    ai_intentos = fields.Integer(string='Intentos IA', default=0)
    ai_error = fields.Char(string='Ultimo error IA')

    # ---- rebote al empleado cuando la IA la marca vaga (enforcement) --------
    rebote_pendiente = fields.Boolean(
        string='Devuelta al empleado', index=True,
        help='La IA marco su justificacion como VAGA y el periodo se REABRIO para '
             'que la rehaga: la ventana del agente vuelve con un aviso. Se apaga en '
             'cuanto el empleado contesta de nuevo.')
    rebotes = fields.Integer(
        string='Veces devuelta', default=0, readonly=True,
        help='Cuantas veces se le devolvio por justificar de forma vaga. Tras '
             'el tope deja de devolverse y queda solo para el admin.')
    nota_rechazada = fields.Char(
        string='Ultimo texto rechazado', readonly=True,
        help='Lo que el empleado habia escrito y la IA considero que no explica '
             'nada. Se guarda para no perder el historial al reabrir.')
    # La conversacion COMPLETA, vuelta por vuelta. Los dos campos de arriba solo
    # guardan la ultima vuelta -cada rebote pisa al anterior-, y con eso el admin
    # no puede decidir nada cuando el periodo le llega tras cinco idas y vueltas.
    msg_ids = fields.One2many(
        'foco.absence.msg', 'absence_id', string='Conversacion', readonly=True)
    msg_count = fields.Integer(string='Mensajes', compute='_compute_msg_count')

    # ---- revision humana de lo que la IA marco ------------------------------
    review_visto = fields.Boolean(
        string='Revisado', index=True,
        help='Un administrador ya reviso esta justificacion marcada por la IA. '
             'Sale de la lista de "Periodos a revisar".')
    review_por = fields.Many2one('res.users', string='Revisado por', readonly=True)
    review_at = fields.Datetime(string='Revisado el', readonly=True)
    review_nota = fields.Char(string='Nota de la revision')

    @api.depends('msg_ids')
    def _compute_msg_count(self):
        for rec in self:
            rec.msg_count = len(rec.msg_ids)

    # ------------------------------------------------- conversacion
    def _registrar_msg(self, quien, texto, **extra):
        """Deja un mensaje en la conversacion del periodo. Nunca levanta: el
        registro es para poder decidir despues, no una parte critica del
        flujo -si falla, la justificacion igual se guarda-."""
        self.ensure_one()
        texto = (texto or '').strip()
        if not texto:
            return False
        vals = {'absence_id': self.id, 'quien': quien, 'texto': texto[:1000],
                'vuelta': self.rebotes, 'at': fields.Datetime.now()}
        vals.update(extra)
        try:
            return self.env['foco.absence.msg'].sudo().create(vals)
        except Exception as e:          # noqa: BLE001
            _logger.warning('foco.absence %s: no se pudo registrar el mensaje (%s)',
                            self.id, e)
            return False

    def _pedir_analisis_ia(self):
        """Despierta al proceso que juzga, en vez de esperar su turno.

        El cron corre cada 15 min porque nacio como un reporte; desde que el
        veredicto DEVUELVE el periodo al empleado, esos minutos son una persona
        esperando. `_trigger` es el mecanismo de Odoo para pedirle a un cron que
        corra ya: deja el analisis fuera de la peticion del agente (que se
        libera al instante, como hoy) y conserva los reintentos del cron, que
        sigue barriendo cada 15 min lo que el disparo no haya alcanzado."""
        cron = self.env.ref('foco_monitor.cron_foco_absence_ia',
                            raise_if_not_found=False)
        if not cron:
            return False
        try:
            cron.sudo()._trigger()
            return True
        except Exception as e:          # noqa: BLE001
            _logger.warning('no se pudo disparar la revision por IA (%s)', e)
            return False

    @api.depends('start', 'stop')
    def _compute_duration(self):
        for rec in self:
            if rec.start and rec.stop and rec.stop > rec.start:
                rec.duration = (rec.stop - rec.start).total_seconds() / 3600.0
            else:
                rec.duration = 0.0

    # ------------------------------------------------------------- ingesta
    @api.model
    def _to_utc(self, value, employee):
        """El agente manda hora LOCAL sin zona; Odoo guarda UTC."""
        if not value:
            return False
        try:
            naive = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return False
        if naive.tzinfo is not None:
            return naive.astimezone(pytz.UTC).replace(tzinfo=None)
        tz = self.env['foco.settings']._tzinfo_for(employee)
        return tz.localize(naive).astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def _covered_by_leave(self, employee, start, stop):
        """Permisos aprobados. OJO: hr_holidays puede NO estar instalado."""
        if not employee or 'hr.leave' not in self.env:
            return False
        try:
            return bool(self.env['hr.leave'].sudo().search_count([
                ('employee_id', '=', employee.id),
                ('state', '=', 'validate'),
                ('date_from', '<=', stop),
                ('date_to', '>=', start),
            ]))
        except Exception:
            return False

    @api.model
    def _cierre_de_jornada(self, employee, start):
        """UTC del cierre de la jornada del dia LOCAL en que empieza el hueco.

        Sirve para recortar lo que se guarda. Un hueco que empieza a las 17:00 y
        termina a las 19:00 es, para efectos de justificar, de 17:00 a 17:30: el
        resto es tiempo de nadie. Sin este recorte, al dejar de explicarse los
        huecos por calendario, a la gente le saldrian ventanas pidiendole que
        justifique catorce horas por haber dejado la computadora prendida.
        """
        if not employee:
            return None
        settings = self.env['foco.settings'].sudo()
        spans = settings.workspan_intervals(employee.resource_calendar_id)
        if not spans:
            return None
        tz = settings._tzinfo_for(employee)
        local = pytz.UTC.localize(start).astimezone(tz)
        tramos = spans.get(local.isoweekday())
        if not tramos:
            return None
        fin_h = max(b for _, b in tramos)
        base = tz.localize(datetime.combine(local.date(), time(0, 0)))
        return (base + timedelta(hours=fin_h)).astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def record_gaps(self, computer, gaps):
        """Guarda los huecos que manda el agente. Idempotente por (equipo, inicio).

        Tres destinos (decision del dueno, 12-oct-2026):

        - **De vacaciones** (permiso aprobado): entra como 'auto', visible pero
          sin preguntarle a nadie. Es el UNICO caso que queda explicado solo.
        - **Fuera de la jornada** (la noche, el domingo, antes de abrir o
          despues de cerrar): NO SE REGISTRA. Antes entraba como 'auto' y
          llenaba la lista de filas de 850 a 2,500 minutos -la computadora que
          se queda prendida- que nadie va a justificar nunca.
        - **Todo lo demas**: 'pendiente'. Y la jornada se mide de CORRIDO, con
          la comida dentro: quien se levanta a las 12:59 y vuelve a las 13:34
          ahora recibe la pregunta. Antes la franja de comida lo explicaba y el
          hueco no le costaba nada a nadie.
        """
        if not computer or not gaps:
            return 0
        settings = self.env['foco.settings'].sudo().get_settings()
        employee = computer.employee_id
        kinds = dict(KINDS)
        stored = 0
        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            start = self._to_utc(gap.get('start'), employee)
            stop = self._to_utc(gap.get('end'), employee)
            if not start or not stop or stop <= start:
                continue
            if self.sudo().search_count([('computer_id', '=', computer.id),
                                         ('start', '=', start)]):
                stored += 1                 # reenviado: ya estaba, no se duplica
                continue
            # Con el checador: lo que pasa despues de checar salida (o antes de
            # checar entrada) no cuenta como jornada esperada y no se pregunta.
            #
            # Para DECIDIR si se pregunta, la jornada va de corrido (la comida
            # cuenta). Para MEDIR, se guarda la jornada real -sin comida-, que
            # es la que gasta la bolsa de 90 min y la que suma el tablero: asi,
            # un hueco que cae entero en la comida se pregunta pero no le cuesta
            # minutos a nadie, y estirarse hasta las 14:20 si cuesta.
            de_vacaciones = self._covered_by_leave(employee, start, stop)
            fin = stop
            if not de_vacaciones:
                # Se recorta al cierre de la jornada del dia en que empezo: lo
                # que sigue ya no es tiempo que nadie deba explicar. El INICIO
                # no se toca nunca, que es la llave contra duplicados.
                cierre = self._cierre_de_jornada(employee, start)
                if cierre:
                    if start >= cierre:
                        # Empezo despues de cerrar. Lo que alcance de la manana
                        # siguiente es asunto de PUNTUALIDAD (lo mide la
                        # adherencia), no un hueco que justificar: si no, por
                        # llegar 08:13 se le pediria explicar catorce horas.
                        stored += 1
                        continue
                    fin = min(fin, cierre)
                jornada = settings.expected_seconds(employee, start, fin,
                                                    con_asistencia=True, continuo=True)
                if jornada < MIN_EXPECTED_SECS:
                    stored += 1         # fuera de la jornada: ni se registra
                    continue
            expected = settings.expected_seconds(employee, start, fin, con_asistencia=True)
            self.sudo().create({
                'computer_id': computer.id,
                'start': start,
                'stop': fin,
                'kind': gap.get('kind') if gap.get('kind') in kinds else 'idle',
                'expected_seconds': expected,
                'state': 'auto' if de_vacaciones else 'pendiente',
            })
            stored += 1
        return stored

    # ------------------------------------------------------------- consulta
    @api.model
    def pending_for_employee(self, employee, limit=20):
        if not employee:
            return self.browse()
        return self.sudo().search(
            [('employee_id', '=', employee.id), ('state', '=', 'pendiente')],
            order='start desc', limit=limit)

    def _suggested_reason(self):
        """Si el periodo traslapa la comida del calendario, se presugiere.

        Desde el 12-oct-2026 la comida ya NO explica el hueco sola -se pregunta
        igual-, asi que presugerir el motivo deja de ser un detalle: es lo que
        hace que contestar sea un clic. Mira tambien el hueco entre el tramo de
        la manana y el de la tarde, que es como esta puesta la comida en FERBA
        (sin linea `day_period='lunch'`).
        """
        self.ensure_one()
        calendar = self.employee_id.resource_calendar_id
        lunch = self.env['foco.settings'].break_intervals(calendar)
        if not lunch or not self.start or not self.stop:
            return False
        tz = self.env['foco.settings']._tzinfo_for(self.employee_id)
        ini = pytz.UTC.localize(self.start).astimezone(tz)
        fin = pytz.UTC.localize(self.stop).astimezone(tz)
        for spans in (lunch.get(ini.isoweekday()) or []), (lunch.get(fin.isoweekday()) or []):
            for h_from, h_to in spans:
                a = ini.hour + ini.minute / 60.0
                b = fin.hour + fin.minute / 60.0 if fin.date() == ini.date() else 24.0
                if min(b, h_to) > max(a, h_from):
                    return 'comida'
        return False

    def payload(self):
        """Forma que consumen la ventana del agente y la pagina publica.

        Las etiquetas vienen ya armadas para que ni la ventana ni la pagina
        tengan que saber de formatos ni de zonas horarias.
        """
        tz_model = self.env['foco.settings']
        # El aviso del rebote viaja desde el servidor (no incrustado en el .exe),
        # para poder ajustar la frase sin recompilar el agente.
        bmsg = (tz_model.sudo().get_settings().justify_rebote_msg or '').strip()
        out = []
        for rec in self:
            tz = tz_model._tzinfo_for(rec.employee_id)
            ini = pytz.UTC.localize(rec.start).astimezone(tz)
            fin = pytz.UTC.localize(rec.stop).astimezone(tz)
            mins = int(round(rec.duration * 60))
            horas, resto = divmod(mins, 60)
            if horas and resto:
                dur = '%d h %d min' % (horas, resto)
            elif horas:
                dur = '%d h' % horas
            else:
                dur = '%d min' % resto
            out.append({
                'id': rec.id,
                'date': ini.strftime('%Y-%m-%d'),
                'start': ini.strftime('%H:%M'),
                'end': fin.strftime('%H:%M'),
                'minutes': mins,
                'dur_label': dur,
                'when_label': '%s %d de %s' % (
                    DIAS[ini.weekday()], ini.day, MESES[ini.month - 1]),
                'kind': rec.kind,
                'kind_label': KIND_FRASE.get(rec.kind, ''),
                'suggested': rec._suggested_reason() or '',
                # Rebote: la ventana del agente pinta este aviso cuando no esta
                # vacio (periodo devuelto por justificacion vaga o insuficiente).
                # Va la PREGUNTA puntual de la IA ("Trabajo, en que proyecto?") y,
                # si no la hay, el aviso general de Ajustes. Si el .exe no conoce
                # la clave, la ignora: el rebote degrada a re-preguntar.
                'bounced': bool(rec.rebote_pendiente),
                'bounce_msg': ((rec.ai_pregunta or '').strip() or bmsg) if rec.rebote_pendiente else '',
            })
        return out

    # ------------------------------------------------------------- tablero
    @api.model
    def dashboard_summary(self, date_from, date_to):
        """Por empleado: jornada esperada, justificado y pendientes del rango.

        Es lo que permite al tablero mostrar la descomposicion honesta
        (esperado - medido - justificado = sin explicar) en vez de un numero
        suelto que castiga a quien tuvo una cita medica.
        """
        Settings = self.env['foco.settings'].sudo()
        settings = Settings.get_settings()
        d_ini = fields.Date.to_date(date_from)
        d_fin = fields.Date.to_date(date_to)
        if not d_ini or not d_fin:
            return {}
        # Sin sudo: la lista de personas que devuelve este resumen tiene que
        # quedar acotada al alcance de quien pregunta.
        computers = self.env['foco.computer'].search(
            [('employee_id', '!=', False)])
        empleados = computers.mapped('employee_id')
        # Rezago checador -> PC del periodo, por persona: promedio de los dias
        # en que checo entrada y hubo senal en la computadora. Una sola lectura
        # para todos, agrupada en Python.
        lags = {}
        for w in self.env['foco.workday'].sudo().search([
                ('employee_id', 'in', empleados.ids),
                ('date', '>=', d_ini), ('date', '<=', d_fin),
                ('shift_source', '=', 'checador'),
                ('first_signal', '!=', False)]):
            lags.setdefault(w.employee_id.id, []).append(w.check_in_lag_minutes)
        # Dias del periodo en que el empleado checo entrada y nunca cerro salida
        # (8-oct-2026). Es un conteo por persona para la etiqueta de su fila y el
        # KPI del equipo; el recordatorio al propio empleado va por su cron.
        sin_salida = {}
        for emp_x, cuantos in self.env['foco.workday'].sudo()._read_group(
                [('employee_id', 'in', empleados.ids),
                 ('date', '>=', d_ini), ('date', '<=', d_fin),
                 ('check_out_missing', '=', True)],
                ['employee_id'], ['__count']):
            if emp_x:
                sin_salida[emp_x.id] = cuantos
        # Jornada REAL segun el CHECADOR y el extra/faltante contra el horario
        # (la guia). Decision del usuario 8-oct: el horario es una guia; el
        # checador tiene el veredicto final de la columna Jornada, y lo
        # configurado dice cuanto fue EXTRA. El indice NO cambia (sigue contra
        # la guia). Un dia sin cerrar salida no cuenta como extra.
        chk = Settings._checador_jornada(empleados, d_ini, d_fin)
        out = {}
        for employee in empleados:
            tz = Settings._tzinfo_for(employee)
            ini = tz.localize(datetime.combine(d_ini, time(0, 0))) \
                    .astimezone(pytz.UTC).replace(tzinfo=None)
            fin = tz.localize(datetime.combine(d_fin, time(23, 59, 59))) \
                    .astimezone(pytz.UTC).replace(tzinfo=None)
            recs = self.search([('employee_id', '=', employee.id),
                                ('start', '>=', ini), ('start', '<=', fin)])
            v = lags.get(employee.id) or []
            # Lo JUSTIFICADO y lo SIN EXPLICAR cuentan solo la porcion que cae
            # DENTRO de la jornada (expected_seconds), no la duracion cruda.
            # Medido el 8-oct: una ausencia "bloqueado" de 8.45 h justificada de
            # madrugada (00:22-08:49) tenia solo 0.48 h dentro de la jornada;
            # sumar su duracion completa inflaba lo cubierto a 8h53 con 31 min
            # activos y daba 100%. Una comida justificada (dentro de jornada) si
            # cuenta entera; una noche bloqueada justificada, casi nada.
            justi = recs.filtered(lambda a: a.state == 'justificada')
            pend = recs.filtered(lambda a: a.state == 'pendiente')
            decidir = recs.filtered(lambda a: a.state == 'por_decidir')
            out[str(employee.id)] = {
                'expected': settings.expected_seconds(employee, ini, fin) / 3600.0,
                'justified': sum(justi.mapped('expected_seconds')) / 3600.0,
                'pending': len(pend),
                # Regla de los 90 min: justificado que paso de la bolsa del dia y
                # espera Aceptar / Rechazar del admin. No cuenta como cubierto.
                'por_decidir': len(decidir),
                'por_decidir_h': sum(decidir.mapped('expected_seconds')) / 3600.0,
                # "Sin explicar" en horas: porcion de la jornada que cubren los
                # huecos SIN justificar. Antes el tablero lo derivaba restando
                # (esperado - activo - justificado), que a media jornada daba un
                # numero enorme porque el esperado es el dia completo.
                'unexplained_h': sum(pend.mapped('expected_seconds')) / 3600.0,
                # Minutos del checador a la PC: promedio de los dias checados.
                # None cuando no hay ningun dia con checada: el tablero no lo
                # dibuja, en vez de pintar un 0 que afirmaria "llego al instante".
                'lag_min': int(round(sum(v) / len(v))) if v else None,
                # Dias del periodo en que checo entrada y no cerro salida.
                'sin_salida': sin_salida.get(employee.id, 0),
                # Jornada real del checador y extra/faltante vs el horario (guia).
                'usa_checador': bool(chk.get(employee.id, {}).get('usa')),
                'checado_h': round(chk.get(employee.id, {}).get('checado', 0.0), 3),
                'extra_h': round(chk.get(employee.id, {}).get('extra', 0.0), 3),
                'faltante_h': round(chk.get(employee.id, {}).get('faltante', 0.0), 3),
                # Checada ABIERTA (10-oct-2026): hoy sigue checado (se cuenta hasta
                # la ultima senal de su equipo o el fin de su horario, sin extra ni
                # faltante); un dia pasado sin salida no se mide y se dice.
                'checado_abierto': bool(chk.get(employee.id, {}).get('abierta_hoy')),
                'checado_no_medible': int(chk.get(employee.id, {}).get('no_medible', 0)),
                # Pausas sin teclear DENTRO de la jornada: los huecos `idle` que el
                # agente abrio (del umbral en adelante), se hayan justificado o no.
                # Es una sola linea de tiempo. El `fg_idle` de los renglones de uso
                # se encima entre ventanas (medido: 10h25 de idle en 9h24 de
                # presencia) y NO se muestra a nadie.
                'sin_teclear_h': round(sum(recs.filtered(lambda a: a.kind == 'idle')
                                           .mapped('expected_seconds')) / 3600.0, 3),
            }
        return out

    @api.model
    def cubierto_por_dia(self, date_from, date_to):
        """Jornada esperada y justificado por DIA y por persona en el rango, en
        horas. Es lo que hace falta para que el indice sea UNO en todo Foco
        (10-oct-2026): cubierto (productivo + justificado) entre la jornada
        esperada, por dia, por persona y para el equipo, como ya lo calculaban
        la tarjeta, la tabla y el reporte diario. Misma gente y mismo alcance
        que `dashboard_summary`."""
        Settings = self.env['foco.settings'].sudo()
        d_ini = fields.Date.to_date(date_from)
        d_fin = fields.Date.to_date(date_to)
        vacio = {'dias': {}, 'empleados': {}}
        if not d_ini or not d_fin or d_fin < d_ini:
            return vacio
        computers = self.env['foco.computer'].search([('employee_id', '!=', False)])
        empleados = computers.mapped('employee_id')
        dias, por_emp = {}, {}
        for emp in empleados:
            tz = Settings._tzinfo_for(emp)
            ini = tz.localize(datetime.combine(d_ini, time(0, 0))) \
                    .astimezone(pytz.UTC).replace(tzinfo=None)
            fin = tz.localize(datetime.combine(d_fin, time(23, 59, 59))) \
                    .astimezone(pytz.UTC).replace(tzinfo=None)
            justi = {}
            for a in self.search([('employee_id', '=', emp.id), ('state', '=', 'justificada'),
                                  ('start', '>=', ini), ('start', '<=', fin)]):
                dia_local = pytz.UTC.localize(a.start).astimezone(tz).date()
                justi[dia_local] = justi.get(dia_local, 0.0) + (a.expected_seconds or 0.0) / 3600.0
            mio = por_emp[str(emp.id)] = {}
            d = d_ini
            while d <= d_fin:
                a_utc = tz.localize(datetime.combine(d, time(0, 0))) \
                          .astimezone(pytz.UTC).replace(tzinfo=None)
                b_utc = tz.localize(datetime.combine(d, time(23, 59, 59))) \
                          .astimezone(pytz.UTC).replace(tzinfo=None)
                esperado = Settings.expected_seconds(emp, a_utc, b_utc) / 3600.0
                k = fields.Date.to_string(d)
                j = justi.get(d, 0.0)
                mio[k] = {'expected': round(esperado, 4), 'justified': round(j, 4)}
                t = dias.setdefault(k, {'expected': 0.0, 'justified': 0.0})
                t['expected'] += esperado
                t['justified'] += j
                d += timedelta(days=1)
        for t in dias.values():
            t['expected'] = round(t['expected'], 4)
            t['justified'] = round(t['justified'], 4)
        return {'dias': dias, 'empleados': por_emp}

    # ------------------------------------------------------------- correo
    @api.model
    def _cron_digest(self):
        """Un correo por empleado con lo que SIGUE pendiente.

        Es la RED DE SEGURIDAD del doble canal: lo que la ventana del agente ya
        recogio no llega aqui, porque solo se buscan las que siguen en
        'pendiente'. Y se avisa UNA sola vez por periodo, para no insistir.
        """
        pendientes = self.sudo().search([('state', '=', 'pendiente'),
                                         ('notified', '=', False)])
        if not pendientes:
            return
        tpl = self.env.ref('foco_monitor.mail_tpl_absences',
                           raise_if_not_found=False)
        if not tpl:
            return
        enviados = 0
        for employee in pendientes.mapped('employee_id'):
            if not employee or not employee.work_email:
                continue
            try:
                tpl.sudo().send_mail(employee.id, force_send=False)
                pendientes.filtered(
                    lambda a, e=employee: a.employee_id == e).notified = True
                enviados += 1
            except Exception:
                _logger.exception(
                    "no se pudo encolar el aviso de ausencias de %s", employee.name)
        if enviados:
            _logger.info("Foco: avisos de periodos sin actividad encolados: %s",
                         enviados)

    # ------------------------------------------------------------- respuesta
    def answer(self, reason, note=None, via='web'):
        """Registra el motivo. GANA EL PRIMERO: si ya fue contestada no se pisa.

        Es lo que permite tener dos canales (ventana del agente y liga por
        correo) sin que se estorben.
        """
        self.ensure_one()
        if reason not in dict(REASONS):
            return {'ok': False, 'error': 'motivo_invalido'}
        note = (note or '').strip()[:200]
        # «Otro» sin texto no explica nada: es un periodo que sigue sin
        # justificar con otro nombre. La regla vive AQUI y no solo en la
        # ventana y en la pagina: los dos canales la muestran, pero es el
        # servidor el que no la deja pasar (decision del cliente, 25-sep).
        if reason == 'otro' and not note:
            return {'ok': False, 'error': 'nota_requerida'}
        if self.state in ('justificada', 'por_decidir', 'rechazada'):
            return {'ok': False, 'error': 'ya_justificada',
                    'reason': self.reason,
                    'reason_label': dict(REASONS).get(self.reason, '')}
        self.sudo().write({
            'reason': reason,
            'note': note or False,
            'state': 'justificada',
            'answered_at': fields.Datetime.now(),
            'answered_via': via,
            # Recien justificada: la IA la analiza en su proximo ciclo. Nota de
            # "." o basura con motivo "Otro" es justo lo que viene a cazar.
            'ai_estado': 'sin_analizar', 'ai_intentos': 0,
            # Si venia devuelta por vaga, el empleado ya contesto de nuevo: se
            # apaga el aviso hasta que la IA vuelva a juzgar este texto.
            'rebote_pendiente': False, 'ai_pregunta': False,
            # La bolsa de 90 se carga cuando la IA acepte ESTE texto.
            'cubierta_por': False, 'gracia_min': 0, 'gracia_restante_min': 0,
        })
        etiqueta = dict(REASONS).get(reason, '')
        self._registrar_msg('empleado', note or etiqueta,
                            reason=etiqueta, via=dict(self._fields['answered_via'].selection).get(via, via))
        # Que lo juzgue YA, no en el siguiente cuarto de hora: del otro lado hay
        # alguien que va a tener que volver a contestar si el texto no sirve.
        self._pedir_analisis_ia()
        return {'ok': True, 'reason_label': etiqueta}

    def write(self, vals):
        # Si cambia el texto o el motivo de una justificacion ya analizada,
        # la IA la vuelve a mirar: un administrador pudo corregir la nota. Se
        # evita la recursion saltando cuando el propio write es de campos IA.
        reanaliza = (('note' in vals or 'reason' in vals)
                     and 'ai_estado' not in vals and 'ai_veredicto' not in vals)
        res = super().write(vals)
        if reanaliza:
            recolar = False
            for rec in self:
                if rec.state == 'justificada' and rec.ai_estado == 'analizado':
                    super(FocoAbsence, rec).write({'ai_estado': 'sin_analizar', 'ai_intentos': 0})
                    recolar = True
            if recolar:
                self[0]._pedir_analisis_ia()
        return res

    # ------------------------------------------------- revision por IA (cron)
    @api.model
    def _cron_revisar_ia(self, limit=40):
        """Analiza las justificaciones que faltan: nuevas y las que fallaron.

        Automatico, sin boton. Solo justificadas y recientes (ultimos dias):
        las viejas no mueven ninguna decision. Cada una va en su savepoint para
        que un fallo no tumbe al resto."""
        OpenAI = self.env['foco.openai']
        if not OpenAI.configurado():
            return 0
        settings = self.env['foco.settings'].sudo().get_settings()
        desde = fields.Datetime.now() - timedelta(days=AI_DIAS_ATRAS)
        recs = self.sudo().search([
            ('state', '=', 'justificada'),
            ('ai_estado', '!=', 'analizado'),
            ('ai_intentos', '<', AI_MAX_INTENTOS),
            ('start', '>=', desde),
        ], order='start desc', limit=limit)
        contexto = (settings.call_review_context or '').strip()
        n = 0
        for rec in recs:
            try:
                with self.env.cr.savepoint():
                    rec._clasificar_ia(contexto)
                    n += 1
            except Exception as e:
                rec.sudo().write({'ai_estado': 'error', 'ai_intentos': rec.ai_intentos + 1,
                                  'ai_error': str(e)[:200]})
                _logger.warning('foco.absence %s: no se pudo analizar (%s)', rec.id, e)
        return n

    def _clasificar_ia(self, contexto):
        """Una justificacion: decide si el texto da una razon (veredicto), la
        ordena en una categoria medible, y marca si lo declarado NO coincide con
        la jornada medida. Lo que es "creible" se decide con el DATO (dentro o
        fuera de turno), no con una conjetura del modelo: asi no hay falsos
        positivos."""
        self.ensure_one()
        # 1) Motivo del catalogo (Comida, Cita medica, Tramite, Permiso, Escuela,
        #    Asunto personal): el motivo elegido YA explica la ausencia y cae
        #    directo en su categoria. No se revisa ni se gasta una llamada.
        if self.reason in MOTIVOS_EXPLICAN:
            self.sudo().write({
                'ai_estado': 'analizado', 'ai_veredicto': 'adecuada',
                'ai_categoria': self.reason, 'ai_requiere_revision': False,
                'ai_incoherente': False, 'ai_pregunta': False,
                'ai_motivo': 'Motivo del catalogo: %s' % dict(REASONS).get(self.reason, ''),
                'ai_confianza': 1.0, 'ai_modelo': False, 'ai_tokens': 0,
                'ai_at': fields.Datetime.now(), 'ai_error': False,
            })
            self._aplicar_gracia()
            return
        # 2) El hueco cae FUERA de su jornada (hecho medido, no conjetura): no hay
        #    nada que justificar fuera de hora. Se acepta sin gastar IA y sin
        #    marcar. Cero falsos positivos: lo dice el dato, no el texto.
        if (self.expected_seconds or 0.0) < OFF_SHIFT_SEGUNDOS:
            # Desde el 12-oct la comida SI se pregunta, pero no cuesta minutos:
            # cae aqui, con 0 de jornada esperada, y se acepta sin gastar IA ni
            # bolsa. Se dice con todas sus letras para que el admin no lea
            # "fuera de su jornada" en un hueco de las 13:10.
            en_comida = self._suggested_reason() == 'comida'
            self.sudo().write({
                'ai_estado': 'analizado', 'ai_veredicto': 'adecuada',
                'ai_categoria': 'comida' if en_comida else 'fuera_horario',
                'ai_requiere_revision': False,
                'ai_incoherente': False, 'ai_pregunta': False,
                'ai_motivo': ('El hueco cae en su hora de comida' if en_comida
                              else 'El hueco cae fuera de su jornada'),
                'ai_confianza': 1.0, 'ai_modelo': False, 'ai_tokens': 0,
                'ai_at': fields.Datetime.now(), 'ai_error': False,
            })
            self._aplicar_gracia()
            return
        # 3) "Otro" con texto y DENTRO de la jornada: lo lee la IA.
        tz = self.env['foco.settings'].sudo()._tzinfo_for(self.employee_id)
        ini = pytz.UTC.localize(self.start).astimezone(tz) if self.start else None
        mins = int(round((self.duration or 0.0) * 60))
        dur = ('%d h %d min' % (mins // 60, mins % 60)) if mins >= 60 else ('%d min' % mins)
        en_turno_min = int(round((self.expected_seconds or 0.0) / 60.0))
        ctx = (contexto or '').strip()
        if ctx and not ctx.endswith('.'):
            ctx += '.'
        sistema = SISTEMA_JUST.format(contexto=ctx or 'la empresa.')
        usuario = (
            'MOTIVO ELEGIDO: %s\nTEXTO QUE ESCRIBIO: %s\nDURACION: %s\nHORA LOCAL: %s\n'
            'MINUTOS DEL HUECO DENTRO DE SU JORNADA: %d\nTIPO: %s'
            % (dict(REASONS).get(self.reason, self.reason or '(ninguno)'),
               (self.note or '').strip() or '(vacio)', dur,
               ini.strftime('%H:%M') if ini else '?', en_turno_min,
               dict(KINDS).get(self.kind, self.kind or '')))
        # Si ya se le devolvio, la IA ve que escribio antes: "." y luego
        # "Trabajo" es el caso real que motivo el criterio.
        if self.rebotes:
            usuario += '\nVECES DEVUELTA: %d\nANTES ESCRIBIO (y se le devolvio): %s' % (
                self.rebotes, (self.nota_rechazada or '').strip() or '(vacio)')
        resp = self.env['foco.openai'].chat(
            [{'role': 'system', 'content': sistema},
             {'role': 'user', 'content': usuario}],
            response_format={'type': 'json_schema', 'json_schema': ESQUEMA_JUST},
            # 500, no 200: con 200 el modelo a veces gasta el cupo en un preambulo
            # y corta el JSON (stop_reason max_tokens, texto vacio) -> "sin JSON
            # valido". El JSON real son ~60 tokens; 500 da margen de sobra.
            max_tokens=500)
        try:
            j = json.loads(resp['message'].get('content') or '{}')
        except (ValueError, TypeError):
            j = {}
        ver = (j.get('veredicto') if j.get('veredicto') in ('adecuada', 'insuficiente', 'vaga', 'indeterminada')
               else 'indeterminada')
        cat = j.get('categoria') if j.get('categoria') in dict(CATEGORIAS_JUST) else 'otra'
        pregunta = (j.get('pregunta') or '').strip()[:200]
        # Se DEVUELVE lo vago y lo insuficiente (11-oct): la justificacion se la
        # lee el patron y "Trabajo" a secas no le dice nada. Va con la pregunta
        # puntual de la IA para que la persona complete justo lo que falta.
        requiere = ver in VEREDICTOS_DEVUELVEN
        # Señal MEDIDA (no acusacion): declaro estar fuera de horario, pero este
        # hueco cae dentro de su turno (llegamos aqui con expected_seconds alto).
        # Lo ve el admin aparte; NO entra a "a revisar", porque puede ser que el
        # horario configurado no sea el real (lo afina la jornada por empleado).
        incoherente = (cat == 'fuera_horario')
        try:
            conf = min(max(float(j.get('confianza') or 0.0), 0.0), 1.0)
        except (TypeError, ValueError):
            conf = 0.0
        vals = {
            'ai_estado': 'analizado', 'ai_veredicto': ver,
            'ai_categoria': cat, 'ai_requiere_revision': requiere,
            'ai_incoherente': incoherente,
            'ai_motivo': (j.get('motivo') or '').strip()[:150] or False,
            'ai_pregunta': (pregunta if requiere else '') or False,
            'ai_confianza': conf, 'ai_modelo': resp.get('modelo') or False,
            'ai_tokens': int((resp.get('usage') or {}).get('total_tokens') or 0),
            'ai_at': fields.Datetime.now(), 'ai_error': False,
        }
        # Rebote (enforcement): si la IA dice que el texto NO trae lo que el
        # patron necesita, se DEVUELVE al empleado con la pregunta -se reabre el
        # periodo y la ventana del agente vuelve con ella-, en vez de solo
        # dejarlo para el admin. Gobernado por el interruptor de Ajustes y con
        # tope de seguridad: tras REBOTES_MAX ya no se reabre y queda "Por
        # decidir" del admin (antes seguia contando como justificada: asi
        # contaron 5h17 de "." y "Trabajo" de Teodolo el 9-oct).
        settings = self.env['foco.settings'].sudo().get_settings()
        if requiere and settings.justify_rebote_activo and self.rebotes < REBOTES_MAX:
            vals.update({
                'state': 'pendiente',
                'rebote_pendiente': True,
                'rebotes': self.rebotes + 1,
                'nota_rechazada': (self.note or '').strip()[:200] or False,
                # Que lo nag la VENTANA, no el correo: evita avisar dos veces.
                'notified': True,
                # Lo que se le haya cargado a la bolsa se libera: se carga
                # cuando la IA acepte el texto nuevo.
                'cubierta_por': False, 'gracia_min': 0, 'gracia_restante_min': 0,
            })
        elif requiere:
            vals.update({'state': 'por_decidir',
                         'cubierta_por': False, 'gracia_min': 0, 'gracia_restante_min': 0})
        # El mensaje ANTES del write: asi comparte numero de vuelta con el texto
        # del empleado al que esta contestando (el write incrementa `rebotes`).
        dicho = (vals.get('ai_motivo') or '').strip()
        if requiere:
            cola = ' (ya no se le devuelve: paso a decision del administrador)' \
                if vals.get('state') == 'por_decidir' else ''
            self._registrar_msg(
                'ia', (pregunta or dicho or 'La justificacion no explica la ausencia.') + cola,
                veredicto=dict(VEREDICTOS_JUST).get(ver, ver), confianza=conf)
        else:
            self._registrar_msg(
                'ia', dicho or 'La justificacion explica la ausencia.',
                veredicto=dict(VEREDICTOS_JUST).get(ver, ver), confianza=conf)
        self.sudo().write(vals)
        if not requiere:
            self._aplicar_gracia()

    # ------------------------------------------------- bolsa de 90 minutos
    def _gracia_total_min(self):
        return int(self.env['foco.settings'].sudo().get_settings().justify_gracia_min or 0)

    def _rango_dia_local(self):
        """[ini, fin] en UTC del dia LOCAL de la persona en que empezo el periodo."""
        self.ensure_one()
        tz = self.env['foco.settings'].sudo()._tzinfo_for(self.employee_id)
        d = pytz.UTC.localize(self.start).astimezone(tz).date()
        ini = tz.localize(datetime.combine(d, time(0, 0))).astimezone(pytz.UTC).replace(tzinfo=None)
        fin = tz.localize(datetime.combine(d, time(23, 59, 59))).astimezone(pytz.UTC).replace(tzinfo=None)
        return ini, fin

    def _gracia_consumida_min(self):
        """Minutos de la bolsa del dia ya cargados por OTROS periodos de la persona."""
        self.ensure_one()
        ini, fin = self._rango_dia_local()
        otros = self.sudo().search([('employee_id', '=', self.employee_id.id), ('id', '!=', self.id),
                                    ('cubierta_por', '=', 'gracia'),
                                    ('start', '>=', ini), ('start', '<=', fin)])
        return sum(otros.mapped('gracia_min'))

    def _punto_de_corte(self, minutos):
        """Instante en que el periodo lleva `minutos` DENTRO de la jornada. Si el
        hueco cae entero en la jornada es inicio + minutos; si parte cae fuera
        (empezo antes del turno, cruza la comida), se busca: la jornada esperada
        crece de forma monotona con el fin del tramo."""
        self.ensure_one()
        total = (self.stop - self.start).total_seconds()
        if abs((self.expected_seconds or 0.0) - total) < 60:
            return self.start + timedelta(minutes=minutos)
        settings = self.env['foco.settings'].sudo().get_settings()
        objetivo = minutos * 60.0
        lo, hi = self.start, self.stop
        for _ in range(24):
            mid = lo + (hi - lo) / 2
            if settings.expected_seconds(self.employee_id, self.start, mid, con_asistencia=True) < objetivo:
                lo = mid
            else:
                hi = mid
        return hi.replace(microsecond=0)

    def _aplicar_gracia(self):
        """La justificacion fue aceptada (IA, catalogo o fuera de jornada): se
        carga a la bolsa diaria de la persona, en el orden en que se acepta.
        Cabe entera: justificada con sus minutos. No cabe nada: por decidir del
        admin. Cabe una parte: se PARTE en dos (lo que cabe queda justificado y
        agota la bolsa; el resto nace como periodo propio, por decidir, con la
        misma nota). La suma de las dos partes es el periodo original y la
        bolsa siempre cuadra. Idempotente: no se carga dos veces."""
        self.ensure_one()
        if self.cubierta_por or self.state != 'justificada':
            return
        total = self._gracia_total_min()
        mins = int(round((self.expected_seconds or 0.0) / 60.0))
        libre = max(0, total - self._gracia_consumida_min())
        if mins <= 0:
            # Fuera de la jornada: no gasta bolsa ni necesita decision.
            self.sudo().write({'cubierta_por': 'gracia', 'gracia_min': 0, 'gracia_restante_min': libre})
            return
        if mins <= libre:
            self.sudo().write({'cubierta_por': 'gracia', 'gracia_min': mins,
                               'gracia_restante_min': libre - mins})
            return
        if libre <= 0:
            self.sudo().write({'state': 'por_decidir', 'cubierta_por': False,
                               'gracia_min': 0, 'gracia_restante_min': 0})
            return
        corte = self._punto_de_corte(libre)
        if corte <= self.start or corte >= self.stop:
            self.sudo().write({'state': 'por_decidir', 'cubierta_por': False,
                               'gracia_min': 0, 'gracia_restante_min': 0})
            return
        settings = self.env['foco.settings'].sudo().get_settings()
        e1 = settings.expected_seconds(self.employee_id, self.start, corte, con_asistencia=True)
        e2 = max(0.0, (self.expected_seconds or 0.0) - e1)
        self.sudo().create({
            'computer_id': self.computer_id.id, 'start': corte, 'stop': self.stop,
            'kind': self.kind, 'expected_seconds': e2,
            'reason': self.reason, 'note': self.note, 'state': 'por_decidir',
            'notified': True, 'answered_at': self.answered_at, 'answered_via': self.answered_via,
            'ai_estado': 'analizado', 'ai_veredicto': self.ai_veredicto, 'ai_categoria': self.ai_categoria,
            'ai_motivo': self.ai_motivo, 'ai_confianza': self.ai_confianza, 'ai_modelo': self.ai_modelo,
            'ai_at': self.ai_at, 'rebotes': self.rebotes, 'nota_rechazada': self.nota_rechazada,
            'origen_id': self.id, 'gracia_min': 0, 'gracia_restante_min': 0,
        })
        self.sudo().write({'stop': corte, 'expected_seconds': e1,
                           'cubierta_por': 'gracia', 'gracia_min': libre, 'gracia_restante_min': 0})

    # ------------------------------------------------- decision del admin
    def action_aceptar(self):
        """Paso de sus 90 min y el admin la da por buena: cuenta como cubierta."""
        ahora = fields.Datetime.now()
        for rec in self.filtered(lambda r: r.state == 'por_decidir'):
            rec._registrar_msg('admin', 'Aceptada por el administrador.',
                               user_id=self.env.user.id)
            rec.sudo().write({'state': 'justificada', 'cubierta_por': 'admin', 'gracia_min': 0,
                              'decidido_por': self.env.user.id, 'decidido_at': ahora,
                              'ai_requiere_revision': False, 'review_visto': True,
                              'review_por': self.env.user.id, 'review_at': ahora})
        return True

    def action_rechazar(self):
        """Paso de sus 90 min y el admin no la acepta: no cuenta."""
        ahora = fields.Datetime.now()
        for rec in self.filtered(lambda r: r.state == 'por_decidir'):
            rec._registrar_msg('admin', 'Rechazada por el administrador: no cuenta como cubierta.',
                               user_id=self.env.user.id)
            rec.sudo().write({'state': 'rechazada', 'cubierta_por': False, 'gracia_min': 0,
                              'decidido_por': self.env.user.id, 'decidido_at': ahora,
                              'ai_requiere_revision': False, 'review_visto': True,
                              'review_por': self.env.user.id, 'review_at': ahora})
        return True

    def devolver_con_mensaje(self, mensaje):
        """El admin se la devuelve al empleado con SUS palabras.

        El tercer camino: cuando la IA ya dio sus vueltas y no le saco el dato,
        Aceptar o Rechazar son las dos malas. Esto reabre el periodo y le manda
        el texto del administrador por el MISMO canal que la pregunta de la IA
        (`ai_pregunta` -> `payload().bounce_msg`), asi que la ventana del agente
        lo pinta sin que haya que tocar el .exe. No gasta rebotes: los cinco son
        de la IA; los del administrador son suyos y los da cuando quiera."""
        self.ensure_one()
        mensaje = (mensaje or '').strip()[:200]
        if not mensaje:
            return False
        self._registrar_msg('admin', mensaje, user_id=self.env.user.id)
        self.sudo().write({
            'state': 'pendiente',
            'rebote_pendiente': True,
            'ai_pregunta': mensaje,
            'nota_rechazada': (self.note or '').strip()[:200] or False,
            # Que lo pida la VENTANA, no el correo (igual que el rebote de la IA).
            'notified': True,
            # Lo que tuviera cargado a la bolsa se libera: se vuelve a cargar
            # cuando la IA acepte el texto nuevo.
            'cubierta_por': False, 'gracia_min': 0, 'gracia_restante_min': 0,
        })
        return True

    def action_devolver_mensaje(self):
        """Abre el cuadro para escribirle al empleado."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Devolver con mi mensaje',
            'res_model': 'foco.absence.devolver',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_absence_id': self.id},
        }

    def action_reconsiderar(self):
        """Vuelve a "Por decidir" una aceptada o rechazada por el admin."""
        for rec in self.filtered(lambda r: r.state == 'rechazada'
                                 or (r.state == 'justificada' and r.cubierta_por == 'admin')):
            rec.sudo().write({'state': 'por_decidir', 'cubierta_por': False,
                              'decidido_por': False, 'decidido_at': False})
        return True

    # ------------------------------------------------- revision humana
    def action_marcar_revisado(self):
        self.write({'review_visto': True, 'review_por': self.env.user.id,
                    'review_at': fields.Datetime.now()})
        return True

    def action_reabrir_revision(self):
        self.write({'review_visto': False, 'review_por': False, 'review_at': False})
        return True

    @api.model
    def revisar_pendientes(self):
        """Cuantas justificaciones marco la IA que siguen sin revisar. Para el
        KPI del tablero; respeta el alcance de quien pregunta (sin sudo)."""
        return self.search_count([('ai_requiere_revision', '=', True),
                                  ('review_visto', '=', False)])

    @api.model
    def pendientes_atencion(self):
        """Lo que la IA dejo esperando a alguien, con nombres, para la lista
        «Requiere atencion» del tablero: justificaciones vagas sin revisar
        (por persona) y las devueltas al empleado que siguen sin respuesta.
        Sin sudo: el alcance de quien pregunta manda."""
        revisar = []
        for emp, n in self._read_group(
                [('ai_requiere_revision', '=', True), ('review_visto', '=', False)],
                ['employee_id'], ['__count']):
            revisar.append({'id': emp.id if emp else 0,
                            'nombre': emp.name if emp else 'Sin empleado', 'n': n})
        revisar.sort(key=lambda r: -r['n'])
        devueltas = []
        for rec in self.search([('rebote_pendiente', '=', True)], order='start desc', limit=20):
            devueltas.append({'id': rec.employee_id.id, 'nombre': rec.employee_id.name,
                              'texto': (rec.nota_rechazada or '')[:40], 'veces': rec.rebotes,
                              'pregunta': (rec.ai_pregunta or '')[:60]})
        # Regla de los 90 min: lo que paso de la bolsa y espera Aceptar / Rechazar.
        decidir = []
        for emp, n in self._read_group([('state', '=', 'por_decidir')], ['employee_id'], ['__count']):
            decidir.append({'id': emp.id if emp else 0,
                            'nombre': emp.name if emp else 'Sin empleado', 'n': n})
        decidir.sort(key=lambda r: -r['n'])
        return {'revisar': sum(r['n'] for r in revisar), 'revisar_por': revisar[:8],
                'devueltas': self.search_count([('rebote_pendiente', '=', True)]),
                'devueltas_lista': devueltas,
                'por_decidir': sum(r['n'] for r in decidir), 'por_decidir_por': decidir[:8]}
