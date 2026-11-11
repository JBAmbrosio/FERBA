# -*- coding: utf-8 -*-
{
    "name": "FERBA Costeo - Piloto Transportador de Banda",
    "version": "19.0.1.1.0",
    "summary": "Prueba de concepto de costeo: TBM 40x8 costeado desde precios.xlsx, replica el flujo BoM/MO de la cadena existente (sube la precision 'Product Unit' a 6 para costear por fraccion de tramo)",
    "description": """
Piloto AUTOCONTENIDO y REVERSIBLE para validar el costeo de maquinaria en Odoo.

Crea (al instalar, via post_init_hook) un producto maquina 'TRANSPORTADOR DE BANDA
MODULAR 40x8' con su lista de materiales (mrp.bom estilo 'LM_...', igual que la que
genera la automatizacion de compras), con los componentes costeados con precios REALES
verificados contra precios.xlsx (cotizacion URSOMEX P2026-172).

Objetivo: demostrar que, con el costo del material cargado, la BoM/MO calcula el costo
real (63,421.05) en lugar de 0, cuadrando al centavo con el Excel.

Todo lleva codigo [PILOTO-...]; al desinstalar se borra (uninstall_hook). No toca datos
reales ni los modelos de Studio.
""",
    "author": "Desarrollo Odoo",
    "category": "Manufacturing",
    "license": "LGPL-3",
    "depends": ["mrp", "product", "stock"],
    "data": [],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}
