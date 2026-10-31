"""Tipos de ubicacion como los pidio el Ing (30-oct-2026): entrada / oficina /
empaque / campo / otra, con icono por chip en la app.

El tipo 'almacen' anterior pasa a 'empaque' (es el termino del campo agricola;
caso Agricola Uromex / Cori). Se corre en PRE-migracion para dejar los datos en
los nuevos valores antes de que cargue la nueva seleccion de foco_tipo_ubicacion.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE res_partner
           SET foco_tipo_ubicacion = 'empaque'
         WHERE foco_tipo_ubicacion = 'almacen'
    """)
