# -*- coding: utf-8 -*-
{
    'name': 'FERBA - Aprobacion de cotizaciones',
    'version': '19.0.1.1.0',
    'summary': 'El vendedor manda la cotizacion a revision; un aprobador la aprueba o rechaza; '
               'hasta entonces no se puede enviar al cliente ni confirmar.',
    'description': """
Flujo de aprobacion de cotizaciones
===================================
* Al vendedor le desaparece «Enviar»/«Confirmar» y solo ve «Enviar a revision».
* Los aprobadores (configurables en Ventas > Configuracion > Ajustes) reciben aviso en el
  chatter, en su bandeja y una actividad; ven el menu «Por aprobar» y aprueban o rechazan
  (el rechazo pide motivo).
* Al aprobar o rechazar se avisa al vendedor. Aprobada, ya puede enviar y confirmar.
* Si la cotizacion cambia despues de aprobarse (lineas, cliente, lista de precios, plazo,
  moneda, posicion fiscal o vigencia) vuelve a «Sin revisar».
* Las cotizaciones que ya estaban enviadas o confirmadas antes de instalar quedan aprobadas.
    """,
    'author': 'FERBA',
    'category': 'Sales',
    'depends': ['sale_management', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'wizard/rechazo_wizard_views.xml',
        'views/sale_order_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
