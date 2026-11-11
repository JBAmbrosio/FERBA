# -*- coding: utf-8 -*-
import json
import logging

_logger = logging.getLogger(__name__)

# Datos extraidos del despiece real "01_TBM. AINOX" de la cotizacion URSOMEX P2026-172
# y verificados al centavo contra precios.xlsx. NO editar a mano: se genera.
DATA = json.loads(r'''{"machine": {"code": "PILOTO-TBM40X8", "name": "TRANSPORTADOR DE BANDA MODULAR 40\" x 8ft (PILOTO)"}, "bom_code": "LM_PILOTO_TBM40X8", "components": [{"code": "PILOTO-M01", "name": "Ptr 1-1/2\" x 1-1/2\" Cal 14", "cost": 798.305, "qty": 2.2770833333333336, "kind": "material"}, {"code": "PILOTO-M02", "name": "Lám. Cal 12", "cost": 5120.69, "qty": 0.47812499999999997, "kind": "material"}, {"code": "PILOTO-M03", "name": "Ptr 1-1/4\" x 1-1/4\" Cal 14 INOX.", "cost": 637.9313, "qty": 0.16666666666666666, "kind": "material"}, {"code": "PILOTO-M04", "name": "Solera 3/16\" x 1-1/2\"", "cost": 267.24, "qty": 0.15, "kind": "material"}, {"code": "PILOTO-M05", "name": "Solera 3/16\" x 1-1/2\" INOX.", "cost": 267.24, "qty": 0.1, "kind": "material"}, {"code": "PILOTO-M06", "name": "Angulo 3/16\" x 1-1/2\"", "cost": 517.24, "qty": 1.5437500000000002, "kind": "material"}, {"code": "PILOTO-M07", "name": "Solera 3/16 x 1-1/2\"", "cost": 267.24, "qty": 0.75, "kind": "material"}, {"code": "PILOTO-M08", "name": "Solera de desgaste UHMW 1/4\" x 1-1/2\" 3mts", "cost": 225.75, "qty": 6.4, "kind": "material"}, {"code": "PILOTO-M09", "name": "SPROCKET serie 1200 de 14 dientes", "cost": 375.0, "qty": 16.0, "kind": "material"}, {"code": "PILOTO-M10", "name": "Cuña cuadrada de 1-1/2\"", "cost": 4219.0, "qty": 0.42083333333333334, "kind": "material"}, {"code": "PILOTO-M11", "name": "Chumacera base 1-1/4\"", "cost": 273.0, "qty": 4.0, "kind": "material"}, {"code": "PILOTO-M12", "name": "Tubo pvc 1-1/2\" ced 40", "cost": 168.08, "qty": 0.6666666666666666, "kind": "material"}, {"code": "PILOTO-M13", "name": "Taquete p/tubo ced 40", "cost": 4.0, "qty": 1.0, "kind": "material"}, {"code": "PILOTO-M14", "name": "tornillo 5/16\" x 2\"L Inox.", "cost": 1.5384, "qty": 8.0, "kind": "material"}, {"code": "PILOTO-M15", "name": "tuerca 5/16\" inox.", "cost": 0.6769, "qty": 16.0, "kind": "material"}, {"code": "PILOTO-M16", "name": "arandela plana 5/16\" inox.", "cost": 0.3538, "qty": 24.0, "kind": "material"}, {"code": "PILOTO-M17", "name": "arandela presion 5/16\" inox.", "cost": 0.448, "qty": 8.0, "kind": "material"}, {"code": "PILOTO-M18", "name": "Solera 3/16 x 2\"", "cost": 357.76, "qty": 0.29375, "kind": "material"}, {"code": "PILOTO-M19", "name": "Varilla roscada 1/2\"", "cost": 99.9771, "qty": 0.8888888888888888, "kind": "material"}, {"code": "PILOTO-O01", "name": "Banda PVC 120 modular 40in x 8ft", "cost": 25033.54, "qty": 1.0, "kind": "otro"}, {"code": "PILOTO-O02", "name": "Motor HP 40:1 tamano 63", "cost": 6300.0, "qty": 1.0, "kind": "otro"}, {"code": "PILOTO-O03", "name": "Lote de refacciones", "cost": 450.0, "qty": 1.0, "kind": "otro"}, {"code": "PILOTO-O04", "name": "Procesos de taller (corte/doblez/barreno/pintura/torno/calibracion)", "cost": 5941.8, "qty": 1.0, "kind": "otro"}, {"code": "PILOTO-O05", "name": "Mano de obra (2 pers x 3 dias x 8h x 200)", "cost": 9600.0, "qty": 1.0, "kind": "otro"}], "total_esperado": 63421.05, "venta_esperada": 95131.58}''')


def _upsert_producto(env, code, name, cost, es_maquina=False):
    P = env["product.product"]
    prod = P.search([("default_code", "=", code)], limit=1)
    vals = {
        "name": name,
        "default_code": code,
        "type": "consu",
        "standard_price": cost,
        "purchase_ok": not es_maquina,
        "sale_ok": bool(es_maquina),
    }
    if prod:
        prod.write(vals)
    else:
        prod = P.create(vals)
    return prod


def post_init_hook(env):
    comps = []
    for c in DATA["components"]:
        prod = _upsert_producto(env, c["code"], c["name"], c["cost"])
        comps.append((prod, c["qty"]))

    machine = _upsert_producto(env, DATA["machine"]["code"], DATA["machine"]["name"], 0.0, es_maquina=True)

    Bom = env["mrp.bom"]
    bom = Bom.search([("code", "=", DATA["bom_code"])], limit=1)
    if not bom:
        bom = Bom.create({
            "product_tmpl_id": machine.product_tmpl_id.id,
            "product_id": machine.id,
            "product_qty": 1.0,
            "type": "normal",
            "code": DATA["bom_code"],
        })
    bom.bom_line_ids.unlink()
    for prod, qty in comps:
        env["mrp.bom.line"].create({
            "bom_id": bom.id,
            "product_id": prod.id,
            "product_qty": qty,
            "product_uom_id": prod.uom_id.id,
        })

    total = sum(p.standard_price * q for p, q in comps)
    machine.write({"standard_price": total})
    _logger.info(
        "FERBA COSTEO PILOTO: BoM %s -> costo componentes = %.2f (esperado %s, venta x1.5 = %s)",
        DATA["bom_code"], total, DATA["total_esperado"], DATA["venta_esperada"],
    )
    # intento opcional de usar el calculo nativo de Odoo (si existe en esta version)
    try:
        machine.product_tmpl_id.button_bom_cost()
    except Exception as e:
        _logger.info("FERBA COSTEO PILOTO: calculo nativo de precio no invocado (%s)", e)


def uninstall_hook(env):
    # reversible: borra el piloto (BoM + productos [PILOTO-...])
    bom = env["mrp.bom"].search([("code", "=", DATA["bom_code"])])
    if bom:
        bom.bom_line_ids.unlink()
        bom.unlink()
    prods = env["product.product"].search([("default_code", "like", "PILOTO-")])
    try:
        prods.unlink()
    except Exception as e:
        _logger.info("FERBA COSTEO PILOTO: no se pudieron borrar algunos productos piloto (%s)", e)
