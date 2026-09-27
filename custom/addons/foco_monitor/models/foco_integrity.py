"""Hechos de integridad: lo que Foco midio y que puede leerse como intento de
saltarse la medicion, UNO por persona, dia y tipo, con su evidencia y su
significado escritos en el propio dato.

No hay puntaje ni "riesgo": cada renglon es un numero verificable (horas, veces
o minutos) con la regla que lo produjo. Quien lo lee -el administrador, el
tablero o Tomy- decide. Es la misma linea del resto del modulo: evidencia para
una persona, nunca un veredicto del sistema.

De donde sale cada uno (27/28-sep-2026, propuesta contra la evasion):

  sintetico_sin_input   foco.usage.injected_hours (agente 2026.09.28+): input generado
                        por software y NADA real en 3 min. Firma de jiggler de software.
  inyectado_con_input   foco.usage.injected_tool_hours: una herramienta inyecta
                        mientras la persona trabaja (raton 3D, macro, remoto). Informativo.
  sin_teclear           foco.usage.nokey_hours: activo 3 min sin una tecla (solo mouse).
  pantalla_sin_cambio   foco.usage.static_hours: hubo input y la pantalla quedo igual.
  sitio_bloqueado       eventos sitio_bloqueado: la pagina de bloqueo al frente.
  navegador_cerrado     eventos navegador_cerrado: Foco cerro un navegador no permitido.
  navegador_desconocido eventos navegador_desconocido: un navegador fuera de la lista dio URL.
  agente_reinicio       agente_inicio sin encendido, reanudacion, desbloqueo, actualizacion
                        ni parada limpia en los VENTANA_CAUSA_MIN minutos previos.
  retraso_entrega       max(recibido - ocurrido) de los eventos del dia, si supera dos
                        ciclos de envio: el equipo no tenia camino a Odoo.
  sin_senal_tras_checar checo entrada (hr.attendance) y su equipo dio la primera senal
                        SIN_SENAL_MIN o mas minutos despues (o nunca).
  admin_local           foco.computer.user_is_admin: puede parar el servicio.
  dispositivo_nuevo     eventos dispositivo_nuevo: mouse, teclado o HID nuevo.
  justificado_otro      ausencias justificadas con motivo "Otro" (horas).
  justificacion_repetida el mismo texto de justificacion mas de una vez en el dia.
  llamada_sin_input     foco.usage.call_noinput_hours: en llamada sin tocar nada.
  llamadas_indeterminadas llamadas analizadas sin veredicto (sin audio).
  contenedor            horas activas en categorias marcadas "contenedor" (remoto, VM).

MANTENIMIENTO: el dia en que el agente se actualizo o se reinstalo, los hechos
se marcan. Ya paso dos veces que el propio soporte disparo "manipulacion".
"""
import json
import logging
from datetime import datetime, timedelta

import pytz

from odoo import api, fields, models

from .foco_event import KINDS as KINDS_EVENTO

_logger = logging.getLogger(__name__)

KINDS = [
    ('sintetico_sin_input', 'Sintetico sin input real'),
    ('inyectado_con_input', 'Inyectado con input real (herramienta)'),
    ('sin_teclear', 'Activo sin teclear'),
    ('pantalla_sin_cambio', 'Activo con pantalla sin cambio'),
    ('sitio_bloqueado', 'Intentos de sitio bloqueado'),
    ('navegador_cerrado', 'Navegador no permitido cerrado'),
    ('navegador_desconocido', 'Navegador no gestionado dio URL'),
    ('agente_reinicio', 'Arranque del agente sin causa registrada'),
    ('retraso_entrega', 'Retraso maximo de entrega'),
    ('sin_senal_tras_checar', 'Checo y su equipo no dio senal'),
    ('admin_local', 'Usuario administrador local'),
    ('dispositivo_nuevo', 'Dispositivo de entrada nuevo'),
    ('justificado_otro', 'Justificado como "Otro"'),
    ('justificacion_repetida', 'Justificacion con el mismo texto repetida'),
    ('llamada_sin_input', 'En llamada sin tocar nada'),
    ('llamadas_indeterminadas', 'Llamadas sin veredicto'),
    ('contenedor', 'Tiempo dentro de un contenedor'),
]

