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
    product = fields.Char(
        string='Suite', readonly=True,
        help='De que producto dice ser el ejecutable, segun su propio recurso '
             'de version. No lo escribe nadie: lo declara el binario.')
    report_document = fields.Boolean(
        string='Reportar el archivo abierto',
        help='Con esto encendido, el agente manda el NOMBRE del archivo que la '
             'persona tiene abierto en esta aplicacion (nunca la ruta, nunca el '
             'contenido). Sirve para contrastar contra el proyecto declarado: '
             '"dijo 2704 y estaba en 2704-presupuesto.xlsx".\n\n'
             'Es dato sensible: un nombre de archivo puede ser "Demanda '
             'laboral.docx". Por eso se enciende app por app y tiene que estar '
             'en el aviso de privacidad que firma el personal.')

    first_seen = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_seen = fields.Datetime(readonly=True)
    classified = fields.Boolean(compute='_compute_classified', store=True)

    # Lo que el binario declara ser para que se le proponga reportar documento.
    # NO es una lista de ejecutables: es el nombre de producto que la propia
    # suite escribe en sus binarios, y por eso sigue funcionando cuando Office
    # cambia de nombres de exe entre versiones.
    _SUITES_DOCUMENTALES = ('microsoft office',)

    @api.model
    def _es_suite_documental(self, product):
        p = (product or '').strip().lower()
        return any(s in p for s in self._SUITES_DOCUMENTALES)

    document_state = fields.Selection(
        [('si', 'Reportando'),
         ('off', 'Apagado en Ajustes'),
         ('no', 'No')],
        string='Estado del reporte', compute='_compute_document_state',
        help='Lo que de verdad pasa, no lo que dice la casilla. Una app marcada '
             'con el interruptor general apagado sale como "Apagado en '
             'Ajustes": sin esta columna, alguien marca la casilla, no ocurre '
             'nada y concluye que el sistema esta roto.')

    @api.depends('report_document')
    def _compute_document_state(self):
        activo = self.env['foco.settings'].sudo().get_settings().document_enabled
        for rec in self:
            if not rec.report_document:
                rec.document_state = 'no'
            else:
                rec.document_state = 'si' if activo else 'off'

    mic_not_call = fields.Boolean(
        string='Capturar microfono NO es llamada',
        help='Marcala en las aplicaciones que se quedan con el microfono sin '
             'que haya una junta: grabadores de reuniones, asistentes de voz, '
             'software de transcripcion.\n\n'
             'Existe por un caso real: un grabador tomo el microfono a las '
             '13:32 y no lo solto, asi que TODA la tarde quedo marcada como '
             '"en llamada" -incluido el tiempo en Excel- y ademas el tiempo '
             'ocioso se conto como activo, porque estar en llamada lo acredita.'
             '\n\nSi esta app y una de verdad tienen el microfono a la vez, '
             'SI se cuenta como llamada: marcar el grabador no tapa la junta.')

    no_screenshot = fields.Boolean(
        string='Nunca capturar con esta app al frente',
        help='Marcala en las aplicaciones cuya pantalla no debe fotografiarse '
             'NUNCA: el gestor de contrasenas, el portal de nomina, la banca en '
             'linea, el expediente medico.\n\n'
             'Es una lista que decide el administrador, no una escrita en el '
             'codigo: lo que en una empresa es delicado en otra no lo es, y una '
             'lista cocida envejeceria sola.\n\n'
             'Si esta aplicacion esta en primer plano, el equipo NO toma la '
             'captura y lo anota como rechazada. Vale para los dos '
             'disparadores, incluido el manual: un administrador no puede '
             'saltarse esta marca pidiendo la captura a mano.')

    @api.model
    def no_call_apps(self):
        """Ejecutables cuyo uso del microfono no implica una llamada."""
        return self.sudo().search([('mic_not_call', '=', True)]).mapped('exe')

    @api.model
    def no_screenshot_apps(self):
        """Ejecutables con los que NUNCA se captura la pantalla."""
        return self.sudo().search([('no_screenshot', '=', True)]).mapped('exe')

    @api.model
    def clasificadas(self):
        """Ejecutables que YA tienen categoria.

        El agente los necesita para el disparador de «aplicacion sin
        clasificar»: lo que esta en esta lista no dispara nada. Se manda la
        lista de conocidas y no la de desconocidas porque las desconocidas son,
        por definicion, las que todavia no existen en el catalogo.
        """
        return self.sudo().search([('category_id', '!=', False)]).mapped('exe')

    @api.model
    def doc_apps(self):
        """Ejecutables autorizados a reportar el archivo abierto.

        La compuerta general vive AQUI, del lado del servidor, y no en el
        agente: asi apagarla no se puede rodear desde la maquina y llega a
        todos los equipos en su siguiente envio, sin tocar ninguno.
        """
        if not self.env['foco.settings'].sudo().get_settings().document_enabled:
            return []
        return self.sudo().search([('report_document', '=', True)]).mapped('exe')

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
    def _variantes_exe(self, exe):
        """Las formas en que el mismo ejecutable llega de los agentes.

        El agente de escritorio lo manda con '.exe' al reportar uso
        ('winword.exe') y sin el al subir una captura ('winword'), y el catalogo
        se llavea con la forma del uso. Buscar por una sola forma dejaba a las
        capturas sin aplicacion: el disparador de app sin clasificar nunca veia
        su propia captura y volvia a fotografiar cada media hora (46 capturas de
        una sola PC en una noche, 24-sep).
        """
        exe = (exe or '').strip().lower()
        if not exe:
            return []
        base = exe[:-4] if exe.endswith('.exe') else exe
        return [exe] + [v for v in (base, base + '.exe') if v != exe]

    @api.model
    def _por_exe(self, exe):
        """La app del catalogo para ese ejecutable, llegue con o sin '.exe'."""
        variantes = self._variantes_exe(exe)
        if not variantes:
            return self.browse()
        apps = self.search([('exe', 'in', variantes)])
        for forma in variantes:        # la forma tal como llego, primero
            app = apps.filtered(lambda a: a.exe == forma)[:1]
            if app:
                return app
        return self.browse()

    @api.model
    def _get_or_create(self, exe, name=None, product=None):
        exe = (exe or '').strip().lower()
        if not exe:
            return self.browse()
        app = self.search([('exe', '=', exe)], limit=1)
        now = fields.Datetime.now()
        limpio = name if self._nombre_valido(name) else None
        producto = (product or '').strip()[:120] or None
        yo = self.with_context(foco_agente=True)
        if app:
            vals = {'last_seen': now}
            # El nombre lo declara el binario y se mantiene al dia SOLO si el
            # admin no lo ha tomado. El admin siempre gana.
            if limpio and not app.name_manual and app.name != limpio:
                vals['name'] = limpio
            if producto and app.product != producto:
                vals['product'] = producto
            app.with_context(foco_agente=True).write(vals)
        else:
            # Al DESCUBRIRLA se propone reportar el archivo si el binario dice
            # ser paqueteria documental. Dos condiciones, las dos necesarias:
            #
            #  - Solo al CREARLA: si el admin lo apaga despues, se queda
            #    apagado. Una app descubierta no vuelve a encenderse sola a
            #    espaldas de quien la apago.
            #  - Solo si el interruptor GENERAL ya esta encendido. Si no, una
            #    app nueva se descubre apagada. Sin esta segunda condicion,
            #    actualizar el modulo y que alguien abriera PowerPoint por
            #    primera vez bastaba para empezar a recoger nombres de archivo
            #    sin que nadie lo hubiera decidido.
            general = self.env['foco.settings'].sudo().get_settings().document_enabled
            app = yo.create({
                'exe': exe, 'name': limpio or exe, 'last_seen': now,
                'product': producto,
                'report_document': (general
                                    and self._es_suite_documental(producto)),
            })
        return app
