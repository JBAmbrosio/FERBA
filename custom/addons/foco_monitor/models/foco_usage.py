from datetime import timedelta

from odoo import api, fields, models


class FocoUsage(models.Model):
    _name = 'foco.usage'
    _description = 'Uso diario por aplicacion'
    _order = 'date desc, fg_active desc'

    _uniq_day = models.Constraint(
        'unique(computer_id, app_id, date, host, shift, document, call_ref)',
        'Ya existe un renglon de uso para ese equipo/app/dia/sitio/turno/archivo/llamada.')

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', store=True, string='Empleado')
    department_id = fields.Many2one(
        related='computer_id.department_id', store=True, string='Departamento')
    app_id = fields.Many2one(
        'foco.app', string='Aplicacion', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True, index=True)
    shift = fields.Selection(
        [('in', 'Dentro de jornada'), ('off', 'Fuera de jornada')],
        string='Turno', default='in', required=True, index=True,
        help='El horario dejo de decidir SI se mide y pasa a decidir COMO SE '
             'LLAMA lo medido. Va en la llave y no en una columna aparte para '
             'que agrupar por turno funcione en pivots, graficos y filtros sin '
             'que cada reporte tenga que enterarse de que existen dos familias '
             'de columnas.')
    document = fields.Char(
        string='Archivo', default='', index=True,
        help='Nombre del archivo abierto en esa aplicacion. Vacio si la app no '
             'tiene habilitado reportarlo, que es lo de fabrica.\n\n'
             'Va en la LLAVE, igual que el sitio: si fuera una columna mas, '
             'todo Excel caeria en un solo renglon y solo se sabria el ultimo '
             'archivo del dia. Asi cada archivo lleva su propio tiempo y se '
             'puede contestar "estuvo 3 h en el 2704".\n\n'
             'Es el NOMBRE, nunca la ruta ni el contenido.')
    host = fields.Char(
        string='Sitio', default='', index=True,
        help='Dominio de la pestana activa cuando la app es un navegador. '
             'Vacio si no aplica. Sin esto, todo el navegador caeria en un '
             'solo renglon y no se podria distinguir YouTube de Odoo.')
    site_id = fields.Many2one(
        'foco.site', string='Sitio (catalogo)', ondelete='set null', index=True,
        help='El sitio como entidad clasificable. Es lo que permite que una '
             'hora en el ERP y una hora en YouTube dejen de valer lo mismo '
             'solo porque las dos ocurrieron en el navegador.')
    # El grupo/chat de WhatsApp abierto en este renglon (requisito de Francesco,
    # 9-oct-2026). Es a WhatsApp lo que el sitio al navegador: lo que permite que
    # el grupo de ventas cuente como productivo y uno de ocio como distraccion,
    # aunque los dos sean "WhatsApp". El nombre viene de `document` (titulo del
    # chat); lo resuelve el ingest. Vacio = ese renglon no es un chat de WhatsApp.
    wa_group_id = fields.Many2one(
        'foco.whatsapp.group', string='Grupo de WhatsApp',
        ondelete='set null', index=True,
        help='El grupo o chat que estaba abierto. Si esta clasificado, su '
             'categoria MANDA sobre la de WhatsApp.')
    host_status = fields.Selection(
        [('ok', 'Leido'), ('typing', 'Escribiendo'),
         ('unreadable', 'Navegador no identificado'),
         ('not_browser', 'No aplica'), ('pending', 'Pendiente')],
        string='Lectura del sitio', default='not_browser',
        help='Por que el sitio es el que es. "Navegador no identificado" es una '
             'anomalia visible, no se descarta en silencio.')

    # La categoria ya NO se hereda ciegamente de la app: si el sitio esta
    # clasificado, MANDA el sitio. Sin esto el navegador es un agujero negro en
    # el que todo pesa igual, y hoy el navegador es donde pasa el dia.
    category_id = fields.Many2one(
        'foco.category', string='Categoria', store=True, index=True,
        compute='_compute_category_id',
        help='Del SITIO si esta clasificado; si no, de la aplicacion.')
    category_source = fields.Selection(
        [('whatsapp', 'Grupo de WhatsApp'),
         ('perfil_sitio', 'Sitio (de este perfil)'),
         ('site', 'Sitio'),
         ('perfil_app', 'Aplicacion (de este perfil)'),
         ('app', 'Aplicacion'), ('none', 'Sin clasificar')],
        string='Origen de la categoria', store=True, compute='_compute_category_id',
        help='De donde salio el peso de este renglon. Que el numero pueda '
             'explicarse es parte del numero.')

    fg_active = fields.Float(string='1er plano activo (h)',
                             help='Horas de uso real: con foco y con teclado/mouse.')
    fg_idle = fields.Float(
        string='1er plano sin input (h)',
        help='La app tenia el foco pero no hubo teclado ni mouse durante el '
             'umbral que tenga configurado el agente de ese equipo (60 s de '
             'fabrica, cambiable con FOCO_IDLE). NO es '
             'automaticamente ociosidad: leer, revisar o pensar frente a la '
             'pantalla cae aqui. Se guarda aparte y NO cuenta como productivo; '
             'se muestra para que una persona lo interprete.')
    background = fields.Float(string='2do plano (h)',
                              help='Abierta pero sin foco. No es trabajo.')
    injected_hours = fields.Float(
        string='Sintetico sin input real (h)',
        help='Horas "activas" con input generado por software y NADA real en '
             'los ultimos tres minutos: la firma de un jiggler. Es un HECHO '
             'verificable: Windows marca el input inyectado. No se descuenta '
             'del total: se deja a la vista con su evidencia para que una '
             'persona lo juzgue. Hasta el agente 2026.09.27 aqui caia tambien '
             'el inyectado CON input real (drivers, macros, soporte remoto), '
             'que ahora va aparte.')
    injected_tool_hours = fields.Float(
        string='Inyectado con input real (h)',
        help='Horas activas en las que hubo input inyectado por software Y '
             'tambien input real de la persona: un raton 3D, un software de '
             'mouse con macros, soporte remoto. No es una persona ausente; se '
             'muestra para poder ponerle nombre a la herramienta.')
    nokey_hours = fields.Float(
        string='Activo sin teclear (h)',
        help='Horas "activas" sin una sola tecla en tres minutos: solo mouse. '
             'Es la evidencia contra un jiggler de HARDWARE, que Windows ve '
             'como input real. Un numero para interpretar, no un veredicto: '
             'revisar planos o leer con el mouse tambien cae aqui.')
    agent_code = fields.Integer(
        string='Version del agente (codigo)', index=True,
        help='El codigo de version del agente que reporto este renglon por ultima '
             'vez. Existe porque el SIGNIFICADO de algunas columnas cambio con la '
             'version: "sintetico" antes del 2026.09.28 (202609280) mezclaba '
             'jigglers y herramientas que inyectan; desde esa version es solo '
             'sin input real. Los hechos de integridad solo se calculan sobre '
             'renglones de agentes que saben medirlos. 0 = anterior al sello.')
    static_hours = fields.Float(
        string='Activo con pantalla sin cambio (h)',
        help='Horas "activas" en las que la pantalla quedo identica a la huella '
             'anterior (32x32 en gris, cada tantos segundos segun el perfil): '
             'hubo teclado o mouse y nada cambio en pantalla. El trabajo real '
             'cambia la pantalla; un mouse que se mueve sin hacer nada, no. '
             'Evidencia, no veredicto: se muestra con la app.')
    keys_count = fields.Integer(
        string='Teclas', help='CUANTAS teclas se pulsaron con esta app al frente; '
                              'nunca cuales.')
    mouse_events = fields.Integer(string='Eventos de mouse')
    positions_count = fields.Integer(
        string='Posiciones del cursor',
        help='Cuantos puntos distintos (en cuadros de 8 px) toco el cursor, '
             'sumando ventanas de 2 s. Un jiggler de hardware da muy pocos.')
    call_noinput_hours = fields.Float(
        string='En llamada sin tocar nada (h)',
        help='Tiempo acreditado por estar en llamada pero sin teclado ni mouse. '
             'Puede ser una junta escuchando (legitimo) o una sala vacia dejada '
             'abierta. No se descuenta; queda visible.')
    call_hours = fields.Float(
        string='En llamada (h)',
        help='Subconjunto de las horas activas que transcurrio en una llamada. '
             'Estar en una junta solo escuchando ES trabajo; este campo explica '
             'por que ese tiempo conto aunque no hubiera teclado ni mouse.')

    active_hours = fields.Float(
        string='Horas activas', compute='_compute_metrics', store=True,
        help='Uso en primer plano, excluyendo categorias de sistema.')
    productive_hours = fields.Float(
        string='Horas productivas', compute='_compute_metrics', store=True,
        help='Horas activas ponderadas por el peso de la categoria.')

    @api.depends('wa_group_id', 'wa_group_id.category_id',
                 'site_id', 'site_id.category_id', 'app_id', 'app_id.category_id',
                 'computer_id', 'computer_id.policy_id')
    def _compute_category_id(self):
        # La categoria se resuelve de lo MAS ESPECIFICO a lo general:
        #   1) grupo de WhatsApp (el mas fino)
        #   2) SITIO clasificado EN ESTE PERFIL   (gana sobre el global)
        #   3) sitio clasificado global
        #   4) APP clasificada EN ESTE PERFIL     (gana sobre la global)
        #   5) app clasificada global
        # El "perfil" es el EFECTIVO del equipo (el suyo o el de omision), igual
        # que foco.policy._perfil_vigente: asi WhatsApp cuenta productivo para un
        # perfil y distraccion para otro (reunion 9-oct-2026).
        PolicyCat = self.env['foco.policy.category'].sudo()
        try:
            default_pol = self.env['foco.settings'].sudo().get_settings()\
                .default_policy_id.filtered('active')
        except Exception:
            default_pol = self.env['foco.policy']

        def perfil(computer):
            propio = computer.policy_id.filtered('active')
            return (propio or default_pol)[:1]

        perfiles = self.env['foco.policy']
        for rec in self:
            perfiles |= perfil(rec.computer_id)
        por_sitio, por_app = {}, {}
        if perfiles:
            for o in PolicyCat.search([('policy_id', 'in', perfiles.ids)]):
                if o.site_id:
                    por_sitio[(o.policy_id.id, o.site_id.id)] = o.category_id
                elif o.app_id:
                    por_app[(o.policy_id.id, o.app_id.id)] = o.category_id

        Cat = self.env['foco.category']
        for rec in self:
            pid = perfil(rec.computer_id).id
            site, app = rec.site_id, rec.app_id
            cat, src = Cat, 'none'
            if rec.wa_group_id.category_id:
                cat, src = rec.wa_group_id.category_id, 'whatsapp'
            elif site and pid and por_sitio.get((pid, site.id)):
                cat, src = por_sitio[(pid, site.id)], 'perfil_sitio'
            elif site.category_id:
                cat, src = site.category_id, 'site'
            elif app and pid and por_app.get((pid, app.id)):
                cat, src = por_app[(pid, app.id)], 'perfil_app'
            elif app.category_id:
                cat, src = app.category_id, 'app'
            rec.category_id = cat.id if cat else False
            rec.category_source = src

    # El rato de una llamada de WhatsApp analizada. Va EN LA LLAVE, como el
    # sitio y el archivo: asi "Excel durante la llamada #12" es su propio
    # renglon y el veredicto de esa llamada le pone el peso a ESE rato y no al
    # dia entero. Vacio = ese rato no fue de una llamada analizada.
    call_ref = fields.Char(
        string='Llamada (ref)', default='', index=True,
        help='Referencia de la llamada de WhatsApp analizada durante la que se '
             'midio este rato. Vacio = no fue durante una llamada analizada.')
    call_id = fields.Many2one(
        'foco.call.review', string='Llamada analizada', ondelete='set null', index=True,
        help='El veredicto de esta llamada manda sobre el peso de la app: '
             'trabajo = productivo, personal = no productivo.')

    @api.depends('fg_active', 'category_id',
                 'category_id.weight', 'category_id.is_system',
                 'call_id', 'call_id.clasificacion')
    def _compute_metrics(self):
        for rec in self:
            cat = rec.category_id
            if cat and cat.is_system:
                rec.active_hours = 0.0
                rec.productive_hours = 0.0
            else:
                rec.active_hours = rec.fg_active
                peso = cat.weight if cat else 0.0
                # Una llamada de trabajo es trabajo aunque al frente estuviera
                # WhatsApp (peso 0); una personal no lo es aunque al frente
                # estuviera Excel. El veredicto manda; sin veredicto, la app.
                if rec.call_id:
                    peso_llamada = rec.call_id.weight()
                    if peso_llamada is not None:
                        peso = peso_llamada
                rec.productive_hours = rec.fg_active * peso

    # ------------------------------------------------------------ analitica
    #
    # Lo que pinta el tablero, agregado EN EL SERVIDOR.
    #
    # La version anterior se traia al navegador TODOS los renglones de uso del
    # periodo y sumaba en JavaScript. Con un equipo y una semana se aguanta;
    # con treinta equipos y un mes son cientos de miles de renglones viajando
    # por la red para producir veinte numeros. Y tenia un defecto que se veia en
    # la pantalla: no pedia el campo `date`, asi que NO PODIA dibujar una serie
    # de tiempo aunque quisiera. Un tablero sin serie de tiempo no contesta la
    # unica pregunta que un administrador hace a diario -si vamos mejor o peor-.
    #
    # `_read_group` hace las tres agregaciones en la base y devuelve decenas de
    # filas en vez de cientos de miles.

    @api.model
    def _indice(self, productivo, cubierto, activo):
        """UN solo indice en todo Foco (10-oct-2026): cubierto (productivo +
        justificado) entre la jornada esperada, tope 100. Es el de la tarjeta,
        la tabla y el reporte diario. Sin jornada esperada (fin de semana, sin
        calendario) se cae a productivo/activo. None cuando no hay dato."""
        exp = float((cubierto or {}).get('expected') or 0.0)
        jus = float((cubierto or {}).get('justified') or 0.0)
        productivo = float(productivo or 0.0)
        activo = float(activo or 0.0)
        if not activo and not jus:
            return None
        if exp > 0:
            return round(min(100.0, 100.0 * (productivo + jus) / exp), 1)
        if activo:
            return round(100.0 * productivo / activo, 1)
        return None

    @api.model
    def _suma_cubierto(self, por_dia):
        """Suma {dia: {expected, justified}} en un solo {expected, justified}."""
        tot = {'expected': 0.0, 'justified': 0.0}
        for v in (por_dia or {}).values():
            tot['expected'] += float(v.get('expected') or 0.0)
            tot['justified'] += float(v.get('justified') or 0.0)
        return tot

    @api.model
    def analitica(self, desde, hasta):
        """Serie por dia, comparacion por persona y reparto del equipo.

        Devuelve SIEMPRE la misma forma, tambien cuando no hay dato: una
        pantalla que recibe `null` a media carga se rompe de una manera que
        parece un error del servidor y no un periodo vacio.
        """
        desde = fields.Date.to_date(desde)
        hasta = fields.Date.to_date(hasta)
        vacio = {'dias': [], 'empleados': [], 'reparto': {}, 'total': {}}
        if not desde or not hasta or hasta < desde:
            return vacio

        dominio = [('date', '>=', desde), ('date', '<=', hasta)]

        # --- serie por dia --------------------------------------------------
        # El indice es productivo/activo, y se calcula sobre los SUMADOS del
        # dia, no promediando los indices de cada persona: un promedio de
        # porcentajes le da el mismo peso a quien trabajo ocho horas que a
        # quien trabajo veinte minutos.
        # `date:day`, no `date`: Odoo 19 exige declarar la granularidad al
        # agrupar por una fecha y revienta con «Granularity not set on a
        # date(time) field» si se omite. Con `:day` la clave que vuelve sigue
        # siendo un `date`, asi que el relleno de dias de abajo no cambia.
        por_dia = {}
        for grupo in self._read_group(
                dominio, ['date:day'],
                ['active_hours:sum', 'productive_hours:sum', 'fg_active:sum']):
            dia, activo, productivo, bruto = grupo
            por_dia[dia] = {
                'date': fields.Date.to_string(dia),
                'activo': round(activo or 0.0, 3),
                'productivo': round(productivo or 0.0, 3),
                'bruto': round(bruto or 0.0, 3),
            }

        # Los dias SIN dato aparecen en la lista -para que el eje no se salte
        # fechas- pero con el indice en `None`, NO en cero.
        #
        # La diferencia no es cosmetica. Un sabado sin dato pintado como 0%
        # dibuja una caida a cero y una recuperacion el lunes: la grafica cuenta
        # que el equipo dejo de rendir, cuando lo que paso es que nadie
        # trabajo. `None` deja el hueco visible, que es la verdad.
        # El indice es el MISMO de la tarjeta, la tabla y el reporte diario:
        # cubierto (productivo + justificado) entre la jornada esperada.
        # `cubierto_por_dia` trae jornada y justificado por dia y por persona
        # de toda la gente monitoreada. Productivo/activo queda aparte, con su
        # nombre (`indice_actividad`), para quien quiera la calidad del activo.
        Absence = self.env['foco.absence']
        cub = Absence.cubierto_por_dia(desde, hasta)
        cub_dias = cub.get('dias') or {}
        cub_emp = cub.get('empleados') or {}
        dias = []
        d = desde
        while d <= hasta:
            f = por_dia.get(d) or {'date': fields.Date.to_string(d),
                                   'activo': 0.0, 'productivo': 0.0, 'bruto': 0.0}
            f['indice'] = self._indice(f['productivo'], cub_dias.get(f['date']), f['activo'])
            f['indice_actividad'] = (round(100.0 * f['productivo'] / f['activo'], 1)
                                     if f['activo'] else None)
            dias.append(f)
            d += timedelta(days=1)

        # --- por persona, partido en lo que se puede explicar ----------------
        # Tres cubetas y no cinco: productivo, distraccion y sin clasificar.
        # «Sin clasificar» es una cubeta de pleno derecho porque es la unica
        # accionable -y si se escondiera dentro de «otros», nadie la atenderia-.
        gente = {}
        for grupo in self._read_group(
                dominio, ['employee_id', 'category_id'],
                ['active_hours:sum', 'productive_hours:sum']):
            emp, cat, activo, productivo = grupo
            if not emp:
                continue
            e = gente.setdefault(emp.id, {
                'id': emp.id, 'nombre': emp.display_name,
                'activo': 0.0, 'productivo': 0.0,
                'distraccion': 0.0, 'sin_clasificar': 0.0})
            activo = activo or 0.0
            e['activo'] += activo
            e['productivo'] += productivo or 0.0
            if not cat:
                e['sin_clasificar'] += activo
            elif not cat.is_system and (cat.weight or 0.0) <= 0.0:
                e['distraccion'] += activo

        # --- el periodo ANTERIOR, del mismo largo, para poder decir "subio" --
        # Un numero sin con que compararse no se puede leer. El periodo previo
        # es del mismo largo a proposito: comparar una semana contra un mes
        # produce una flecha que miente.
        largo = (hasta - desde).days + 1
        prev_hasta = desde - timedelta(days=1)
        prev_desde = prev_hasta - timedelta(days=largo - 1)
        cub_prev = Absence.cubierto_por_dia(prev_desde, prev_hasta).get('empleados') or {}
        previo = {}
        for grupo in self._read_group(
                [('date', '>=', prev_desde), ('date', '<=', prev_hasta)],
                ['employee_id'], ['active_hours:sum', 'productive_hours:sum']):
            emp, activo, productivo = grupo
            if emp and activo:
                previo[emp.id] = self._indice(
                    productivo or 0.0, self._suma_cubierto(cub_prev.get(str(emp.id))), activo)

        empleados = []
        for e in gente.values():
            e['indice_actividad'] = round(100.0 * e['productivo'] / e['activo'], 1) if e['activo'] else 0.0
            e['indice'] = self._indice(
                e['productivo'], self._suma_cubierto(cub_emp.get(str(e['id']))), e['activo'])
            if e['indice'] is None:
                e['indice'] = 0.0
            antes = previo.get(e['id'])
            # `None` y `0` no son lo mismo: sin periodo previo no hay flecha
            # que dibujar, y una flecha de "+0" afirmaria que se midio.
            e['delta'] = round(e['indice'] - antes, 1) if antes is not None else None
            e['otro'] = round(max(e['activo'] - e['productivo']
                                  - e['distraccion'] - e['sin_clasificar'], 0.0), 3)
            for k in ('activo', 'productivo', 'distraccion', 'sin_clasificar'):
                e[k] = round(e[k], 3)
            empleados.append(e)
        empleados.sort(key=lambda x: -x['activo'])

        # --- hechos de integridad del periodo, por persona --------------------
        # Van con el resto de la analitica para que la fila del tablero los
        # muestre sin otra consulta. Con el env del usuario: su alcance manda.
        try:
            hechos = self.env['foco.integrity.fact'].resumen(
                desde, hasta, [e['id'] for e in empleados])
        except Exception:
            hechos = {}
        for e in empleados:
            lista = hechos.get(e['id']) or []
            # `mantenimiento_dias` y no solo el "si" agregado: un solo dia con
            # mantenimiento tapaba a los limpios en la etiqueta del tablero.
            e['hechos'] = [{'etiqueta': h['etiqueta'], 'texto': h['texto'], 'dias': h['dias'],
                            'mantenimiento': h['mantenimiento'],
                            'mantenimiento_dias': h.get('mantenimiento_dias', 0),
                            'kind': h['kind']}
                           for h in lista[:6]]
            e['hechos_n'] = len(lista)

        # --- veredictos de la IA del periodo, por persona ----------------------
        # Solo administradores ven los episodios (ACL): a los demas, ceros y
        # `ia_visible` en falso, y el tablero no dibuja la tarjeta. Cuenta los
        # analizados, los que NO parecen trabajo y nadie descarto, y los que
        # esperan revision.
        ia = {}
        ia_visible = False
        try:
            Ver = self.env['foco.integrity.verdict']
            ia_visible = bool(Ver.has_access('read')) if hasattr(Ver, 'has_access') \
                else bool(Ver.check_access_rights('read', raise_exception=False))
            if ia_visible and empleados:
                import pytz
                from datetime import datetime as _dt, time as _time
                try:
                    tz = pytz.timezone(self.env.user.tz or self.env.company.partner_id.tz or 'America/Mazatlan')
                except Exception:
                    tz = pytz.timezone('America/Mazatlan')
                ini = tz.localize(_dt.combine(desde, _time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
                fin = tz.localize(_dt.combine(hasta, _time.max)).astimezone(pytz.UTC).replace(tzinfo=None, microsecond=0)
                for v in Ver.search([('employee_id', 'in', [e['id'] for e in empleados]),
                                     ('state', '=', 'analizado'),
                                     ('started_at', '>=', ini), ('started_at', '<=', fin)]):
                    x = ia.setdefault(v.employee_id.id, {'analizados': 0, 'sospechosos': 0,
                                                          'sin_revisar': 0, 'confirmados': 0, 'minutos': 0.0})
                    x['analizados'] += 1
                    if v.sospechoso and v.review_outcome != 'descartado':
                        x['sospechosos'] += 1
                        x['minutos'] += v.duration_min or 0.0
                        if v.review_outcome == 'pendiente':
                            x['sin_revisar'] += 1
                        elif v.review_outcome == 'confirmado':
                            x['confirmados'] += 1
        except Exception:
            ia, ia_visible = {}, False
        for e in empleados:
            x = ia.get(e['id']) or {'analizados': 0, 'sospechosos': 0, 'sin_revisar': 0,
                                    'confirmados': 0, 'minutos': 0.0}
            x['minutos'] = round(x['minutos'], 1)
            e['ia'] = x

        total_activo = sum(e['activo'] for e in empleados)
        total_prod = sum(e['productivo'] for e in empleados)
        # La jornada del EQUIPO suma a toda la gente monitoreada, tenga renglon
        # o no: quien no reporto nada tambien debia cubrir la suya.
        tot_cub = self._suma_cubierto(cub_dias)
        return {
            'dias': dias,
            'empleados': empleados,
            'ia_visible': ia_visible,
            'reparto': {
                'productivo': round(total_prod, 3),
                'distraccion': round(sum(e['distraccion'] for e in empleados), 3),
                'sin_clasificar': round(sum(e['sin_clasificar'] for e in empleados), 3),
            },
            'total': {
                'activo': round(total_activo, 3),
                'productivo': round(total_prod, 3),
                'indice': self._indice(total_prod, tot_cub, total_activo) or 0.0,
                'indice_actividad': round(100.0 * total_prod / total_activo, 1) if total_activo else 0.0,
                'esperado': round(tot_cub['expected'], 3),
                'justificado': round(tot_cub['justified'], 3),
                'desde': fields.Date.to_string(desde),
                'hasta': fields.Date.to_string(hasta),
                'dias_periodo': largo,
            },
        }

    @api.model
    def cobertura(self, desde, hasta):
        """Que tan COMPLETO esta el dato de cada persona en el periodo.

        Sin esto se comparan cosas que no son comparables. Un 62 % de indice
        sobre 22 dias medidos y un 62 % sobre 9 dias -porque el agente estuvo
        caido- se ven identicos en el tablero, y cualquier decision tomada
        sobre esa comparacion es un acto de fe.

        Se mide en DIAS, no en horas: dias laborables del calendario de la
        persona contra dias en los que su equipo reporto algo. Es lo que se
        puede explicar en una frase, que es el requisito de un numero que va a
        usarse para juzgar a alguien.

        NO devuelve un veredicto ni un umbral: devuelve el hecho, para que
        quien mire decida si esos numeros se pueden comparar.
        """
        desde = fields.Date.to_date(desde)
        hasta = fields.Date.to_date(hasta)
        if not desde or not hasta or hasta < desde:
            return {}

        Ajustes = self.env['foco.settings']
        empleados = self.env['foco.computer'].search(
            [('employee_id', '!=', False)]).mapped('employee_id')

        # Un calendario se traduce UNA vez, no una por empleado.
        por_calendario = {}
        for cal in empleados.mapped('resource_calendar_id'):
            por_calendario[cal.id] = set(Ajustes.calendar_intervals(cal).keys())

        dias = []
        d = desde
        while d <= hasta:
            dias.append(d)
            d += timedelta(days=1)

        # Dias con dato: una sola consulta agrupada, no una por persona.
        grupos = self._read_group(
            [('date', '>=', desde), ('date', '<=', hasta),
             ('employee_id', 'in', empleados.ids)],
            ['employee_id', 'date:day'], ['__count'])
        con_dato = {}
        for empleado, dia, _n in grupos:
            con_dato.setdefault(empleado.id, set()).add(
                dia.date() if hasattr(dia, 'date') else dia)

        salida = {}
        for emp in empleados:
            laborables = por_calendario.get(emp.resource_calendar_id.id)
            if laborables:
                esperados = [d for d in dias if d.isoweekday() in laborables]
            else:
                # Sin calendario propio no hay jornada que contrastar: se toma
                # el periodo completo y se dice de donde salio.
                esperados = list(dias)
            medidos = con_dato.get(emp.id, set())
            n_esp = len(esperados)
            n_med = len([d for d in esperados if d in medidos])
            salida[str(emp.id)] = {
                'dias_con_dato': n_med,
                'dias_esperados': n_esp,
                'pct': round(100.0 * n_med / n_esp) if n_esp else 0,
                'fuente': 'calendario' if laborables else 'periodo',
            }
        return salida

    # ------------------------------------------------------------ desglose
    #
    # En que se fue el tiempo. Es UNA pregunta y por eso hay un metodo, no una
    # tabla generica: la pantalla anterior abria en tabla dinamica -un cruce,
    # que es la vista mas abstracta que existe- y ofrecia doce columnas con
    # CINCO medidas de horas distintas. Tenia todo el dato y ninguna respuesta.
    #
    # Lo que devuelve esta ordenado por tiempo y anidado como esta el dato:
    # aplicacion y, dentro, el sitio o el archivo. Nada se agrega que el
    # usuario no pueda volver a abrir en la tabla completa.

    @api.model
    def desglose(self, employee_ids, desde, hasta):
        desde = fields.Date.to_date(desde)
        hasta = fields.Date.to_date(hasta)
        vacio = {'total': {}, 'filas': [], 'categorias': [],
                 'pendientes': [], 'dias': 0}
        if not desde or not hasta or hasta < desde:
            return vacio

        dominio = [('date', '>=', desde), ('date', '<=', hasta)]
        if employee_ids:
            dominio.append(('employee_id', 'in', employee_ids))
        filas = self.search(dominio)
        if not filas:
            return vacio

        def h(x):
            return round(x, 4)

        total = {
            'activo': h(sum(filas.mapped('fg_active'))),
            'sin_input': h(sum(filas.mapped('fg_idle'))),
            'segundo': h(sum(filas.mapped('background'))),
            'llamada': h(sum(filas.mapped('call_hours'))),
            'productivas': h(sum(filas.mapped('productive_hours'))),
            'sistema': h(sum(f.fg_active for f in filas
                             if f.category_id.is_system)),
        }
        # El denominador de los porcentajes es el tiempo ACTIVO sin el ruido
        # del sistema: incluirlo haria que "explorer.exe" se comiera una tajada
        # del grafico sin que nadie hubiera trabajado en el explorador.
        base = max(total['activo'] - total['sistema'], 0.0001)

        # --- por categoria -------------------------------------------------
        cats = {}
        for f in filas:
            if f.category_id.is_system:
                continue
            c = f.category_id
            k = c.id or 0
            d = cats.setdefault(k, {
                'id': c.id or 0,
                'nombre': c.name or 'Sin clasificar',
                'peso': c.weight if c else 0.0,
                'sin_clasificar': not c,
                'horas': 0.0,
            })
            d['horas'] += f.fg_active
        categorias = sorted(cats.values(), key=lambda d: -d['horas'])
        for c in categorias:
            c['horas'] = h(c['horas'])
            c['pct'] = round(100.0 * c['horas'] / base, 1)

        # --- por aplicacion, y dentro por sitio o archivo -------------------
        apps = {}
        for f in filas:
            a = f.app_id
            d = apps.setdefault(a.id, {
                'id': a.id,
                'nombre': a.display_name,
                'exe': a.exe,
                'horas': 0.0,
                'sistema': bool(f.category_id.is_system),
                'categoria': f.category_id.name or 'Sin clasificar',
                'peso': f.category_id.weight if f.category_id else 0.0,
                'sin_clasificar': not f.category_id,
                '_hijos': {},
            })
            d['horas'] += f.fg_active
            # El detalle solo existe donde el dato lo tiene: el sitio para el
            # navegador, el archivo para la paqueteria. Inventar un renglon
            # "(sin detalle)" para las demas seria ruido.
            etiqueta = f.document or f.host
            if etiqueta:
                hijo = d['_hijos'].setdefault(etiqueta, {
                    'nombre': etiqueta,
                    'tipo': 'archivo' if f.document else 'sitio',
                    'site_id': f.site_id.id or 0,
                    'sin_clasificar': bool(f.site_id) and not f.site_id.category_id,
                    'horas': 0.0,
                })
                hijo['horas'] += f.fg_active

        lista = []
        for d in apps.values():
            hijos = sorted(d.pop('_hijos').values(), key=lambda x: -x['horas'])
            for x in hijos:
                x['horas'] = h(x['horas'])
                x['pct'] = round(100.0 * x['horas'] / base, 1)
            d['horas'] = h(d['horas'])
            d['pct'] = round(100.0 * d['horas'] / base, 1)
            d['detalle'] = hijos
            lista.append(d)
        lista.sort(key=lambda d: (d['sistema'], -d['horas']))

        # La cola larga se junta en un renglon. El corte NO es un umbral de
        # opinion: es medio minuto, o sea lo que redondea a "0m" y por tanto no
        # se puede ni mostrar. Dejar quince renglones diciendo 0m y 0.0% es
        # ruido que compite con lo que si se puede leer.
        visibles = [d for d in lista if d['horas'] >= 0.0083]
        cola = [d for d in lista if d['horas'] < 0.0083]
        if len(cola) > 1:
            visibles.append({
                'id': -1, 'nombre': 'y %d más por debajo de un minuto' % len(cola),
                'exe': '', 'horas': h(sum(d['horas'] for d in cola)),
                'pct': 0.0, 'sistema': True, 'categoria': '',
                'peso': 0.0, 'sin_clasificar': False, 'detalle': [], 'cola': True,
            })
        elif cola:
            visibles += cola

        # --- lo que pide una accion ----------------------------------------
        #
        # Sitios Y aplicaciones. Solo los sitios dejaba fuera el caso dominante:
        # en una instalacion nueva lo que esta sin clasificar son las APPS, y la
        # banda no aparecia justo cuando mas falta hacia.
        pend = []
        for d in apps.values():
            if d['sin_clasificar'] and d['horas'] >= 0.0083:
                pend.append({'tipo': 'app', 'id': d['id'],
                             'nombre': d['nombre'], 'horas': d['horas']})
        vistos = {}
        for f in filas:
            if f.site_id and not f.site_id.category_id:
                p = vistos.setdefault(f.site_id.id, {
                    'tipo': 'sitio', 'id': f.site_id.id,
                    'nombre': f.site_id.host, 'horas': 0.0})
                p['horas'] += f.fg_active
        for p in vistos.values():
            p['horas'] = h(p['horas'])
            if p['horas'] >= 0.0083:
                pend.append(p)
        pend.sort(key=lambda d: -d['horas'])

        return {
            'total': total,
            'base': h(base),
            'categorias': categorias,
            'filas': visibles,
            'pendientes': pend[:8],
            'pendientes_horas': h(sum(p['horas'] for p in pend)),
            'pendientes_total': len(pend),
            'dias': len(set(filas.mapped('date'))),
        }

    # ------------------------------------------------------------ arbol
    #
    # Que hizo UNA persona, anidado como esta el dato: la aplicacion, dentro el
    # sitio (navegador) o el archivo (paqueteria), y dentro del sitio la pagina
    # que tenia abierta. Es lo que el tablero despliega debajo de cada renglon
    # de la tabla de empleados.
    #
    # Distinto de `desglose` a proposito: aquel junta sitio y archivo en un solo
    # nivel (`document or host`), asi que en el navegador agrupa por titulo de
    # pagina y pierde el sitio. Aqui el sitio es el segundo nivel y el titulo el
    # tercero, que es como el administrador pregunta: "abrio YouTube; que vio".

    # Medio minuto: lo que redondea a "0m" y por tanto no se puede ni mostrar.
    _MINIMO_VISIBLE_H = 0.0083
    # Tope de renglones por nivel. Un navegador en 30 dias junta cientos de
    # sitios; los que no caben se dicen como "y N mas", nunca se pierden.
    _TOPE_POR_NIVEL = 30

    @api.model
    def arbol_actividad(self, employee_id, desde, hasta):
        desde = fields.Date.to_date(desde)
        hasta = fields.Date.to_date(hasta)
        vacio = {'activo': 0.0, 'sin_input': 0.0, 'dias': 0,
                 'apps': [], 'sistema': [], 'sistema_horas': 0.0, 'cola': None}
        if not employee_id or not desde or not hasta or hasta < desde:
            return vacio
        dominio = [('employee_id', '=', employee_id),
                   ('date', '>=', desde), ('date', '<=', hasta)]

        def h(x):
            return round(x or 0.0, 4)

        def cat(categoria):
            """Lo que el cliente necesita para pintar, sin listas cocidas: el
            nombre, el peso y si es de sistema. El color lo decide el tablero."""
            return {'categoria': categoria.name if categoria else '',
                    'peso': categoria.weight if categoria else 0.0,
                    'sistema': bool(categoria and categoria.is_system)}

        # UNA consulta agregada por (app, sitio, host, pagina). El host va aparte
        # del sitio porque es lo que trae el renglon; el sitio es el catalogo.
        grupos = self._read_group(
            dominio, ['app_id', 'site_id', 'host', 'document'],
            ['fg_active:sum', 'fg_idle:sum'])

        apps = {}
        for app, site, host, document, activo, sin_input in grupos:
            if not app:
                continue
            activo = activo or 0.0
            sin_input = sin_input or 0.0
            a = apps.get(app.id)
            if a is None:
                a = apps[app.id] = dict(cat(app.category_id), **{
                    'id': app.id, 'tipo': 'app', 'nombre': app.display_name,
                    'exe': app.exe, 'sin_clasificar': not app.category_id,
                    'horas': 0.0, 'sin_input': 0.0, '_hijos': {}})
            a['horas'] += activo
            a['sin_input'] += sin_input
            host = (host or '').strip()
            document = (document or '').strip()
            if host:
                s = a['_hijos'].get(('sitio', host))
                if s is None:
                    # El sitio hereda la categoria del navegador mientras nadie
                    # lo clasifique: es la MISMA regla con la que se calcula el
                    # indice (`_compute_category_id`), para que lo que se ve
                    # aqui sea lo que se conto alla.
                    s = a['_hijos'][('sitio', host)] = dict(
                        cat(site.category_id if site and site.category_id
                            else app.category_id), **{
                        'id': site.id if site else 0, 'tipo': 'sitio',
                        'nombre': host,
                        'sin_clasificar': not (site and site.category_id),
                        'horas': 0.0, 'sin_input': 0.0, '_hijos': {}})
                s['horas'] += activo
                s['sin_input'] += sin_input
                if document:
                    p = s['_hijos'].setdefault(document, {
                        'id': 0, 'tipo': 'pagina', 'nombre': document,
                        'horas': 0.0, 'sin_input': 0.0, '_hijos': {}})
                    p['horas'] += activo
                    p['sin_input'] += sin_input
            elif document:
                d = a['_hijos'].setdefault(('archivo', document), {
                    'id': 0, 'tipo': 'archivo', 'nombre': document,
                    'horas': 0.0, 'sin_input': 0.0, '_hijos': {}})
                d['horas'] += activo
                d['sin_input'] += sin_input

        # El denominador es el tiempo activo SIN el sistema, igual que en la
        # tabla de empleados: un porcentaje que incluyera al explorador de
        # Windows diria que la persona "trabajo" en el.
        base = sum(a['horas'] for a in apps.values() if not a['sistema'])
        base = max(base, 0.0001)

        def cerrar(nodo):
            """Convierte el diccionario de hijos en lista ordenada y podada."""
            hijos = sorted(nodo.pop('_hijos').values(), key=lambda x: -x['horas'])
            visibles, cola = [], []
            for i, x in enumerate(hijos):
                if x['horas'] < self._MINIMO_VISIBLE_H or i >= self._TOPE_POR_NIVEL:
                    cola.append(x)
                else:
                    visibles.append(cerrar(x))
            nodo['hijos'] = visibles
            nodo['cola'] = ({'n': len(cola), 'horas': h(sum(x['horas'] for x in cola))}
                            if cola else None)
            nodo['horas'] = h(nodo['horas'])
            nodo['sin_input'] = h(nodo['sin_input'])
            nodo['pct'] = round(100.0 * nodo['horas'] / base, 1)
            return nodo

        lista = sorted(apps.values(), key=lambda a: -a['horas'])
        principales, sistema, cola = [], [], []
        for a in lista:
            if a['sistema']:
                sistema.append(cerrar(a))
            elif a['horas'] < self._MINIMO_VISIBLE_H \
                    or len(principales) >= self._TOPE_POR_NIVEL:
                cola.append(a)
            else:
                principales.append(cerrar(a))

        dias = self._read_group(dominio, ['date:day'], ['__count'])
        return {
            'activo': h(base if apps else 0.0),
            'sin_input': h(sum(a['sin_input'] for a in apps.values())),
            'dias': len(dias),
            'apps': principales,
            'sistema': sistema,
            'sistema_horas': h(sum(a['horas'] for a in sistema)),
            'cola': ({'n': len(cola), 'horas': h(sum(a['horas'] for a in cola))}
                     if cola else None),
        }

    @api.model
    def serie_indice(self, desde, hasta):
        """El indice productivo/activo del equipo por dia, y nada mas: la
        chispa de tendencia de las tarjetas del tablero. `analitica` ya trae
        la serie, pero calcula ademas hechos, veredictos y reparto; para una
        linea de 7 puntos eso es trabajo tirado. Dias sin dato van en None."""
        desde = fields.Date.to_date(desde)
        hasta = fields.Date.to_date(hasta)
        if not desde or not hasta or hasta < desde:
            return []
        por_dia = {}
        for dia, activo, productivo in self._read_group(
                [('date', '>=', desde), ('date', '<=', hasta)],
                ['date:day'], ['active_hours:sum', 'productive_hours:sum']):
            por_dia[dia] = (activo or 0.0, productivo or 0.0)
        # El mismo indice que la cifra grande de la tarjeta que dibuja esta
        # chispa: cubierto entre jornada. Antes la chispa era productivo/activo
        # debajo de un numero que era otra cosa.
        cub_dias = self.env['foco.absence'].cubierto_por_dia(desde, hasta).get('dias') or {}
        salida = []
        d = desde
        while d <= hasta:
            activo, productivo = por_dia.get(d, (0.0, 0.0))
            k = fields.Date.to_string(d)
            salida.append({
                'date': k,
                'activo': round(activo, 3), 'productivo': round(productivo, 3),
                'indice': self._indice(productivo, cub_dias.get(k), activo),
            })
            d += timedelta(days=1)
        return salida

    @api.model
    def sitios_por_clasificar(self, dias=30, limite=15):
        """Sitios sin clasificar ordenados por HORAS, no por novedad.

        Un catalogo que se descubre solo se pudre si nadie lo clasifica, y
        clasificar 400 sitios no lo hace nadie. Esta es la cola que importa:
        casi siempre un punado de sitios explica la mayor parte del tiempo.
        """
        desde = fields.Date.subtract(fields.Date.context_today(self), days=dias)
        grupos = self._read_group(
            [('date', '>=', desde), ('site_id', '!=', False),
             ('site_id.category_id', '=', False), ('fg_active', '>', 0)],
            ['site_id'], ['fg_active:sum'])
        filas = [{'id': site.id, 'host': site.host, 'hours': horas}
                 for site, horas in grupos]
        filas.sort(key=lambda f: f['hours'], reverse=True)
        return filas[:limite]
