from odoo import api, fields, models


class FocoUsage(models.Model):
    _name = 'foco.usage'
    _description = 'Uso diario por aplicacion'
    _order = 'date desc, fg_active desc'

    _uniq_day = models.Constraint(
        'unique(computer_id, app_id, date, host)',
        'Ya existe un renglon de uso para ese equipo/app/dia/sitio.')

    computer_id = fields.Many2one(
        'foco.computer', string='Equipo', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        related='computer_id.employee_id', store=True, string='Empleado')
    department_id = fields.Many2one(
        related='computer_id.department_id', store=True, string='Departamento')
    app_id = fields.Many2one(
        'foco.app', string='Aplicacion', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True, index=True)
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
        [('site', 'Sitio'), ('app', 'Aplicacion'), ('none', 'Sin clasificar')],
        string='Origen de la categoria', store=True, compute='_compute_category_id',
        help='De donde salio el peso de este renglon. Que el numero pueda '
             'explicarse es parte del numero.')

    fg_active = fields.Float(string='1er plano activo (h)',
                             help='Horas de uso real: con foco y con teclado/mouse.')
    fg_idle = fields.Float(
        string='1er plano sin input (h)',
        help='La app tenia el foco pero no hubo teclado ni mouse en 60 s. NO es '
             'automaticamente ociosidad: leer, revisar o pensar frente a la '
             'pantalla cae aqui. Se guarda aparte y NO cuenta como productivo; '
             'se muestra para que una persona lo interprete.')
    background = fields.Float(string='2do plano (h)',
                              help='Abierta pero sin foco. No es trabajo.')
    injected_hours = fields.Float(
        string='Con input sintetico (h)',
        help='Horas "activas" acompanadas de input generado por software '
             '(jiggler). Es un HECHO verificable: Windows marca el input '
             'inyectado. No se descuenta del total: se deja a la vista con su '
             'evidencia para que una persona lo juzgue.')
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

    @api.depends('site_id', 'site_id.category_id', 'app_id', 'app_id.category_id')
    def _compute_category_id(self):
        for rec in self:
            cat = rec.site_id.category_id
            if cat:
                rec.category_id = cat
                rec.category_source = 'site'
            elif rec.app_id.category_id:
                rec.category_id = rec.app_id.category_id
                rec.category_source = 'app'
            else:
                rec.category_id = False
                rec.category_source = 'none'

    @api.depends('fg_active', 'category_id',
                 'category_id.weight', 'category_id.is_system')
    def _compute_metrics(self):
        for rec in self:
            cat = rec.category_id
            if cat and cat.is_system:
                rec.active_hours = 0.0
                rec.productive_hours = 0.0
            else:
                rec.active_hours = rec.fg_active
                rec.productive_hours = rec.fg_active * (cat.weight if cat else 0.0)

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
