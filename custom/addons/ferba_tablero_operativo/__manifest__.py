{
    'name': 'FERBA - Tablero Operativo',
    'version': '19.0.3.1.0',
    'category': 'Productivity',
    'summary': 'Tablero del flujo FERBA (ventas, proyectos, compras, almacen y pagos) '
               'para todos los usuarios.',
    'description': """
Tablero Operativo FERBA
=======================
Un tablero a la medida que junta en una sola pantalla el status del flujo que
FERBA maneja hoy (desarrollado con Studio): ventas y proyectos, el embudo de
compras (solicitud -> requisicion -> RFQ -> orden de compra), almacen
(recepciones y traslados) y pagos (gestion de pagos y solicitudes).

- Visible para TODOS los usuarios internos.
- Lee en vivo los modelos del flujo y agrega por empresa activa.
- KPIs con monto, embudos por etapa y pendientes que piden atencion.
""",
    'author': 'FERBA',
    'license': 'LGPL-3',
    'depends': ['base', 'web', 'sale'],
    'data': [
        'views/ferba_operativo_menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Chart.js vendorizado (sin CDN: la cuenta corre en red cerrada)
            'ferba_tablero_operativo/static/lib/chartjs/chart.umd.min.js',
            'ferba_tablero_operativo/static/src/operativo/ferba_operativo.js',
            'ferba_tablero_operativo/static/src/operativo/ferba_operativo.xml',
            'ferba_tablero_operativo/static/src/operativo/ferba_operativo.scss',
        ],
    },
    'installable': True,
    'application': True,
}
