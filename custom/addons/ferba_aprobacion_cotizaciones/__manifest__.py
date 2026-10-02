# -*- coding: utf-8 -*-
{
    'name': 'FERBA - Aprobacion de cotizaciones',
    'version': '19.0.1.4.0',
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

19.0.1.4.0
----------
* Mientras la cotizacion no este aprobada, del encabezado solo se ven «Enviar a revision» y
  «Cancelar» (mas «Aprobar» y «Rechazar» para los aprobadores). Enviar, Confirmar, Vista
  previa, Imprimir, PROFORMA y los botones de Studio (Crear almacen, Lanzar fabricacion,
  Crear CC) aparecen al aprobarse. La regla corre sobre la vista ya combinada
  (sale.order._get_view), asi cubre tambien botones que se agreguen despues.
* El engrane (menu de acciones) de una cotizacion sin aprobar solo ofrece «Duplicar»:
  imprimir, eliminar, compartir, enlace de pago, enviar correo, marcar como enviada y
  solicitar firma aparecen al aprobarse.

19.0.1.3.0
----------
* «Imprimir» tambien se oculta hasta que la cotizacion este aprobada (igual que «Enviar»):
  no se manda al cliente ni un PDF de una cotizacion que la direccion no libero.

19.0.1.2.0
----------
* Un aprobador que pulsa «Enviar» o «Confirmar» aprueba en el acto (queda quien y cuando);
  ya no tiene que mandarse la cotizacion a revision a si mismo. «Aprobar» y «Rechazar» se
  ven desde «Sin revisar».
* El aviso al vendedor dice quien aprueba, en que estado esta cada cotizacion y que hacer
  si no ve el boton «Enviar a revision» (formulario cargado antes de un despliegue).
* Si la aprobacion se retira por un cambio, la cotizacion lo dice arriba (que cambio,
  quien y cuando), no solo en el chatter.
* Un cliente que intenta aceptar desde el portal una cotizacion no liberada recibe un
  mensaje claro, sin jerga interna.
* Solo se avisa y se asigna actividad a aprobadores que pueden entrar a la empresa de la
  cotizacion (a los demas la actividad ni se les puede crear).
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
    'assets': {
        'web.assets_backend': [
            'ferba_aprobacion_cotizaciones/static/src/js/engrane_aprobacion.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