UNIDADES = [('h', 'horas'), ('n', 'veces'), ('min', 'minutos')]

SIGNIFICADO = {
    'sintetico_sin_input': 'Input generado por software y nada real en 3 minutos: la firma de un '
                           'jiggler de software. Windows marca el input inyectado; es un hecho.',
    'inyectado_con_input': 'Inyectado Y input real a la vez: una herramienta que inyecta mientras '
                           'trabaja (raton 3D, software de mouse, soporte remoto). NO es ausencia.',
    'sin_teclear': 'Activo solo con mouse durante tramos de 3 minutos. Evidencia contra un jiggler '
                   'de hardware; revisar planos con el mouse tambien cae aqui.',
    'pantalla_sin_cambio': 'Hubo teclado o mouse y la pantalla quedo identica. El trabajo real '
                           'cambia la pantalla; un mouse que se mueve sin hacer nada, no.',
    'sitio_bloqueado': 'Veces que la pagina de bloqueo estuvo al frente: intentos, no visitas.',
    'navegador_cerrado': 'Veces que Foco cerro un navegador que no obedece el bloqueo (Opera). '
                         'La persona lo abrio; no vio nada en el.',
    'navegador_desconocido': 'Un navegador fuera de la lista gestionada entrego una URL: existe en '
                             'el equipo y la politica de sitios no lo cubre.',
    'agente_reinicio': 'El agente arranco sin que antes hubiera encendido, reanudacion, desbloqueo, '
                       'actualizacion ni parada limpia: lo mataron o se cayo, y el servicio lo relanzo.',
    'retraso_entrega': 'Minutos entre que algo paso en el equipo y que Odoo lo recibio, mas alla de '
                       'dos ciclos de envio: el equipo no tenia camino a Odoo en ese rato.',
    'sin_senal_tras_checar': 'Checo entrada y su equipo dio la primera senal mucho despues (o nunca): '
                             'estuvo en la empresa sin usar su equipo, o el equipo no reporto.',
    'admin_local': 'El usuario es administrador local del equipo: puede detener el servicio y '
                   'quitar el bloqueo. Nada medido en ese equipo esta garantizado.',
    'dispositivo_nuevo': 'Windows instalo un mouse, teclado o HID que no conocia. Normal al estrenar '
                         'un mouse; junto a horas solo-mouse es evidencia de jiggler de hardware.',
    'justificado_otro': 'Horas de ausencia justificadas con el motivo generico "Otro".',
    'justificacion_repetida': 'El mismo texto de justificacion, repetido en el dia: se escribe para '
                              'cerrar la ventana, no para explicar.',
    'llamada_sin_input': 'Tiempo acreditado por estar en llamada sin teclado ni mouse: una junta '
                         'escuchando o una sala vacia dejada abierta.',
    'llamadas_indeterminadas': 'Llamadas analizadas que quedaron sin veredicto (sin audio o sin voz): '
                               'no pesan como trabajo ni como personal.',
    'contenedor': 'Horas activas dentro de un escritorio remoto, maquina virtual o emulador: lo que '
                  'pasa adentro no se ve.',
}

# Lo que Foco NO puede saber, para que ningun lector lo infiera del silencio.
NO_SE_PUEDE_SABER = [
    'Otra persona frente al teclado del equipo vigilado.',
    'Un segundo dispositivo no vigilado (el telefono personal, otra laptop).',
    'Un jiggler de hardware que aleatorice intervalos y desplazamientos: solo queda la evidencia '
    'de horas solo-mouse y de dispositivos de entrada nuevos.',
    'Lo que pasa dentro de un escritorio remoto o una maquina virtual.',
    'Si el usuario es administrador local, nada de lo anterior esta garantizado.',
]

