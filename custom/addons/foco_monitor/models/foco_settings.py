import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Cuanto borra como maximo una corrida. La primera purga de un sistema con
# anos encima podria ser de cientos de miles de renglones; partirla evita
# que el proceso diario se quede colgado, y como corre todos los dias, se
# drena solo. Lo que falta se reporta, no se esconde.
PURGA_MAX_POR_CORRIDA = 20000


class FocoSettings(models.Model):
    _name = 'foco.settings'
    _description = 'Configuracion de Foco'

    name = fields.Char(default="Configuracion", readonly=True)
    sched_enabled = fields.Boolean(
        string="Aplicar horario", default=True,
        help="Si esta activo, los agentes SOLO miden dentro del horario. "
             "Si se apaga, miden todo el tiempo (24/7).")
    sched_from = fields.Float(
        string="Desde", default=9.0,
        help="Hora de inicio en formato 24h (9.0 = 09:00, 9.5 = 09:30).")
    sched_to = fields.Float(
        string="Hasta", default=20.0,
        help="Hora de fin en formato 24h (20.0 = 20:00).")
    day_mon = fields.Boolean("Lunes", default=True)
    day_tue = fields.Boolean("Martes", default=True)
    day_wed = fields.Boolean("Miercoles", default=True)
    day_thu = fields.Boolean("Jueves", default=True)
    day_fri = fields.Boolean("Viernes", default=True)
    day_sat = fields.Boolean("Sabado", default=False)
    day_sun = fields.Boolean("Domingo", default=False)

    # ---- bloqueo de navegacion ------------------------------------------
    # APAGADO de fabrica. Una funcion que puede dejar a alguien sin poder abrir
    # una pagina no se enciende sola: se enciende cuando alguien lo decide.
    block_enabled = fields.Boolean(
        string='Aplicar bloqueo de sitios', default=False,
        help='Interruptor general. Apagado, los equipos LIMPIAN la lista que '
             'tuvieran puesta; no se quedan con la ultima. Que apagarlo suelte '
             'de verdad los equipos es lo que lo hace un interruptor y no un '
             'adorno.')
    default_policy_id = fields.Many2one(
        'foco.policy', string='Perfil por omision',
        help='El que toma un equipo que no tenga uno propio. Vacio = ese equipo '
             'no bloquea nada.')

    # ---- el archivo abierto ---------------------------------------------
    # APAGADO de fabrica, y es la compuerta de TODO lo demas: mientras este
    # apagado, las marcas por aplicacion quedan inertes. Sin esto, actualizar el
    # modulo bastaria para que una app recien descubierta empezara a reportar
    # nombres de archivo sin que ninguna persona lo hubiera decidido, y eso es
    # justo lo que no puede pasar en un sistema que mide a personas.
    document_enabled = fields.Boolean(
        string='Reportar el archivo abierto', default=False,
        help='Interruptor general. Apagado, NINGUN equipo reporta el nombre de '
             'ningun archivo, sin importar que aplicaciones esten marcadas. '
             'Se puede dejar todo configurado y encenderlo el dia que el aviso '
             'de privacidad este firmado.')

    # ---- capturas de pantalla -------------------------------------------
    #
    # APAGADO de fabrica y con un motivo que no es tecnico: el aviso de
    # privacidad vigente promete EN NEGRITAS que no se toman capturas. Mientras
    # ese sea el texto firmado, encender esto rompe una promesa escrita. La
    # funcion se construye para que este lista el dia que exista un aviso nuevo
    # firmado, no para encenderla antes.
    screenshot_enabled = fields.Boolean(
        string='Capturas de pantalla', default=False,
        help='Interruptor general. Apagado, NINGUN equipo toma ni guarda una '
             'sola captura, sin importar que ordenes se manden. No lo '
             'enciendas mientras el aviso de privacidad firmado diga que no se '
             'toman capturas.')
    screenshot_unclassified_minutes = fields.Integer(
        string='Minutos en una app sin clasificar', default=30,
        help='Si alguien pasa mas de estos minutos seguidos en una aplicacion '
             'que el catalogo no conoce, el equipo toma UNA captura para poder '
             'identificarla. Esa aplicacion no vuelve a disparar nunca. '
             '0 = apagar este disparador y dejar solo las que se piden a mano.')
    screenshot_retention_days = fields.Integer(
        string='Conservar las capturas (dias)', default=30,
        help='Plazo PROPIO, aparte del resto del dato y mas corto a proposito: '
             'una imagen de la pantalla de alguien envejece mal. 0 = no borrar, '
             'que con capturas es una decision que hay que tomar a conciencia.')

    # ---- monitoreo movil (Android) --------------------------------------
    #
    # APAGADO de fabrica, misma razon que capturas: es la pieza que trae
    # UBICACION y LLAMADAS, lo mas sensible del sistema. Mientras este apagado,
    # la ingesta movil NO guarda nada aunque un telefono la mande, y el aviso de
    # privacidad tiene que cubrir ubicacion y llamadas antes de encenderlo. Son
    # telefonos PROPIEDAD DE LA EMPRESA, con consentimiento firmado.
    mobile_enabled = fields.Boolean(
        string='Monitoreo movil (Android)', default=False,
        help='Interruptor general del lado movil. Apagado, NINGUN telefono '
             'guarda ubicacion, uso de apps ni llamadas, sin importar que '
             'reporte. Enciendelo solo con el aviso de privacidad que cubra '
             'ubicacion y llamadas ya firmado.')
    mobile_location_minutes = fields.Integer(
        string='Ubicacion cada (min)', default=5,
        help='Cada cuantos minutos el telefono toma y guarda su posicion. '
             'Mas seguido = mas precision de recorrido y mas bateria. '
             '0 = no registrar ubicacion (solo apps y llamadas).')
    mobile_collect_calls = fields.Boolean(
        string='Registrar llamadas', default=True,
        help='Numero, direccion y duracion de las llamadas del telefono.')
    mobile_collect_apps = fields.Boolean(
        string='Registrar uso de apps', default=True,
        help='Segundos en primer plano por aplicacion y dia.')
    # PIN de proteccion (anti-desinstalacion) para equipos que NO se pueden
    # aprovisionar como Device Owner (telefonos ya en uso). El telefono lo pide
    # antes de dejar desinstalar Foco, forzar su detencion o desactivar su
    # administrador. Es POR EMPRESA (una sola configuracion). En un equipo que si
    # es Device Owner no hace falta: alli el bloqueo es del sistema.
    mobile_uninstall_pin = fields.Char(
        string='PIN de proteccion (desinstalar/pausar)', size=8,
        help='PIN que el telefono pide antes de dejar desinstalar Foco, forzar '
             'su detencion o desactivar su administrador, en equipos que NO son '
             'Device Owner. 4 a 8 digitos. Vacio = sin PIN.')
    # Capturas de pantalla del movil. Interruptor PROPIO (aparte del de la
    # laptop) porque un pantallazo del telefono es lo mas invasivo del sistema:
    # solo se enciende con el aviso de privacidad firmado que lo contemple, y
    # solo funciona en equipos con el servicio de accesibilidad activo (Android
    # 11+). Reusa los minutos y la retencion de las capturas de la laptop.
    mobile_screenshot_enabled = fields.Boolean(
        string='Capturas de pantalla (movil)', default=False,
        help='Apagado, ningun telefono toma pantallazos. Enciendelo solo con el '
             'aviso de privacidad firmado que contemple capturas. Requiere el '
             'servicio de accesibilidad de Foco activo (Android 11+).')
    # Apps que se fotografian PERIODICAMENTE mientras esten en primer plano (no
    # una sola vez), p.ej. WhatsApp. La lista vive en el servidor -no en el
    # telefono- para poder cambiarla sin reinstalar la app. Sembrada con los dos
    # paquetes de WhatsApp; el admin agrega o quita los que quiera, uno por linea.
    mobile_screenshot_monitor_packages = fields.Text(
        string='Apps a fotografiar cada N min (una por linea)',
        default='com.whatsapp\ncom.whatsapp.w4b',
        help='Paquetes Android que se capturan CADA CIERTO TIEMPO mientras esten '
             'en primer plano, no una sola vez, p.ej. WhatsApp '
             '(com.whatsapp) y WhatsApp Business (com.whatsapp.w4b). Uno por '
             'linea. El resto de las apps solo se capturan a mano o al '
             'descubrirlas por primera vez.')
    mobile_screenshot_monitor_minutes = fields.Integer(
        string='Cada cuantos minutos (apps monitoreadas)', default=15,
        help='Cada cuantos minutos se toma una captura mientras una app de la '
             'lista de arriba esta en primer plano. 0 = usar los mismos minutos '
             'de las apps sin clasificar.')
    mobile_screenshot_capture_unregistered = fields.Boolean(
        string='Fotografiar apps NO registradas cada N min', default=True,
        help='Encendido, cualquier app que NO este marcada como registrada en '
             'el catalogo movil (una app personal o desconocida) se fotografia '
             'cada N min mientras este al frente, igual que las monitoreadas. '
             'Las apps de trabajo registradas y las de la lista "nunca capturar" '
             'quedan fuera. Apagado, solo se toma UNA captura de descubrimiento.')

    def mobile_screenshot_monitor_list(self):
        """Los paquetes a fotografiar en forma periodica, ya limpios. Acepta
        separados por linea o por coma; ignora vacios y espacios."""
        self.ensure_one()
        txt = (self.mobile_screenshot_monitor_packages or '').replace(',', '\n')
        vistos = []
        for p in txt.splitlines():
            p = p.strip()
            if p and p not in vistos:
                vistos.append(p)
        return vistos

    @api.constrains('mobile_uninstall_pin')
    def _check_uninstall_pin(self):
        for r in self:
            p = (r.mobile_uninstall_pin or '').strip()
            if p and (not p.isdigit() or not (4 <= len(p) <= 8)):
                raise ValidationError(
                    'El PIN de proteccion debe ser de 4 a 8 digitos (o vacio).')

    def mobile_config(self):
        """Lo que el telefono necesita saber para comportarse. Viaja en el
        enrolamiento y en cada envio, para que apagarlo surta efecto rapido."""
        self.ensure_one()
        return {
            'enabled': self.mobile_enabled,
            'location_minutes': self.mobile_location_minutes or 0,
            'collect_calls': self.mobile_collect_calls,
            'collect_apps': self.mobile_collect_apps,
            'uninstall_pin': (self.mobile_uninstall_pin or '').strip(),
        }

    # ---- cuanto tiempo se guarda el dato --------------------------------
    # APAGADA de fabrica (0). Una purga encendida por omision borraria datos
    # que nadie decidio borrar, y ese borrado no se puede deshacer.
    retention_months = fields.Integer(
        string='Conservar el detalle (meses)', default=0,
        help='0 = no se borra nada, nunca. Con un numero, un proceso diario '
             'elimina el detalle de uso y los periodos sin actividad mas '
             'antiguos que esos meses. En un sistema que mide a personas, poder '
             'decir cuanto tiempo se guarda el dato es parte del sistema.')
    retention_last_run = fields.Datetime(
        string='Ultima purga', readonly=True,
        help='Cuando corrio por ultima vez. Si esta vacio con la retencion '
             'encendida, el proceso no ha corrido.')
    retention_last_deleted = fields.Integer(
        string='Borrados en la ultima purga', readonly=True)
    retention_pending = fields.Integer(
        string='Pendientes de borrar', readonly=True,
        help='Renglones que ya superaron la retencion y que la ultima corrida '
             'no alcanzo a borrar. Se van en las siguientes corridas diarias.')

    installer = fields.Binary(string="Instalador (.exe)",
                              help="El FERBA-Foco-Setup.exe que descargan los empleados desde el correo.")
    installer_name = fields.Char(string="Nombre del instalador", default="FERBA-Foco-Setup.exe")

    # ---- app movil (APK) para repartir por QR ---------------------------
    # La Play Store no admite apps de monitoreo, asi que el APK se reparte por
    # sideload: se sube aqui y se descarga desde /foco/instalar (QR + token
    # efimero). El version_code sirve para el auto-update de la app.
    mobile_apk = fields.Binary(
        string='App movil (APK)',
        help='El .apk de Foco que se instala en los telefonos de la empresa. '
             'Se descarga escaneando el QR de /foco/instalar.')
    mobile_apk_name = fields.Char(string='Nombre del APK', default='foco.apk')
    mobile_apk_version_name = fields.Char(
        string='Version de la app', help='Etiqueta visible, p.ej. 1.9.0.')
    mobile_apk_version_code = fields.Integer(
        string='Codigo de version', help='Numero entero que sube en cada '
             'version (versionCode). Lo usa el auto-update para saber si hay '
             'una mas nueva.')
    mobile_apk_token_minutes = fields.Integer(
        string='Minutos que vive el enlace del QR', default=10,
        help='Cada QR de instalacion acuña un enlace de un solo uso que caduca '
             'a estos minutos. Corto a proposito: es la seguridad real, no el '
             'codigo de teclas.')

    def mobile_apk_ready(self):
        """True si hay un APK publicado para repartir."""
        self.ensure_one()
        return bool(self.mobile_apk)

    # ---- quien puede ver Foco -------------------------------------------
    # Se administra desde aqui y no desde Ajustes > Usuarios para que el
    # responsable de Foco no necesite permisos generales de administracion de
    # Odoo. Los campos no se almacenan: son una vista de los grupos, de modo que
    # no puede haber dos verdades sobre quien tiene acceso.
    access_user_ids = fields.Many2many(
        'res.users', string='Pueden ver Foco',
        compute='_compute_accesos', inverse='_inverse_access_user',
        help='Ven el tablero y el uso. Sin estar aqui, la aplicacion no les '
             'aparece siquiera en el menu.')
    access_manager_ids = fields.Many2many(
        'res.users', string='Administran Foco',
        compute='_compute_accesos', inverse='_inverse_access_manager',
        help='Ademas de ver, clasifican apps y sitios, envian ordenes remotas y '
             'gestionan estos accesos. Incluye de forma automatica el permiso '
             'de consulta.')

    def _grupo(self, xmlid):
        return self.env.ref('foco_monitor.' + xmlid, raise_if_not_found=False)

    @api.depends_context('uid')
    def _compute_accesos(self):
        g_user = self._grupo('group_foco_user')
        g_admin = self._grupo('group_foco_manager')
        for rec in self:
            rec.access_user_ids = g_user.user_ids if g_user else False
            rec.access_manager_ids = g_admin.user_ids if g_admin else False

    def _inverse_access_user(self):
        g_user = self._grupo('group_foco_user')
        if not g_user:
            return
        for rec in self:
            g_user.sudo().user_ids = [(6, 0, rec.access_user_ids.ids)]

    def _inverse_access_manager(self):
        g_admin = self._grupo('group_foco_manager')
        if not g_admin:
            return
        for rec in self:
            g_admin.sudo().user_ids = [(6, 0, rec.access_manager_ids.ids)]

    @api.model
    def action_accesos(self):
        """Abre la pantalla de accesos sobre el registro unico de configuracion."""
        ajustes = self.get_settings()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Quien puede ver Foco',
            'res_model': 'foco.settings',
            'view_mode': 'form',
            'res_id': ajustes.id,
            'target': 'current',
            'views': [(self.env.ref('foco_monitor.foco_settings_view_form_accesos').id, 'form')],
        }

    @api.model
    def action_movil(self):
        """Abre los ajustes del monitoreo movil sobre el registro unico."""
        ajustes = self.get_settings()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Monitoreo movil',
            'res_model': 'foco.settings',
            'view_mode': 'form',
            'res_id': ajustes.id,
            'target': 'current',
            'views': [(self.env.ref('foco_monitor.foco_settings_view_form_movil').id, 'form')],
        }

    @api.model
    def action_alcance(self):
        """Abre la lista de quien tiene acceso, para repartir departamentos.

        Se limita a las personas con acceso a Foco: repartir alcance a alguien
        que ni siquiera ve la aplicacion no significa nada, y la lista completa
        de usuarios de Odoo solo haria ruido.
        """
        g_ver = self._grupo('group_foco_user')
        g_admin = self._grupo('group_foco_manager')
        usuarios = (g_ver.user_ids if g_ver else self.env['res.users'])
        usuarios |= (g_admin.user_ids if g_admin else self.env['res.users'])
        return {
            'type': 'ir.actions.act_window',
            'name': 'Que ve cada quien',
            'res_model': 'res.users',
            'view_mode': 'list',
            'views': [(self.env.ref('foco_monitor.foco_res_users_view_list_alcance').id, 'list')],
            'domain': [('id', 'in', usuarios.ids)],
            'target': 'current',
            'help': '<p class="o_view_nocontent_smiling_face">Todavia nadie '
                    'tiene acceso a Foco</p><p>Concede el acceso primero en '
                    '"Quien puede ver Foco".</p>',
        }

    @api.model
    def get_settings(self):
        return self.search([], limit=1) or self.create({})

    def schedule_dict(self):
        self.ensure_one()
        flags = [self.day_mon, self.day_tue, self.day_wed, self.day_thu,
                 self.day_fri, self.day_sat, self.day_sun]
        days = [i + 1 for i, on in enumerate(flags) if on]
        return {"enabled": self.sched_enabled, "from": self.sched_from,
                "to": self.sched_to, "days": days}

    @api.model
    def calendar_intervals(self, calendar):
        """Intervalos de trabajo por dia ISO (1=lunes), EXCLUYENDO la comida.

        Ojo: en resource.calendar la comida NO es un hueco, es una LINEA con
        day_period='lunch' (por ejemplo 12.0-13.0). Si no se excluye, se
        contaria como tiempo de trabajo.

        dayofweek de Odoo es '0'=lunes; el agente usa ISO 1=lunes.
        """
        out = {}
        if not calendar or calendar.two_weeks_calendar:
            return out          # calendario quincenal alternante: no soportado
        for att in calendar.attendance_ids:
            if att.day_period == 'lunch':
                continue
            try:
                iso = int(att.dayofweek) + 1
            except (TypeError, ValueError):
                continue
            if att.hour_to > att.hour_from:
                out.setdefault(iso, []).append((att.hour_from, att.hour_to))
        for iso in out:
            out[iso].sort()
        return out

    @api.model
    def lunch_intervals(self, calendar):
        """Franjas de comida por dia ISO. Sirven para PRESUGERIR el motivo."""
        out = {}
        if not calendar or calendar.two_weeks_calendar:
            return out
        for att in calendar.attendance_ids:
            if att.day_period != 'lunch':
                continue
            try:
                iso = int(att.dayofweek) + 1
            except (TypeError, ValueError):
                continue
            out.setdefault(iso, []).append((att.hour_from, att.hour_to))
        return out

    @api.model
    def _tzinfo_for(self, employee, computer=None):
        """La zona con la que se fecha la jornada de esa persona.

        El orden va de lo mas declarado a lo mas deducido: lo que dice RRHH
        primero, y el desfase que reporta la maquina solo cuando no hay nada
        configurado. Asumir UTC en silencio es lo que no se puede hacer: mueve
        un dia entero de actividad y no deja rastro de por que.
        """
        # Manda lo que REPORTA la maquina, y no es un capricho: el agente ya
        # aplica el horario con su propio reloj (in_schedule usa la hora local
        # del equipo), asi que ese reloj ya era la autoridad de facto para
        # decidir que es "dentro de jornada". Alinear el servidor a el reduce el
        # numero de verdades en vez de aumentarlo. Ademas trae el horario de
        # verano ya aplicado por Windows, y sigue a la persona si trabaja desde
        # otro pais.
        #
        # La zona configurada en Odoo queda de respaldo porque en una base nueva
        # NADIE la decide: Odoo rellena 'UTC' solo, copiandola del usuario que
        # creo al empleado. Tratar ese relleno como una decision es lo que movia
        # un dia entero de actividad todas las noches, en silencio.
        if computer is None and employee:
            computer = self.env['foco.computer'].sudo().search(
                [('employee_id', '=', employee.id),
                 ('utc_offset_min', '!=', 0)], limit=1)
        if computer and computer.utc_offset_min:
            return pytz.FixedOffset(computer.utc_offset_min)
        nombre = None
        if employee:
            nombre = employee.tz or employee.resource_calendar_id.tz
        nombre = nombre or self.env.company.resource_calendar_id.tz
        if nombre:
            try:
                return pytz.timezone(nombre)
            except Exception:
                pass
        return pytz.UTC

    @api.model
    def _tz_for(self, employee):
        if employee:
            return (employee.tz
                    or employee.resource_calendar_id.tz
                    or self.env.company.resource_calendar_id.tz
                    or 'UTC')
        return self.env.company.resource_calendar_id.tz or 'UTC'

    def schedule_for(self, employee=None):
        """Jornada que se le manda al agente de ese equipo.

        Preferencia: calendario del empleado > horario global > apagado.
        """
        self.ensure_one()
        if not self.sched_enabled:
            return {"enabled": False, "intervals": {}, "source": "off", "tz": ""}
        calendar = employee.resource_calendar_id if employee else None
        intervals = self.calendar_intervals(calendar)
        if intervals:
            return {"enabled": True,
                    "tz": calendar.tz or self._tz_for(employee),
                    "source": "employee_calendar",
                    "intervals": {str(k): v for k, v in intervals.items()}}
        data = self.schedule_dict()
        data["source"] = "global"
        data["tz"] = self._tz_for(employee)
        return data

    # ---- purga ----------------------------------------------------------
    def _purgar_modelo(self, modelo, dominio, cupo):
        """Borra hasta `cupo` renglones y dice cuantos quedaron.

        Devuelve (borrados, pendientes). El pendiente es un conteo real, no una
        estimacion: es la diferencia entre lo que cumple el criterio y lo que
        se alcanzo a borrar.
        """
        Modelo = self.env[modelo].sudo()
        total = Modelo.search_count(dominio)
        if not total:
            return 0, 0
        registros = Modelo.search(dominio, limit=cupo)
        borrados = len(registros)
        # De mil en mil: un unlink de veinte mil de golpe arma una sentencia
        # que no le hace ningun favor a la base.
        for i in range(0, borrados, 1000):
            registros[i:i + 1000].unlink()
        return borrados, total - borrados

    @api.model
    def _cron_purgar(self):
        """Borra el detalle mas viejo que la retencion configurada.

        Con retention_months = 0 no borra nada y lo deja anotado en el registro:
        un proceso de borrado que corre en silencio, y del que no se sabe si
        hizo algo, es peor que no tenerlo.
        """
        ajustes = self.get_settings()

        # Las capturas se purgan SIEMPRE que tengan plazo, aunque la retencion
        # general este apagada. Son dos decisiones distintas: alguien puede
        # querer conservar el detalle de uso indefinidamente y aun asi no
        # querer guardar fotografias de la pantalla de su gente un ano.
        ahora = fields.Datetime.now()

        # 1) Monitoreo periodico: cada imagen trae SU fecha de caducidad
        #    (foco.watch.retention_days, por empleado y app), fijada al guardar.
        #    Con plazos distintos por regla, una sola consulta por `expires_at`
        #    las purga todas -no se puede con un corte global unico-.
        n, quedan = self._purgar_modelo(
            'foco.capture',
            [('trigger', '=', 'monitoreo'),
             ('expires_at', '!=', False), ('expires_at', '<', ahora)],
            PURGA_MAX_POR_CORRIDA)
        if n or quedan:
            _logger.info('Foco: capturas de monitoreo caducadas -> %d borradas, '
                         'quedan %d', n, quedan)

        # 2) Manual y "sin clasificar": por el plazo global de capturas.
        dias_img = ajustes.screenshot_retention_days or 0
        if dias_img > 0:
            corte_img = ahora - timedelta(days=dias_img)
            n, quedan = self._purgar_modelo(
                'foco.capture',
                [('trigger', 'in', ['manual', 'sin_clasificar']),
                 ('at', '<', corte_img)],
                PURGA_MAX_POR_CORRIDA)
            if n or quedan:
                _logger.info('Foco: capturas anteriores a %s -> %d borradas, '
                             'quedan %d', corte_img, n, quedan)

        meses = ajustes.retention_months or 0
        if meses <= 0:
            _logger.info('Foco: retencion apagada, no se purga nada')
            return 0

        corte = fields.Date.subtract(fields.Date.context_today(self), months=meses)
        corte_dt = datetime.combine(corte, time.min)

        # Todo lo que fecha a una persona entra a la purga, no solo el uso:
        # dejar fuera los eventos del equipo o la jornada convertiria la
        # promesa de "se conservan N meses" en una verdad a medias.
        objetivos = [
            ('foco.usage', [('date', '<', corte)]),
            ('foco.absence', [('start', '<', corte_dt)]),
            ('foco.event', [('at', '<', corte_dt)]),
            ('foco.workday', [('date', '<', corte)]),
            # Lado movil: la ubicacion es lo que mas crece, y fecha a una persona
            # igual que el resto, asi que entra a la misma retencion.
            ('foco.location', [('at', '<', corte_dt)]),
            ('foco.mobile.usage', [('date', '<', corte)]),
            ('foco.call', [('at', '<', corte_dt)]),
        ]
        cupo = PURGA_MAX_POR_CORRIDA
        borrados = pendientes = 0
        detalle = []
        for modelo, dominio in objetivos:
            if cupo > 0:
                n, quedan = self._purgar_modelo(modelo, dominio, cupo)
                cupo -= n
            else:
                n = 0
                quedan = self.env[modelo].sudo().search_count(dominio)
            borrados += n
            pendientes += quedan
            detalle.append('%s=%d' % (modelo, n))

        ajustes.sudo().write({
            'retention_last_run': fields.Datetime.now(),
            'retention_last_deleted': borrados,
            'retention_pending': pendientes,
        })
        _logger.info('Foco: purga anterior a %s -> %s, quedan %d',
                     corte, ', '.join(detalle), pendientes)
        return borrados

    @api.model
    def cobertura_horario(self):
        """A quien gobierna DE VERDAD este horario.

        schedule_for() da preferencia al calendario laboral del empleado sobre
        este horario global. Es lo correcto -la jornada la define RRHH, no una
        pantalla de Foco- pero deja un ajuste que para parte de la plantilla no
        surte ningun efecto, sin decirlo. Un control que no aplica y no lo
        avisa es peor que no tenerlo: se configura, se cree aplicado, y los
        numeros salen distintos por una razon invisible.

        Devuelve el reparto real sobre las personas MONITOREADAS (las que
        tienen equipo), no sobre toda la plantilla.
        """
        empleados = self.env['foco.computer'].search(
            [('employee_id', '!=', False)]).mapped('employee_id')
        sin_cal = empleados.filtered(lambda e: not e.resource_calendar_id)
        con_cal = empleados - sin_cal

        por_calendario = {}
        for emp in con_cal:
            cal = emp.resource_calendar_id
            grupo = por_calendario.setdefault(
                cal.id, {'id': cal.id, 'nombre': cal.name or 'Sin nombre', 'quienes': []})
            grupo['quienes'].append(emp.name)
        calendarios = sorted(por_calendario.values(),
                             key=lambda g: (-len(g['quienes']), g['nombre']))
        for g in calendarios:
            g['personas'] = len(g['quienes'])

        return {
            'total': len(empleados),
            'global': len(sin_cal),
            'con_calendario': len(con_cal),
            'nombres_global': sin_cal.mapped('name')[:10],
            'calendarios': calendarios,
        }

    @api.model
    def expected_seconds(self, employee, start_utc, stop_utc):
        """Segundos de JORNADA ESPERADA dentro de [start_utc, stop_utc].

        Es lo que permite no molestar al empleado por huecos que caen fuera de
        su jornada (o en su comida): ahi el esperado es 0.
        """
        if stop_utc <= start_utc:
            return 0.0
        calendar = employee.resource_calendar_id if employee else None
        intervals = self.calendar_intervals(calendar)
        if not intervals:
            # Sin calendario utilizable no se puede afirmar que estuviera libre:
            # se asume esperado para que alguien lo revise.
            return (stop_utc - start_utc).total_seconds()
        tz = self._tzinfo_for(employee)
        ini = pytz.UTC.localize(start_utc).astimezone(tz)
        fin = pytz.UTC.localize(stop_utc).astimezone(tz)
        total = 0.0
        day = ini.date()
        while day <= fin.date():
            base = tz.localize(datetime.combine(day, time(0, 0)))
            for h_from, h_to in intervals.get(day.isoweekday(), []):
                span_a = base + timedelta(hours=h_from)
                span_b = base + timedelta(hours=h_to)
                lo = max(ini, span_a)
                hi = min(fin, span_b)
                if hi > lo:
                    total += (hi - lo).total_seconds()
            day += timedelta(days=1)
        return total
