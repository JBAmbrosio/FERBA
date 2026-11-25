"""Clientes desde el telefono: contactos, edicion, baja y su BITACORA.

Pedido de campo (Jose Watanabe, 23-nov-2026): al registrar un cliente solo se
podia poner nombre y numero, no se podia corregir un nombre mal escrito, la
ficha no mostraba el contacto y no habia como eliminar un registro.

Decision del usuario (24-nov-2026): TODOS los vendedores pueden ver, editar y
eliminar cualquier cliente de la cartera; a cambio, cada alta, cambio y baja
queda en una bitacora que dice quien, cuando, desde que telefono y que cambio.
La bitacora no se edita ni se borra (ni el administrador): es la evidencia.

"Eliminar" es ARCHIVAR (reversible desde Odoo): un cliente puede tener visitas,
cotizaciones e historial que no deben perderse por un toque en el telefono.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError

ACCIONES = [
    ('alta', 'Alta de cliente'),
    ('edicion', 'Edición de cliente'),
    ('baja', 'Baja de cliente'),
    ('contacto_alta', 'Alta de contacto'),
    ('contacto_edicion', 'Edición de contacto'),
    ('contacto_baja', 'Baja de contacto'),
    ('ubicacion_alta', 'Alta de ubicación'),
    ('ubicacion_edicion', 'Edición de ubicación'),
    ('ubicacion_baja', 'Baja de ubicación'),
    ('telefono', 'Cambio de teléfono'),
    ('pin', 'Cambio de pin'),
]


class FocoClienteBitacora(models.Model):
    _name = 'foco.cliente.bitacora'
    _description = 'Foco - Bitacora de clientes (cambios desde el telefono)'
    _order = 'fecha desc, id desc'
    _rec_name = 'cliente'

    fecha = fields.Datetime(string='Fecha', required=True, readonly=True,
                            default=fields.Datetime.now, index=True)
    user_id = fields.Many2one('res.users', string='Quién', readonly=True, index=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado', readonly=True)
    device_id = fields.Many2one('foco.mobile.device', string='Teléfono',
                                readonly=True, ondelete='set null')
    accion = fields.Selection(ACCIONES, string='Qué hizo', required=True,
                              readonly=True, index=True)
    # El cliente (razon social) al que pertenece el cambio. Se guarda tambien su
    # nombre en texto: si un dia se borra de verdad en Odoo, el renglon se entiende.
    partner_id = fields.Many2one('res.partner', string='Cliente', readonly=True,
                                 index=True, ondelete='set null',
                                 context={'active_test': False})
    cliente = fields.Char(string='Nombre del cliente', readonly=True)
    detalle = fields.Text(string='Qué cambió', readonly=True)

    def write(self, vals):
        raise UserError('La bitácora de clientes no se edita.')

    def unlink(self):
        raise UserError('La bitácora de clientes no se borra.')


class FocoMobileDeviceClientes(models.Model):
    _inherit = 'foco.mobile.device'

    # ------------------------------------------------------------ bitacora
    def _bitacora(self, partner, accion, detalle=''):
        """Un renglon en la bitacora de clientes. Nunca tumba la operacion."""
        self.ensure_one()
        try:
            razon = partner.sudo().with_context(active_test=False).commercial_partner_id
            self.env['foco.cliente.bitacora'].sudo().create({
                'user_id': self._vendedor().id or False,
                'employee_id': self.sudo().employee_id.id or False,
                'device_id': self.id,
                'accion': accion,
                'partner_id': razon.id,
                'cliente': razon.name or '',
                'detalle': (detalle or '')[:2000],
            })
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception('foco: no se pudo escribir la bitacora de clientes')

    @api.model
    def _cambio(self, etiqueta, antes, despues):
        return '%s: «%s» → «%s»' % (etiqueta, antes or '', despues or '')

    # ----------------------------------------------------------- contactos
    @api.model
    def _contactos_de(self, partner):
        """[{id, nombre, puesto, numero}] de las PERSONAS de la razon social (sus
        hijos que no son ubicaciones). Para un pin que es una ubicacion, los de su
        razon social: el contacto es de la empresa, no del campo."""
        razon = partner.commercial_partner_id
        return [{'id': p.id, 'nombre': p.name or '', 'puesto': p.function or '',
                 'numero': p.phone or ''}
                for p in razon.child_ids.sorted('id')   # el primero registrado, primero
                if p.active and p.type == 'contact' and not p.foco_tipo_ubicacion]

    def _vendedor_o_error(self):
        user = self._vendedor()
        if not user:
            raise UserError('Tu teléfono no está ligado a un usuario de Odoo.')
        return user

    def _razon_social(self, partner_id):
        """La razon social (cliente principal) de un pin, con los permisos del vendedor."""
        user = self._vendedor_o_error()
        p = self.env['res.partner'].with_user(user).browse(int(partner_id or 0)).exists()
        if not p:
            raise UserError('Ese cliente ya no existe en Odoo.')
        razon = p.commercial_partner_id
        # exists() tambien devuelve los archivados: uno ya eliminado no se vuelve a
        # tocar (y no deja un segundo renglon en la bitacora).
        if not razon.active:
            raise UserError('Ese cliente ya fue eliminado.')
        if not razon.foco_cliente_ventas:
            raise UserError('Ese registro no es un cliente de ventas.')
        return razon

    @api.model
    def _numero_valido(self, numero):
        numero = (numero or '').strip()
        if numero and len([c for c in numero if c.isdigit()]) < 7:
            raise UserError('«%s» no parece un número de teléfono.' % numero)
        return numero[:32]

    def editar_cliente(self, partner_id, nombre):
        """Corrige el NOMBRE de la empresa (lo que Jose no podia: se equivoco al
        registrarla y no habia como cambiarlo)."""
        self.ensure_one()
        razon = self._razon_social(partner_id)
        nombre = (nombre or '').strip()[:120]
        if not nombre:
            raise UserError('Ponle un nombre a la empresa.')
        antes = razon.name or ''
        if nombre == antes:
            return {'ok': True, 'id': razon.id, 'name': razon.display_name,
                    'mensaje': 'No hubo cambios.'}
        razon.write({'name': nombre})
        cambio = self._cambio('Nombre', antes, nombre)
        try:
            razon.message_post(body='Editado desde el teléfono. %s' % cambio)
        except Exception:  # noqa: BLE001
            pass
        self._bitacora(razon, 'edicion', cambio)
        return {'ok': True, 'id': razon.id, 'name': razon.display_name,
                'mensaje': 'Actualicé el nombre a %s.' % nombre}

    def guardar_contacto(self, partner_id, contacto_id=None, nombre='', puesto='', numero=''):
        """Agrega un contacto (persona) a la empresa, o edita uno que ya tiene."""
        self.ensure_one()
        razon = self._razon_social(partner_id)
        nombre = (nombre or '').strip()[:120]
        puesto = (puesto or '').strip()[:120]
        numero = self._numero_valido(numero)
        if not nombre:
            raise UserError('Ponle un nombre al contacto.')
        if contacto_id:
            c = self.env['res.partner'].with_user(self._vendedor()).browse(int(contacto_id)).exists()
            if not c or not c.active or c.parent_id != razon or c.foco_tipo_ubicacion:
                raise UserError('Ese contacto ya no existe en %s.' % razon.name)
            cambios = []
            if nombre != (c.name or ''):
                cambios.append(self._cambio('Nombre', c.name, nombre))
            if puesto != (c.function or ''):
                cambios.append(self._cambio('Puesto', c.function, puesto))
            if numero != (c.phone or ''):
                cambios.append(self._cambio('Número', c.phone, numero))
            if not cambios:
                return {'ok': True, 'id': c.id, 'mensaje': 'No hubo cambios.'}
            c.write({'name': nombre, 'function': puesto, 'phone': numero})
            detalle = 'Contacto «%s». %s' % (nombre, '; '.join(cambios))
            accion, mensaje = 'contacto_edicion', 'Actualicé el contacto %s.' % nombre
        else:
            vals = {'name': nombre, 'function': puesto, 'phone': numero,
                    'parent_id': razon.id, 'type': 'contact', 'is_company': False,
                    'user_id': razon.user_id.id or False}
            if razon.company_id:
                vals['company_id'] = razon.company_id.id
            c = self.env['res.partner'].with_user(self._vendedor()).create(vals)
            detalle = 'Contacto «%s»%s%s' % (
                nombre, ', puesto «%s»' % puesto if puesto else '',
                ', número «%s»' % numero if numero else '')
            accion, mensaje = 'contacto_alta', 'Agregué a %s como contacto de %s.' % (nombre, razon.name)
        try:
            razon.message_post(body='Desde el teléfono. %s' % detalle)
        except Exception:  # noqa: BLE001
            pass
        self._bitacora(razon, accion, detalle)
        return {'ok': True, 'id': c.id, 'mensaje': mensaje}

    def borrar_contacto(self, partner_id, contacto_id):
        """Quita (ARCHIVA) un contacto de la empresa."""
        self.ensure_one()
        razon = self._razon_social(partner_id)
        c = self.env['res.partner'].with_user(self._vendedor()).browse(int(contacto_id or 0)).exists()
        if not c or not c.active or c.parent_id != razon or c.foco_tipo_ubicacion:
            raise UserError('Ese contacto ya no existe en %s.' % razon.name)
        detalle = 'Contacto «%s»%s%s' % (
            c.name or '', ', puesto «%s»' % c.function if c.function else '',
            ', número «%s»' % c.phone if c.phone else '')
        c.write({'active': False})
        try:
            razon.message_post(body='Contacto eliminado (archivado) desde el teléfono. %s' % detalle)
        except Exception:  # noqa: BLE001
            pass
        self._bitacora(razon, 'contacto_baja', detalle)
        return {'ok': True, 'id': c.id, 'mensaje': 'Quité a %s de %s.' % (c.name, razon.name)}

    def borrar_cliente(self, partner_id):
        """Elimina (ARCHIVA, reversible) un cliente con sus ubicaciones y contactos:
        deja de salir en el mapa y en la cartera. En Odoo se recupera con el filtro
        Archivados."""
        self.ensure_one()
        razon = self._razon_social(partner_id)
        nombre = razon.name or ''
        hijos = razon.child_ids.filtered('active')
        ubic = hijos.filtered('foco_tipo_ubicacion')
        detalle = 'Cliente «%s» archivado' % nombre
        if hijos:
            detalle += ' con %d ubicación(es) y %d contacto(s)' % (len(ubic), len(hijos - ubic))
        try:
            razon.message_post(body='Eliminado (archivado) desde el teléfono.')
        except Exception:  # noqa: BLE001
            pass
        (hijos | razon).write({'active': False})
        self._bitacora(razon, 'baja', detalle + '.')
        return {'ok': True, 'id': razon.id, 'mensaje': 'Eliminé a %s.' % nombre}