# Definiciones con numero, declaradas AQUI y no enterradas en el codigo:
# - VENTANA_CAUSA_MIN: cuanto antes de un arranque del agente se busca su causa.
#   Un encendido y el inicio de sesion pueden llevarse varios minutos.
# - CICLO_ENVIO_MIN: el agente empuja cada 5 min y recoge el registro de Windows
#   en ese mismo ciclo; un retraso de hasta dos ciclos es normal.
# - SIN_SENAL_MIN: lo que se tolera entre checar en la puerta y que el equipo
#   de la mesa de senal.
VENTANA_CAUSA_MIN = 30
CICLO_ENVIO_MIN = 5
SIN_SENAL_MIN = 30
# Instalador de Foco tal como aparece en el catalogo cuando corre (Inno Setup).
INSTALADOR_PREFIJO = 'ferba-foco-setup'
# Desde que version del agente las columnas de integridad significan lo que
# aqui se dice (sintetico = sin input real; solo mouse; pantalla sin cambio).
# Renglones de agentes anteriores NO producen esos hechos: medirian otra cosa.
CODIGO_SEMANTICA = 202609280
# Eventos que por naturaleza llegan al siguiente encendido: un apagado no se
# puede enviar mientras el equipo se apaga. Su retraso no dice nada.
EVENTOS_DIFERIDOS = ('apagado', 'apagado_solicitado', 'apagado_inesperado', 'suspendido')


class FocoCategory(models.Model):
    _inherit = 'foco.category'

    is_container = fields.Boolean(
        string='Contenedor de otro sistema',
        help='Escritorio remoto, maquina virtual, emulador: lo que la persona hace '
             'ADENTRO no se ve. Las horas activas en estas apps se reportan como '
             'hecho de integridad "tiempo dentro de un contenedor".')


