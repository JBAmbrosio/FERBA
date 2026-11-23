# -*- coding: utf-8 -*-
"""Bitacora del checador: una fila por cada intento de marca.

Es la EVIDENCIA y no se edita (nadie tiene permiso de escritura; la crea el
servidor). La asistencia (hr.attendance) se deriva de los eventos aceptados y
RH la puede corregir como siempre: el evento original queda intacto.
"""
import hmac
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

ACEPTADOS = ('entrada', 'salida')


class FerbaChecadorEvento(models.Model):
    _name = 'ferba.checador.evento'
    _description = 'Evento del checador'
    _order = 'id desc'
    _rec_name = 'employee_id'

    checador_id = fields.Many2one('ferba.checador', string='Checador', required=True,
                                  index=True, ondelete='restrict')
    company_id = fields.Many2one(related='checador_id.company_id', store=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado', index=True,
                                  ondelete='restrict')
    resultado = fields.Selection(
        [('entrada', 'Entrada'),
         ('salida', 'Salida'),
         ('nip_fallido', 'NIP incorrecto'),
         ('bloqueado', 'Tarjeta bloqueada'),
         ('sin_nip', 'Empleado sin NIP'),
         ('doble', 'Doble marca'),
         ('vencida', 'Sin conexion: llego fuera de plazo'),
         ('conflicto', 'Sin conexion: choca con otra asistencia')],
        string='Resultado', required=True, index=True)
    aceptado = fields.Boolean(string='Aceptada', compute='_compute_aceptado', store=True)
    fecha = fields.Datetime(string='Fecha', required=True, index=True,
                            default=fields.Datetime.now)
    uuid = fields.Char(string='Identificador', index=True, copy=False)
    foto = fields.Image(string='Foto', max_width=640, max_height=640, attachment=True)
    sin_foto = fields.Boolean(string='Sin foto')
    # Marca hecha sin red: `fecha` es el momento en que se marco (no cuando llego)
    # y `create_date` es cuando la recibio el servidor.
    offline = fields.Boolean(string='Sin conexion', index=True)
    hora_dispositivo = fields.Boolean(
        string='Hora del reloj de la tableta',
        help='La tableta se reinicio antes de enviar la marca, asi que la hora viene de su '
             'reloj (corregido con la ultima sincronizacion) y no de un conteo exacto.')
    attendance_id = fields.Many2one('hr.attendance', string='Asistencia',
                                    ondelete='set null')

    _uuid_unico = models.Constraint(
        'unique(uuid)', 'Ese intento de marca ya estaba registrado.')

    @api.depends('resultado')
    def _compute_aceptado(self):
        for e in self:
            e.aceptado = e.resultado in ACEPTADOS

    # ------------------------------------------------------------------
    # El unico camino por el que entra una marca
    # ------------------------------------------------------------------
    @api.model
    def _fecha_offline(self, offline, ahora):
        """Momento real de una marca hecha sin red. Devuelve (fecha, es_del_reloj).

        Lo exacto es `hace_ms`: cuanto tiempo paso en la tableta desde la marca,
        medido con un reloj que no se puede cambiar a mano. Solo si la tableta se
        reinicio se usa la hora de su reloj, y nunca se acepta una hora futura.
        """
        try:
            hace = int(offline.get('hace_ms'))
        except (TypeError, ValueError):
            hace = -1
        if hace >= 0:
            return (ahora - timedelta(milliseconds=hace)).replace(microsecond=0), False
        try:
            fecha = fields.Datetime.to_datetime(offline.get('fecha'))
        except (TypeError, ValueError):
            fecha = None
        if not fecha:
            return None, True
        return min(fecha, ahora).replace(microsecond=0), True

    def _asistencia_en(self, emp, fecha):
        """Entrada o salida con una hora pasada. Devuelve (asistencia, resultado) o (None, None)."""
        Asistencia = self.env['hr.attendance']
        abierta = Asistencia.search(
            [('employee_id', '=', emp.id), ('check_out', '=', False)],
            order='check_in desc', limit=1)
        if abierta and abierta.check_in > fecha:
            return None, None
        try:
            with self.env.cr.savepoint():
                if abierta:
                    abierta.write({'check_out': fecha, 'out_mode': 'kiosk'})
                    return abierta, 'salida'
                nueva = Asistencia.create({
                    'employee_id': emp.id, 'check_in': fecha, 'in_mode': 'kiosk'})
                return nueva, 'entrada'
        except (UserError, ValidationError):
            return None, None

    @api.model
    def _marcar(self, checador, employee_id, nip, uuid=None, foto_b64=None, offline=None):
        """Valida y registra un intento. Devuelve un dict para la tableta.

        Reenviar el mismo `uuid` no duplica: devuelve lo que ya se resolvio.
        `offline` (dict con `hace_ms` y/o `fecha`) indica que la marca se hizo sin
        red y llega despues: se registra con su hora real.
        """
        self = self.sudo()
        ahora = fields.Datetime.now()
        uuid = (uuid or '').strip()[:64] or False
        if uuid:
            previo = self.search([('uuid', '=', uuid)], limit=1)
            if previo:
                return previo._respuesta(repetido=True)

        emp = self.env['hr.employee'].browse(int(employee_id or 0)).exists()
        if not emp or emp.company_id != checador.company_id or not emp.active:
            return {'ok': False, 'motivo': 'empleado', 'mensaje': 'Empleado no encontrado.'}

        base = {'checador_id': checador.id, 'employee_id': emp.id, 'uuid': uuid, 'fecha': ahora}
        cuando = ahora
        if offline is not None:
            cuando, del_reloj = self._fecha_offline(offline, ahora)
            base.update({'offline': True, 'hora_dispositivo': del_reloj, 'fecha': cuando or ahora})
            limite = ahora - timedelta(hours=checador.horas_offline)
            if not checador.permite_offline or not cuando or cuando < limite:
                return self.create({**base, 'resultado': 'vencida'})._respuesta()

        # El bloqueo de AHORA no dice nada de una marca que se hizo antes sin red.
        if offline is None and emp.checador_bloqueado_hasta and emp.checador_bloqueado_hasta > ahora:
            return self.create({**base, 'resultado': 'bloqueado'})._respuesta()

        if not emp.pin:
            return self.create({**base, 'resultado': 'sin_nip'})._respuesta()

        if offline is not None and not hmac.compare_digest(str(emp.pin), str(nip or '')):
            # La tableta ya lo valido con el NIP que conocia; si aqui no coincide es que
            # cambio mientras tanto. Queda en la bitacora y no cuenta para el bloqueo.
            return self.create({**base, 'resultado': 'nip_fallido'})._respuesta(restantes=None)

        if not hmac.compare_digest(str(emp.pin), str(nip or '')):
            fallos = emp.checador_fallos + 1
            vals = {'checador_fallos': fallos}
            if fallos >= checador.max_fallos:
                vals = {
                    'checador_fallos': 0,
                    'checador_bloqueado_hasta': ahora + timedelta(minutes=checador.minutos_bloqueo),
                }
                checador.message_post(body=(
                    'Tarjeta de %s bloqueada %s min por %s NIP incorrectos seguidos.'
                    % (emp.name, checador.minutos_bloqueo, fallos)))
            emp.write(vals)
            ev = self.create({**base, 'resultado': 'nip_fallido'})
            return ev._respuesta(restantes=max(checador.max_fallos - fallos, 0))

        if checador.segundos_doble_marca:
            reciente = self.search([
                ('employee_id', '=', emp.id), ('aceptado', '=', True),
                ('fecha', '>=', cuando - timedelta(seconds=checador.segundos_doble_marca)),
                ('fecha', '<=', cuando + timedelta(seconds=checador.segundos_doble_marca)),
            ], limit=1)
            if reciente:
                return self.create({**base, 'resultado': 'doble'})._respuesta(previo=reciente)

        if emp.checador_fallos or emp.checador_bloqueado_hasta:
            emp.write({'checador_fallos': 0, 'checador_bloqueado_hasta': False})

        if offline is not None:
            asistencia, resultado = self._asistencia_en(emp, cuando)
            if not asistencia:
                return self.create({
                    **base, 'resultado': 'conflicto', 'foto': foto_b64 or False})._respuesta()
        else:
            asistencia = emp._attendance_action_change({'mode': 'kiosk'})
            resultado = 'salida' if asistencia.check_out else 'entrada'
        ev = self.create({
            **base,
            'resultado': resultado,
            'attendance_id': asistencia.id,
            'foto': foto_b64 or False,
            'sin_foto': bool(checador.exige_foto and not foto_b64),
        })
        return ev._respuesta()

    def _respuesta(self, repetido=False, restantes=None, previo=None):
        self.ensure_one()
        emp = self.employee_id
        r = {
            'ok': self.aceptado,
            'resultado': self.resultado,
            'empleado': emp.name,
            'fecha_utc': fields.Datetime.to_string(self.fecha),
            'repetido': repetido,
        }
        if self.aceptado:
            r['horas_hoy'] = round(emp.hours_today, 2)
            r['mensaje'] = 'Entrada registrada' if self.resultado == 'entrada' else 'Salida registrada'
        elif self.resultado == 'nip_fallido':
            r['restantes'] = restantes
            r['mensaje'] = 'NIP incorrecto.'
            if not restantes and emp.checador_bloqueado_hasta:
                # Este fallo fue el que bloqueo la tarjeta: se dice hasta cuando.
                r['hasta_utc'] = fields.Datetime.to_string(emp.checador_bloqueado_hasta)
                r['mensaje'] = 'NIP incorrecto. Tarjeta bloqueada por intentos fallidos.'
        elif self.resultado == 'bloqueado':
            r['hasta_utc'] = fields.Datetime.to_string(emp.checador_bloqueado_hasta)
            r['mensaje'] = 'Tarjeta bloqueada por intentos fallidos. Avisa a Recursos Humanos.'
        elif self.resultado == 'vencida':
            r['mensaje'] = 'La marca llego fuera del plazo permitido sin conexion.'
        elif self.resultado == 'conflicto':
            r['mensaje'] = 'La marca choca con otra asistencia. Recursos Humanos la revisa.'
        elif self.resultado == 'sin_nip':
            r['mensaje'] = 'No tienes NIP asignado. Pidelo a Recursos Humanos.'
        elif self.resultado == 'doble':
            r['mensaje'] = 'Ya habias marcado hace un momento.'
            if previo:
                r['previo'] = previo.resultado
        return r

    # ------------------------------------------------------------------
    # Las fotos no se guardan para siempre
    # ------------------------------------------------------------------
    @api.model
    def cron_borrar_fotos(self):
        dias = int(self.env['ir.config_parameter'].sudo().get_param(
            'ferba_checador.dias_foto', '90') or 90)
        viejos = self.sudo().search([
            ('foto', '!=', False),
            ('fecha', '<', fields.Datetime.now() - timedelta(days=dias)),
        ], limit=2000)
        viejos.write({'foto': False})
        return len(viejos)
