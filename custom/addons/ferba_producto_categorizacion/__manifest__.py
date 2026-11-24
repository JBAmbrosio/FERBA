{
    'name': 'FERBA - Categorizacion de productos',
    'version': '19.0.1.0.0',
    'summary': 'Un producto nuevo queda oculto hasta que un categorizador le asigna su categoria contable '
               '(cuentas y clave SAT). Avisos por chat y actividad, como la aprobacion de cotizaciones.',
    'description': """
Categorizacion obligatoria de productos
=======================================
Pedido de la contadora (reuniones del 19 y 20-nov-2026): la categoria del producto decide como se
contabiliza (ingreso, costo/gasto, almacen) y su clave SAT, asi que no debe existir un producto sin
categoria, y solo ciertas personas la asignan.

Con la opcion encendida en la empresa (Contabilidad > Configuracion > Ajustes > Categorizacion de productos):

* Quien da de alta un producto y NO es categorizador lo crea "Por categorizar": queda archivado (no
  aparece en ventas, compras, inventario ni facturas) y sin categoria. Puede proponer una
  "Categoria sugerida".
* A los categorizadores con acceso a esa empresa les llega un mensaje directo por chat y una actividad
  "Categorizar producto". Altas masivas (mas de 5 a la vez): un solo chat con la liga a la lista.
* El categorizador asigna la categoria (boton "Categorizar", o varios a la vez desde la lista). El
  producto se activa, toma la clave SAT de la categoria en esa empresa y a quien lo dio de alta le llega
  un chat. Tambien puede rechazar el alta (duplicado, mal capturado) con motivo.
* Solo los categorizadores cambian la categoria, y solo a una categoria contable (hija de la raiz
  configurada, "CATEGORIAS CONTABLES"). El servidor lo valida aunque la pantalla no lo muestre.
* Guardas de servidor: un producto que no esta categorizado no entra en lineas de venta, compra,
  factura ni movimientos de inventario.
* Lista "Por categorizar" en Inventario, Ventas, Compras y Contabilidad. Filtro "Sin categoria
  contable" para limpiar el catalogo viejo.

Los categorizadores son el grupo "Contabilidad: Gestor de categorias de producto" (modulo
ferba_categoria_sat), editable desde los Ajustes. Los productos que ya existian no cambian. Sin la
opcion encendida, la empresa sigue como siempre.
""",
    'author': 'FERBA',
    'category': 'Inventory/Inventory',
    'license': 'LGPL-3',
    'depends': ['ferba_categoria_sat', 'mail', 'account', 'sale', 'purchase', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/categorizar_wizard_views.xml',
        'views/product_template_views.xml',
        'views/product_category_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': False,
}
