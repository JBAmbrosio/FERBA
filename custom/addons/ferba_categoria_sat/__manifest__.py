{
    'name': 'FERBA - Clave SAT por categoria de producto',
    'version': '19.0.1.0.0',
    'summary': 'La categoria del producto lleva su clave SAT y el producto la toma de ella.',
    'description': """
Clave SAT por categoria
=======================
La contabilidad de FERBA se lleva por categoria de producto: la categoria
decide las cuentas y, con este modulo, tambien la clave de producto/servicio
del SAT (catalogo UNSPSC) que viaja en el CFDI.

- La categoria tiene el campo "Clave SAT", guardado POR EMPRESA (igual que
  sus cuentas). Asi cada empresa lo configura cuando le toque, sin afectar a
  las demas.
- Al elegir o cambiar la categoria de un producto, su clave SAT se llena con
  la de la categoria. Al crear un producto sin clave, la toma de su categoria.
- Si la categoria no tiene clave en la empresa activa, no se toca nada.
""",
    'author': 'FERBA',
    'category': 'Accounting/Localizations',
    'license': 'LGPL-3',
    'depends': ['product', 'product_unspsc'],
    'data': [
        'views/product_category_views.xml',
    ],
    'installable': True,
    'application': False,
}
