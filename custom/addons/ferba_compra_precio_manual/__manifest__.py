{
    'name': 'FERBA - Precio de compra solo manual',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Purchase',
    'summary': 'Las líneas de compra no traen precio automático (costo ni lista del proveedor): lo captura el comprador',
    'description': """
Pedido de Compras (Isaura, 27-oct-2026): que la orden de compra no ponga sola el precio.

Odoo pone en cada línea nueva, sin preguntar:

* el precio de la lista del proveedor, si el proveedor tiene uno registrado para el producto
  (se guarda en la primera compra con ese proveedor y no se actualiza), o
* el costo promedio del producto, si no tiene (siempre con "POR DEFINIR", que es como
  "Generar Orden" crea las órdenes desde las requisiciones).

Ninguno es el precio de la última compra y en general no es el precio que se va a pagar.

Con la opción "Precio de compra solo manual" activa en la empresa (Compras > Configuración > Ajustes):

* Las líneas nuevas entran con precio 0 y sin descuento automático, vengan de una requisición,
  se capturen a mano o se generen por reabastecimiento.
* Al cambiar de proveedor o de cantidad, Odoo ya no pone precio. El que capture el comprador no se toca
  (Odoo 19 ya respeta los precios tecleados).
* Si reabastecimiento suma cantidad a una línea existente, conserva el precio que ya tenía.
* Un precio que viene explícito (duplicar una orden, importar) se respeta.

Solo actúa en las empresas con la opción activa; al instalar se activa en la empresa principal (Industrias).
""",
    'author': 'FERBA',
    'depends': ['purchase_stock'],
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'license': 'LGPL-3',
}