class FocoIntegrityFact(models.Model):
    _name = 'foco.integrity.fact'
    _description = 'Hecho de integridad'
    _order = 'date desc, employee_id, kind'

    _uniq = models.Constraint('unique(employee_id, date, kind)',
                              'Ya hay un hecho de ese tipo para esa persona y dia.')

    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True,
                                  ondelete='cascade', index=True)
    department_id = fields.Many2one(related='employee_id.department_id', store=True,
                                    string='Departamento')
    computer_id = fields.Many2one('foco.computer', string='Equipo', ondelete='set null')
    date = fields.Date(string='Dia', required=True, index=True)
    kind = fields.Selection(KINDS, string='Hecho', required=True, index=True)
    value = fields.Float(string='Valor')
    unit = fields.Selection(UNIDADES, string='Unidad', required=True, default='n')
    value_text = fields.Char(string='Medida', compute='_compute_textos')
    evidence = fields.Text(string='Evidencia (JSON)')
    evidence_text = fields.Char(string='Evidencia', compute='_compute_textos')
    meaning = fields.Char(string='Que significa')
    maintenance = fields.Boolean(
        string='Mantenimiento ese dia',
        help='Ese dia el agente se actualizo o se reinstalo. Un arranque sin causa o '
             'una base recreada pueden ser del soporte, no de la persona.')
    computed_at = fields.Datetime(string='Calculado', readonly=True)

    @api.depends('value', 'unit', 'evidence')
    def _compute_textos(self):
        for r in self:
            r.value_text = self._formatea(r.value, r.unit)
            try:
                datos = json.loads(r.evidence) if r.evidence else {}
            except ValueError:
                datos = {}
            partes = []
            for k, v in list(datos.items())[:6]:
                if isinstance(v, list):
                    v = ', '.join(str(x) for x in v[:5]) + (', ...' if len(v) > 5 else '')
                partes.append('%s: %s' % (k, v))
            r.evidence_text = ' | '.join(partes)[:250]

    @api.model
    def _formatea(self, valor, unidad):
        if unidad == 'h':
            m = int(round((valor or 0.0) * 60))
            return '%d h %02d min' % (m // 60, m % 60) if m >= 60 else '%d min' % m
        if unidad == 'min':
            return '%d min' % int(round(valor or 0.0))
        n = int(round(valor or 0.0))
        return '%d %s' % (n, 'vez' if n == 1 else 'veces')

    # ------------------------------------------------------------ calculo
    @api.model
    def rebuild(self, employees, dias):
        """Rehace los hechos de esas personas en esos dias. Se llama al recibir
        un envio (una persona, uno o dos dias) y desde el proceso nocturno."""
        for emp in employees:
            for dia in dias:
                try:
                    with self.env.cr.savepoint():
                        self._rehacer(emp, fields.Date.to_date(dia))
                except Exception:
                    _logger.exception('Foco: hechos de integridad de %s el %s', emp.name, dia)

    @api.model
    def _cron_rebuild(self, dias_atras=2):
        hoy = fields.Date.context_today(self)
        dias = [hoy - timedelta(days=i) for i in range(dias_atras + 1)]
        empleados = self.env['foco.computer'].sudo().search(
            [('employee_id', '!=', False)]).mapped('employee_id')
        self.rebuild(empleados, dias)

    def _rehacer(self, emp, dia):
        hechos = self._hechos_de(emp, dia)
        self.search([('employee_id', '=', emp.id), ('date', '=', dia)]).unlink()
        if hechos:
            self.create(hechos)

    def _hechos_de(self, emp, dia):
        """Lista de vals de hechos para esa persona y dia. Solo lo que tiene valor."""
        Usage = self.env['foco.usage'].sudo()
        Event = self.env['foco.event'].sudo()
        equipos = self.env['foco.computer'].sudo().search([('employee_id', '=', emp.id)])
        zona = self.env['foco.settings'].sudo()._tzinfo_for(emp)
        ini_utc, fin_utc = self.env['foco.workday'].sudo()._limites_utc(dia, zona)
        ahora = fields.Datetime.now()
        salida = []

        def hecho(kind, valor, unidad, evidencia=None):
            salida.append({
                'employee_id': emp.id, 'computer_id': equipos[:1].id or False,
                'date': dia, 'kind': kind, 'value': float(valor), 'unit': unidad,
                'evidence': json.dumps(evidencia or {}, ensure_ascii=False, default=str),
                'meaning': SIGNIFICADO.get(kind, ''), 'computed_at': ahora,
            })

        # El alta del equipo: antes de ese dia no habia agente, y lo que hay en
        # el registro de Windows de esos dias es la lectura historica inicial
        # (los ultimos 50 eventos), no algo que se midio en vivo.
        altas = [c.create_date for c in equipos if c.create_date]
        alta_utc = min(altas) if altas else None
        alta_local = (pytz.UTC.localize(alta_utc).astimezone(zona or pytz.UTC).date()
                      if alta_utc else None)
        antes_del_alta = bool(alta_local and dia < alta_local)

        # --- del uso ---------------------------------------------------------
        dominio = [('employee_id', '=', emp.id), ('date', '=', dia)]
        tot = Usage._read_group(dominio, [], ['call_noinput_hours:sum', 'fg_active:sum'])
        (cni, activo) = (tot[0] if tot else (0, 0))
        # Las columnas de integridad solo de agentes que las miden con el
        # significado de hoy (ver CODIGO_SEMANTICA).
        dominio_nuevo = dominio + [('agent_code', '>=', CODIGO_SEMANTICA)]
        tot2 = Usage._read_group(dominio_nuevo, [], ['injected_hours:sum', 'injected_tool_hours:sum',
                                                   'nokey_hours:sum', 'static_hours:sum'])
        (iny, iny_tool, nokey, static) = (tot2[0] if tot2 else (0,) * 4)
        por_app = {}
        for app, a_iny, a_nokey, a_static in Usage._read_group(
                dominio_nuevo, ['app_id'], ['injected_hours:sum', 'nokey_hours:sum', 'static_hours:sum']):
            if app:
                por_app[app.display_name] = (a_iny or 0.0, a_nokey or 0.0, a_static or 0.0)

        def top(idx):
            pares = sorted(((v[idx], n) for n, v in por_app.items() if v[idx] > 0), reverse=True)[:3]
            return ['%s %s' % (n, self._formatea(v, 'h')) for v, n in pares]

        if iny:
            hecho('sintetico_sin_input', iny, 'h', {'de_activo': self._formatea(activo, 'h'), 'apps': top(0)})
        if iny_tool:
            hecho('inyectado_con_input', iny_tool, 'h', {'de_activo': self._formatea(activo, 'h')})
        if nokey:
            hecho('sin_teclear', nokey, 'h', {'de_activo': self._formatea(activo, 'h'), 'apps': top(1)})
        if static:
            hecho('pantalla_sin_cambio', static, 'h', {'de_activo': self._formatea(activo, 'h'), 'apps': top(2)})
        if cni:
            hecho('llamada_sin_input', cni, 'h', {})
        contenedor = 0.0
        for cat, horas in Usage._read_group(dominio + [('category_id.is_container', '=', True)],
                                            ['category_id'], ['fg_active:sum']):
            contenedor += horas or 0.0
        if contenedor:
            hecho('contenedor', contenedor, 'h', {})

        # --- de los eventos --------------------------------------------------
        eventos = Event.search([('employee_id', '=', emp.id), ('date', '=', dia)], order='at asc')
        mantenimiento = any(e.kind == 'agente_actualizado' for e in eventos)
        if not mantenimiento and equipos:
            App = self.env['foco.app'].sudo()
            if Usage.search_count(dominio + [('app_id', 'in', App.search(
                    [('exe', 'like', INSTALADOR_PREFIJO + '%')]).ids)]):
                mantenimiento = True
        conteos = {}
        for e in eventos:
            conteos.setdefault(e.kind, []).append(e)
        for kind in ('sitio_bloqueado', 'navegador_cerrado', 'navegador_desconocido', 'dispositivo_nuevo'):
            lista = conteos.get(kind) or []
            if lista:
                hecho(kind, len(lista), 'n', {
                    'horas': [self._local(e.at, zona)[-5:] for e in lista[:12]],
                    'detalle': sorted({(e.process or e.os_word or '')[:60] for e in lista if (e.process or e.os_word)})[:6],
                })
        causas = ('encendido', 'reanudado', 'desbloqueo', 'agente_actualizado', 'agente_fin')
        sin_causa = []
        for e in conteos.get('agente_inicio') or []:
            desde = e.at - timedelta(minutes=VENTANA_CAUSA_MIN)
            if not any(c.kind in causas and desde <= c.at <= e.at for c in eventos):
                sin_causa.append(self._local(e.at, zona)[-5:])
        if sin_causa:
            hecho('agente_reinicio', len(sin_causa), 'n', {'horas': sin_causa, 'mantenimiento': mantenimiento})
        retraso = 0.0
        peor = None
        for e in eventos:
            # Ni lo anterior al alta (lectura historica), ni lo que por
            # naturaleza viaja al siguiente encendido.
            if antes_del_alta or (alta_utc and e.at and e.at < alta_utc) or e.kind in EVENTOS_DIFERIDOS:
                continue
            if e.create_date and e.at:
                minutos = (e.create_date - e.at).total_seconds() / 60.0
                if minutos > retraso:
                    retraso, peor = minutos, e
        if retraso > 2 * CICLO_ENVIO_MIN:
            hecho('retraso_entrega', retraso, 'min', {
                'evento': dict(KINDS_EVENTO).get(peor.kind, peor.kind) if peor else '',
                'ocurrio': self._local(peor.at, zona) if peor else '', 'llego': self._local(peor.create_date, zona) if peor else ''})

        # --- checador contra primera senal ----------------------------------
        # Solo desde el alta del equipo: antes no habia quien diera senal.
        if 'hr.attendance' in self.env and not antes_del_alta:
            asist = self.env['hr.attendance'].sudo().search(
                [('employee_id', '=', emp.id), ('check_in', '>=', ini_utc), ('check_in', '<=', fin_utc)],
                order='check_in asc', limit=1)
            if asist:
                jornada = self.env['foco.workday'].sudo().search(
                    [('employee_id', '=', emp.id), ('date', '=', dia)], limit=1)
                primera = jornada.first_signal if jornada else False
                fin_ref = primera or asist.check_out or (ahora if dia == fields.Date.context_today(self) else fin_utc)
                minutos = (fin_ref - asist.check_in).total_seconds() / 60.0
                if minutos >= SIN_SENAL_MIN:
                    hecho('sin_senal_tras_checar', minutos, 'min', {
                        'checo': self._local(asist.check_in, zona),
                        'primera_senal': self._local(primera, zona) if primera else 'ninguna'})

        # --- equipo ------------------------------------------------------------
        if any(c.user_is_admin for c in equipos):
            hecho('admin_local', 1, 'n', {'equipos': [c.name for c in equipos if c.user_is_admin]})

        # --- justificaciones --------------------------------------------------
        Abs = self.env['foco.absence'].sudo()
        just = Abs.search([('employee_id', '=', emp.id), ('state', '=', 'justificada'),
                           ('start', '>=', ini_utc), ('start', '<=', fin_utc)])
        otro = sum(a.duration or 0.0 for a in just if a.reason == 'otro')
        if otro:
            hecho('justificado_otro', otro, 'h', {'ausencias': len([a for a in just if a.reason == 'otro'])})
        textos = {}
        for a in just:
            t = (a.note or '').strip().lower()
            if t:
                textos[t] = textos.get(t, 0) + 1
        repetidas = {t: n for t, n in textos.items() if n > 1}
        if repetidas:
            hecho('justificacion_repetida', sum(repetidas.values()), 'n',
                  {'textos': ['"%s" x%d' % (t[:40], n) for t, n in sorted(repetidas.items(), key=lambda kv: -kv[1])[:4]]})

        # --- llamadas ---------------------------------------------------------
        if 'foco.call.review' in self.env:
            indet = self.env['foco.call.review'].sudo().search_count(
                [('employee_id', '=', emp.id), ('clasificacion', '=', 'indeterminada'),
                 ('started_at', '>=', ini_utc), ('started_at', '<=', fin_utc)])
            if indet:
                hecho('llamadas_indeterminadas', indet, 'n', {})

        for h in salida:
            h['maintenance'] = mantenimiento
        return salida

    @api.model
    def _local(self, dt, zona):
        if not dt:
            return ''
        return pytz.UTC.localize(dt).astimezone(zona or pytz.UTC).strftime('%Y-%m-%d %H:%M')

    # ------------------------------------------------------------ consumo
    @api.model
    def resumen(self, desde, hasta, employee_ids=None):
        """Por persona y tipo, agregado en el periodo: {emp_id: [{kind, etiqueta,
        valor, unidad, texto, dias, significado, mantenimiento}]}. Respeta las
        reglas de registro del usuario que pregunta."""
        desde = fields.Date.to_date(desde)
        hasta = fields.Date.to_date(hasta)
        dominio = [('date', '>=', desde), ('date', '<=', hasta)]
        if employee_ids:
            dominio.append(('employee_id', 'in', list(employee_ids)))
        etiquetas = dict(KINDS)
        salida = {}
        for r in self.search(dominio, order='date asc'):
            por = salida.setdefault(r.employee_id.id, {})
            h = por.setdefault(r.kind, {'kind': r.kind, 'etiqueta': etiquetas.get(r.kind, r.kind),
                                        'valor': 0.0, 'unidad': r.unit, 'dias': 0,
                                        'significado': SIGNIFICADO.get(r.kind, ''),
                                        'mantenimiento': False, 'evidencia': []})
            h['valor'] += r.value or 0.0
            h['dias'] += 1
            h['mantenimiento'] = h['mantenimiento'] or bool(r.maintenance)
            if len(h['evidencia']) < 3 and r.evidence_text:
                h['evidencia'].append('%s: %s' % (r.date, r.evidence_text[:120]))
        for emp_id, por in salida.items():
            for h in por.values():
                h['texto'] = self._formatea(h['valor'], h['unidad'])
            salida[emp_id] = sorted(por.values(), key=lambda x: (x['unidad'] != 'h', -x['valor']))
        return salida
