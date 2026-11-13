{
    'name': 'FERBA - Almacén',
    'version': '19.0.2.4.0',
    'category': 'Inventory',
    'summary': 'Pantalla simple y didáctica para el almacenista: recibir lo que '
               'llega, entregar lo que sale y buscar piezas, sin entrar al '
               'inventario completo de Odoo.',
    'description': """
Almacén Fácil (FERBA)
=====================
Una sola pantalla, pensada para una persona con conocimientos básicos, que
envuelve el inventario nativo de Odoo (stock.picking) sin su complejidad.

MVP (Fase 1):
- RECIBIR: lista de recepciones pendientes como tarjetas grandes (proveedor,
  proyecto, piezas, días esperando con color por urgencia). Al abrir una, se ven
  los renglones y con un botón se recibe TODO (valida el picking, mueve stock) y
  se guarda la firma del responsable (campo x_studio_firma_de_responsable).
- BUSCAR: por pieza (existencia y unidad) o por orden / recepción.

Responsivo: sirve en teléfono/tablet y en PC. Rol propio "Almacenista" que solo
ve este módulo. Las operaciones de inventario corren con sudo() y se registra en
el chatter quién recibió (trazabilidad) además de la firma.
""",
    'author': 'FERBA',
    'license': 'LGPL-3',
    'depends': ['stock', 'web'],
    'data': [
        'security/almacen_facil_security.xml',
        'views/almacen_facil_menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ferba_almacen_facil/static/src/almacen/almacen_facil.js',
            'ferba_almacen_facil/static/src/almacen/almacen_facil.xml',
            'ferba_almacen_facil/static/src/almacen/almacen_facil.scss',
        ],
    },
    'installable': True,
    'application': True,
}
