import re

from odoo import api, fields, models


class FocoApp(models.Model):
    _name = 'foco.app'
    _description = 'Aplicacion descubierta'
    _order = 'last_seen desc'

    _exe_uniq = models.Constraint('unique(exe)',
                                  'Ese ejecutable ya existe en el catalogo.')

    name = fields.Char(required=True)
    exe = fields.Char(string='Ejecutable', required=True, index=True)
    category_id = fields.Many2one(
        'foco.category', string='Categoria', ondelete='set null',
        help='La asigna el admin. Vacio = sin clasificar (no cuenta en el indice).')
    foreground_only = fields.Boolean(
        string='Solo primer plano',
        help='Si esta marcado, el tiempo en SEGUNDO plano de esta app no cuenta '
             '(p.ej. Spotify, WhatsApp: solo vale cuando tiene el foco).')
    name_manual = fields.Boolean(
        string='Nombre puesto por el admin', readonly=True, copy=False,
        help='Marcado en cuanto una persona edita el nombre. Mientras este '
             'apagado, el agente lo mantiene al dia con el nombre que declara '
             'el propio ejecutable. Prendido, el agente NO lo vuelve a tocar.')
    first_seen = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_seen = fields.Datetime(readonly=True)
    classified = fields.Boolean(compute='_compute_classified', store=True)

    @api.depends('category_id')
    def _compute_classified(self):
        for rec in self:
            rec.classified = bool(rec.category_id)

    # Direccion de correo dentro del texto. Deliberadamente NO se intenta
    # detectar "esto parece un titulo de ventana": no existe una regla no
    # arbitraria para eso, y cualquier umbral (largo maximo, numero de
    # separadores) seria un numero inventado y ajustado a la muestra que se
    # tenga enfrente. El origen del nombre se arreglo donde corresponde: el
    # agente manda el FileDescription del binario, no el titulo.
    #
    # Esto es lo unico que queda aqui, y no es una heuristica sino un
    # INVARIANTE DE PRIVACIDAD: un correo jamas es el nombre de un programa, y
    # el servidor no debe persistir PII que le llegue de un cliente que no
    # controla (un agente viejo sin actualizar, o mal configurado, sigue
    # mandando titulos). El costo de rechazar de mas es que la app se muestre
    # con su ejecutable; el de aceptar de mas es guardar datos personales.
    _CORREO = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')

    @api.model
    def _nombre_valido(self, name):
        name = (name or '').strip()
        return bool(name) and not self._CORREO.search(name)

    # ---- de quien es el campo `name` -------------------------------------
    # Antes se deducia comparando valores: "si name == exe, nadie lo ha
    # reclamado". Eso no distingue una decision del admin de un nombre heredado
    # —quedaban CONGELADOS 17 titulos de ventana, imposibles de refrescar— y
    # ademas pisaba al admin si este escribia literalmente el ejecutable.
    # Ahora la propiedad es EXPLICITA: en cuanto una persona edita el nombre,
    # `name_manual` se enciende y el agente no vuelve a tocarlo.
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name') and not self.env.context.get('foco_agente'):
                vals['name_manual'] = True
        return super().create(vals_list)

    def write(self, vals):
        if 'name' in vals and not self.env.context.get('foco_agente'):
            vals = dict(vals, name_manual=True)
        return super().write(vals)

    def action_soltar_nombre(self):
        """Devuelve el nombre al agente: se repone solo en el proximo reporte."""
        self.write({'name_manual': False})
        return True

    @api.model
    def _get_or_create(self, exe, name=None):
        exe = (exe or '').strip().lower()
        if not exe:
            return self.browse()
        app = self.search([('exe', '=', exe)], limit=1)
        now = fields.Datetime.now()
        limpio = name if self._nombre_valido(name) else None
        yo = self.with_context(foco_agente=True)
        if app:
            vals = {'last_seen': now}
            # El nombre lo declara el binario y se mantiene al dia SOLO si el
            # admin no lo ha tomado. El admin siempre gana.
            if limpio and not app.name_manual and app.name != limpio:
                vals['name'] = limpio
            app.with_context(foco_agente=True).write(vals)
        else:
            app = yo.create({'exe': exe, 'name': limpio or exe, 'last_seen': now})
        return app
