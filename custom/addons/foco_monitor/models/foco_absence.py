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
SISTEMA_JUST = (
    "Eres quien revisa las justificaciones de ausencia de los empleados de {contexto} "
    "El empleado eligio el motivo 'Otro' y escribio un TEXTO LIBRE para explicar por que su "
    "computadora estuvo un rato sin actividad. Te doy ese texto, cuanto duro, a que hora y "
    "cuantos minutos del hueco caian DENTRO de su jornada. Haz dos cosas:\n"
    "(A) VEREDICTO sobre el texto:\n"
    " - 'adecuada' si el texto da CUALQUIER razon o circunstancia concreta: una actividad, "
    "tarea, lugar, gestion, o una afirmacion de horario, aunque sea de una sola palabra "
    "('almacen', 'bano', 'junta'), este en mayusculas o sea informal. 'fuera de horario "
    "laboral', 'sin actividades asignadas' y 'reunion con proveedor' SON adecuadas: dicen algo.\n"
    " - 'vaga' SOLO cuando el texto no aporta NADA: vacio, un punto, comas, 'x', 'asdf', letras "
    "o numeros sueltos, o repetir 'otro'/'na'/'nada'.\n"
    " - 'indeterminada' si de verdad no puedes decidir.\n"
    "NO castigues que alguien diga que estaba fuera de su horario: si eso es cierto o no lo "
    "verifica el SISTEMA con el dato de la jornada, no tu. Tu solo dices si el texto aporta una "
    "razon.\n"
    "(B) CATEGORIA: elige de la lista del esquema la que mejor describa el texto. Si dice que "
    "estaba fuera de horario, de fin de semana, dormido o en casa, usa 'fuera_horario'. Si no "
    "encaja en ninguna, 'otra'.\n"
    "'motivo' RESUME el texto en maximo 12 palabras, sin nombres de personas. Se tolerante: la "
    "meta es cazar solo las que no dicen nada y ordenar las demas por categoria."
)
# Motivos del catalogo que explican la ausencia por SI SOLOS: si el empleado
# eligio uno de estos (no "Otro"), no hay nada que revisar ni se llama a la IA.
MOTIVOS_EXPLICAN = {'comida', 'medico', 'escuela', 'permiso', 'tramite', 'personal'}
# Veredicto del texto. 'sin_relacion' queda solo por compatibilidad con filas
# viejas (ya no se asigna ni se marca a revisar): ahora lo que no es creible se
# decide por el dato, no por el texto.
VEREDICTOS_JUST = [
    ('adecuada', 'Justificacion adecuada'),
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
            'veredicto': {'type': 'string', 'enum': ['adecuada', 'vaga', 'indeterminada']},
            'categoria': {'type': 'string', 'enum': [c for c, _ in CATEGORIAS_JUST]},
            'motivo': {'type': 'string'},
            'confianza': {'type': 'number'},
        },
        'required': ['veredicto', 'categoria', 'motivo', 'confianza'],
    },
}
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
    state = fields.Selection(
        [('auto', 'Explicado por calendario'),
         ('pendiente', 'Por justificar'),
         ('justificada', 'Justificada')],
        string='Estado', default='pendiente', index=True, required=True)
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
             'basura). Solo eso llega a "Periodos a revisar": una razon escrita, '
             'aunque sea discutible, no se marca aqui.')
    ai_incoherente = fields.Boolean(
        string='No coincide con su horario', index=True,
        help='El empleado declaro que estaba fuera de su horario, pero el hueco '
             'cae DENTRO de su jornada configurada. Es un DATO medido, no una '
             'acusacion: puede ser una evasion o que el horario configurado no sea '
             'el real (lo afina la jornada por empleado). No entra a "a revisar".')
    ai_motivo = fields.Char(string='Que vio la IA')
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

    # ---- revision humana de lo que la IA marco ------------------------------
    review_visto = fields.Boolean(
        string='Revisado', index=True,
        help='Un administrador ya reviso esta justificacion marcada por la IA. '
             'Sale de la lista de "Periodos a revisar".')
    review_por = fields.Many2one('res.users', string='Revisado por', readonly=True)
    review_at = fields.Datetime(string='Revisado el', readonly=True)
    review_nota = fields.Char(string='Nota de la revision')

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
    def record_gaps(self, computer, gaps):
        """Guarda los huecos que manda el agente. Idempotente por (equipo, inicio).

        Solo se marca 'pendiente' lo que el sistema NO puede explicar solo: si
        el hueco cae fuera de la jornada o en la comida, entra como 'auto' y
        nadie tiene que contestarlo. Sin esto, preguntariamos por la hora de
        comida todos los dias.
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
            expected = settings.expected_seconds(employee, start, stop, con_asistencia=True)
            explicado = (expected < MIN_EXPECTED_SECS
                         or self._covered_by_leave(employee, start, stop))
            self.sudo().create({
                'computer_id': computer.id,
                'start': start,
                'stop': stop,
                'kind': gap.get('kind') if gap.get('kind') in kinds else 'idle',
                'expected_seconds': expected,
                'state': 'auto' if explicado else 'pendiente',
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
        """Si el periodo traslapa la comida del calendario, se presugiere."""
        self.ensure_one()
        calendar = self.employee_id.resource_calendar_id
        lunch = self.env['foco.settings'].lunch_intervals(calendar)
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
                # vacio (periodo devuelto por justificacion vaga). Si el .exe no
                # conoce la clave, la ignora: el rebote degrada a re-preguntar.
                'bounced': bool(rec.rebote_pendiente),
                'bounce_msg': bmsg if rec.rebote_pendiente else '',
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
            out[str(employee.id)] = {
                'expected': settings.expected_seconds(employee, ini, fin) / 3600.0,
                'justified': sum(justi.mapped('expected_seconds')) / 3600.0,
                'pending': len(pend),
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
            }
        return out

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
        if self.state == 'justificada':
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
            'rebote_pendiente': False,
        })
        return {'ok': True, 'reason_label': dict(REASONS).get(reason, '')}

    def write(self, vals):
        # Si cambia el texto o el motivo de una justificacion ya analizada,
        # la IA la vuelve a mirar: un administrador pudo corregir la nota. Se
        # evita la recursion saltando cuando el propio write es de campos IA.
        reanaliza = (('note' in vals or 'reason' in vals)
                     and 'ai_estado' not in vals and 'ai_veredicto' not in vals)
        res = super().write(vals)
        if reanaliza:
            for rec in self:
                if rec.state == 'justificada' and rec.ai_estado == 'analizado':
                    super(FocoAbsence, rec).write({'ai_estado': 'sin_analizar', 'ai_intentos': 0})
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
                'ai_incoherente': False,
                'ai_motivo': 'Motivo del catalogo: %s' % dict(REASONS).get(self.reason, ''),
                'ai_confianza': 1.0, 'ai_modelo': False, 'ai_tokens': 0,
                'ai_at': fields.Datetime.now(), 'ai_error': False,
            })
            return
        # 2) El hueco cae FUERA de su jornada (hecho medido, no conjetura): no hay
        #    nada que justificar fuera de hora. Se acepta sin gastar IA y sin
        #    marcar. Cero falsos positivos: lo dice el dato, no el texto.
        if (self.expected_seconds or 0.0) < OFF_SHIFT_SEGUNDOS:
            self.sudo().write({
                'ai_estado': 'analizado', 'ai_veredicto': 'adecuada',
                'ai_categoria': 'fuera_horario', 'ai_requiere_revision': False,
                'ai_incoherente': False,
                'ai_motivo': 'El hueco cae fuera de su jornada',
                'ai_confianza': 1.0, 'ai_modelo': False, 'ai_tokens': 0,
                'ai_at': fields.Datetime.now(), 'ai_error': False,
            })
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
        ver = j.get('veredicto') if j.get('veredicto') in ('adecuada', 'vaga', 'indeterminada') else 'indeterminada'
        cat = j.get('categoria') if j.get('categoria') in dict(CATEGORIAS_JUST) else 'otra'
        # A REVISAR solo lo VAGO (no da ninguna razon). Una razon escrita, aunque
        # sea discutible, no se marca: eso es lo que pidio la reunion del 8-oct.
        requiere = (ver == 'vaga')
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
            'ai_confianza': conf, 'ai_modelo': resp.get('modelo') or False,
            'ai_tokens': int((resp.get('usage') or {}).get('total_tokens') or 0),
            'ai_at': fields.Datetime.now(), 'ai_error': False,
        }
        # Rebote (enforcement): si la IA dice que el texto NO explica nada, se
        # DEVUELVE al empleado para que lo rehaga -se reabre el periodo y la
        # ventana del agente vuelve con el aviso-, en vez de solo dejarlo para el
        # admin. Gobernado por el interruptor de Ajustes y con tope de seguridad:
        # tras REBOTES_MAX se deja de reabrir (queda en «Periodos a revisar»).
        settings = self.env['foco.settings'].sudo().get_settings()
        if requiere and settings.justify_rebote_activo and self.rebotes < REBOTES_MAX:
            vals.update({
                'state': 'pendiente',
                'rebote_pendiente': True,
                'rebotes': self.rebotes + 1,
                'nota_rechazada': (self.note or '').strip()[:200] or False,
                # Que lo nag la VENTANA, no el correo: evita avisar dos veces.
                'notified': True,
            })
        self.sudo().write(vals)

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
