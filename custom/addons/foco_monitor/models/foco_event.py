import json
import logging
from datetime import datetime

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Lo que le puede pasar a un equipo. La llave es la misma que usa el agente.
#
# Los seis primeros salen del registro de eventos de Windows, identificados por
# Event ID -un numero, igual en cualquier idioma-. Los cuatro ultimos los aporta
# el agente, porque el canal donde Windows anota inicio y cierre de sesion exige
# privilegios de administrador que no se van a pedir.
KINDS = [
    ('encendido', 'Equipo encendido'),
    ('apagado', 'Equipo apagado'),
    ('apagado_inesperado', 'Apagado inesperado'),
    ('apagado_solicitado', 'Alguien pidio apagar o reiniciar'),
    ('suspendido', 'Equipo suspendido'),
    ('reanudado', 'Equipo reanudado'),
    ('agente_inicio', 'El agente arranco'),
    ('agente_fin', 'El agente se detuvo'),
    ('bloqueo', 'Sesion bloqueada'),
    ('desbloqueo', 'Sesion desbloqueada'),
    ('llamada_inicio', 'Entro a una llamada'),
    ('llamada_fin', 'Salio de la llamada'),
    # Lo aporta el SERVICIO del equipo (SYSTEM), no el agente: cerro un
    # navegador que no obedece el bloqueo de sitios (Opera). Ver foco.policy.
    ('navegador_cerrado', 'Navegador no permitido cerrado'),
]

class FocoEvent(models.Model):
    """Que le paso al equipo, con hora y con fuente.

    Es la pieza que le pone CAUSA a los huecos. Sin esto, "apago a las 18:14 y
    se fue a su casa" y "el agente dejo de correr a las 15:20 con el equipo
    prendido" son el mismo renglon vacio, y como los dos se ven igual, el
    segundo -que es el unico que amerita mirar- no se ve nunca.
    """

    _name = 'foco.event'
    _description = 'Evento del equipo'
    _order = 'at desc, id desc'

    _uniq = models.Constraint(
        'unique(computer_id, at, kind, source)',
        'Ese evento ya estaba registrado para el equipo.')

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True,
        ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', string='Empleado',
        store=True, index=True)
    department_id = fields.Many2one(
        related='computer_id.department_id', string='Departamento', store=True)
    at = fields.Datetime(string='Cuando', required=True, index=True)
    date = fields.Date(
        string='Dia', index=True,
        help='El dia LOCAL de la persona, que es el que se usa para agrupar la '
             'jornada. No coincide siempre con el dia UTC del campo Cuando.')
    kind = fields.Selection(KINDS, string='Que paso', required=True, index=True)
    source = fields.Selection(
        [('os', 'Registro de Windows'), ('agent', 'Agente'),
         ('servicio', 'Servicio del equipo')],
        string='De donde se supo', required=True, default='agent',
        help='Que la procedencia este siempre a la vista es lo que separa un '
             'dato de una acusacion: el dia que alguien discuta la hora, la '
             'fuente ya esta ahi.')
    process = fields.Char(
        string='Proceso', help='Que programa pidio el apagado o el reinicio.')
    os_word = fields.Char(
        string='Palabra de Windows',
        help='El texto TAL CUAL lo escribio Windows, en el idioma del equipo. '
             'Se guarda como cita para que lo lea una persona; el sistema no '
             'decide nada con el, porque viene traducido.')
    user_name = fields.Char(string='Usuario de Windows')
    event_id = fields.Integer(
        string='Id de evento', help='El numero del registro de Windows.')

    def name_get(self):
        etiquetas = dict(KINDS)
        return [(r.id, '%s %s' % (
            fields.Datetime.context_timestamp(r, r.at).strftime('%d/%m %H:%M')
            if r.at else '', etiquetas.get(r.kind, r.kind))) for r in self]

    # ------------------------------------------------------------- ingesta
    @api.model
    def record_events(self, computer, eventos):
        """Guarda los eventos que manda un agente. Devuelve cuantos entraron.

        Idempotente: el agente reintenta hasta que el servidor confirma, asi que
        el mismo evento puede llegar dos veces y no debe duplicarse.
        """
        if not eventos:
            return 0
        validos = dict(KINDS)
        zona = self.env['foco.settings'].sudo()._tzinfo_for(
            computer.employee_id, computer)
        existentes = self
        n = 0
        vals_list = []
        for e in eventos:
            kind = e.get('kind')
            if kind not in validos:
                continue
            at = self._parse_utc(e.get('at'))
            if not at:
                continue
            detalle = e.get('detail') or ''
            datos = {}
            if detalle:
                try:
                    datos = json.loads(detalle)
                except (TypeError, ValueError):
                    datos = {}
            vals_list.append({
                'computer_id': computer.id,
                'at': at,
                'date': self._dia_local(at, zona),
                'kind': kind,
                'source': e.get('source') if e.get('source') in ('os', 'servicio') else 'agent',
                'process': (datos.get('process') or '')[:120] or False,
                'os_word': (datos.get('os_word') or '')[:120] or False,
                'user_name': (datos.get('user') or '')[:120] or False,
                'event_id': datos.get('event_id') or 0,
            })
        if not vals_list:
            return 0
        # Se filtran los que ya estan en vez de confiar en la restriccion: una
        # excepcion de base abortaria TODA la ingesta del envio, incluidos los
        # renglones de uso que venian en el mismo paquete.
        claves = {(v['at'], v['kind'], v['source']) for v in vals_list}
        existentes = self.search([
            ('computer_id', '=', computer.id),
            ('at', 'in', sorted({v['at'] for v in vals_list})),
        ])
        ya = {(r.at, r.kind, r.source) for r in existentes}
        nuevos = [v for v in vals_list if (v['at'], v['kind'], v['source']) not in ya]
        if nuevos:
            self.create(nuevos)
            n = len(nuevos)
        # Se devuelve el total RECIBIDO, no el insertado: el agente necesita
        # saber que el servidor se hizo cargo de todos para dejar de mandarlos;
        # los repetidos ya estaban guardados, que es el mismo resultado.
        _logger.debug('Foco: %s eventos recibidos, %s nuevos', len(vals_list), n)
        return len(vals_list)

    @api.model
    def _parse_utc(self, valor):
        """'2026-09-11T16:34:41' (UTC) -> datetime naive, que es como guarda Odoo."""
        if not valor:
            return False
        texto = str(valor).replace('T', ' ').split('.')[0].rstrip('Z').strip()
        for formato in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                return datetime.strptime(texto, formato)
            except ValueError:
                continue
        return False

    @api.model
    def _dia_local(self, at_utc, zona):
        """El dia de la persona, no el de UTC.

        Un apagado a las 19:30 de Merida son las 00:30 UTC del dia siguiente. Si
        se agrupara por el dia UTC, ese apagado cerraria la jornada equivocada.
        """
        import pytz
        return pytz.UTC.localize(at_utc).astimezone(zona or pytz.UTC).date()
