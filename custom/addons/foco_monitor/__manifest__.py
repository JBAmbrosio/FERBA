{
    'name': 'Foco - Monitor de Productividad',
    'version': '19.0.11.16.0',
    'summary': 'Mide el uso real de aplicaciones (primer plano vs segundo plano) '
               'por empleado, con control remoto de equipos.',
    'description': """
Foco - Monitor de Productividad
===============================
Reemplazo in-house de SoftActivity. Un agente ligero en cada equipo Windows
descubre EN VIVO que aplicacion esta en primer plano y cuales en segundo,
mide el tiempo activo (con input) vs inactivo, y lo envia a Odoo.

- El catalogo de aplicaciones Y DE SITIOS se DESCUBRE solo (no hay listas cocidas).
- El admin clasifica cada app y cada sitio (productiva / distraccion / ...) y
  ese peso alimenta el indice de productividad. Si el sitio esta clasificado,
  MANDA sobre la app: asi el ERP en el navegador deja de valer lo mismo que
  YouTube solo porque los dos se abren en Chrome.
- Control remoto: bloquear equipo, mostrar mensaje, cerrar una app, cerrar sesion.
- Cada equipo se liga a un empleado (hr.employee).
""",
    'author': 'FERBA',
    'category': 'Human Resources/Productivity',
    'license': 'LGPL-3',
    'depends': ['base', 'hr', 'mail'],
    'data': [
        'security/foco_security.xml',
        'security/ir.model.access.csv',
        'security/foco_record_rules.xml',
        'data/foco_category_data.xml',
        'data/foco_settings_data.xml',
        'data/foco_retention_data.xml',
        'data/foco_mail_data.xml',
        'data/foco_invite_action.xml',
        'views/foco_category_views.xml',
        'views/foco_app_views.xml',
        'views/foco_site_views.xml',
        'views/foco_policy_views.xml',
        'views/foco_computer_views.xml',
        'views/foco_usage_views.xml',
        'views/foco_uso_views.xml',
        'views/foco_event_views.xml',
        'views/foco_workday_views.xml',
        'views/foco_command_views.xml',
        'views/foco_capture_views.xml',
        'views/foco_watch_views.xml',
        'views/foco_mobile_views.xml',
        'views/foco_mobile_app_views.xml',
        'views/foco_movil_action.xml',
        'views/foco_settings_views.xml',
        'views/res_config_settings_views.xml',
        'views/res_users_views.xml',
        'views/foco_invitation_views.xml',
        'views/foco_absence_views.xml',
        'views/foco_justify_templates.xml',
        'views/foco_distribucion_templates.xml',
        'views/foco_dashboard_action.xml',
        'views/foco_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'foco_monitor/static/src/dashboard/foco_dashboard.js',
            'foco_monitor/static/src/dashboard/foco_dashboard.xml',
            'foco_monitor/static/src/dashboard/foco_dashboard.scss',
            'foco_monitor/static/src/jornada/foco_jornada.js',
            'foco_monitor/static/src/jornada/foco_jornada.xml',
            'foco_monitor/static/src/jornada/foco_jornada.scss',
            'foco_monitor/static/src/uso/foco_uso.js',
            'foco_monitor/static/src/uso/foco_uso.xml',
            'foco_monitor/static/src/uso/foco_uso.scss',
            'foco_monitor/static/src/settings/foco_settings.js',
            'foco_monitor/static/src/settings/foco_settings.xml',
            'foco_monitor/static/src/settings/foco_settings.scss',
            'foco_monitor/static/src/movil/foco_movil.js',
            'foco_monitor/static/src/movil/foco_movil.xml',
            'foco_monitor/static/src/movil/foco_movil.scss',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
}
