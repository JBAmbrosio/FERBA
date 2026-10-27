"""Pruebas de "Precio de compra solo manual" con el flujo real de compras de Odoo 19.

Reproduce lo medido en FERBA con el producto 24621 (Loctite): costo promedio 42.82, precio de lista de
TOMACO 139.15; "Generar Orden" crea las líneas solo con producto y cantidad, con proveedor POR DEFINIR.
"""
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFerbaPrecioManual(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.ferba_compra_precio_manual = True
        Partner = cls.env['res.partner']
        cls.por_definir = Partner.create({'name': 'POR DEFINIR (prueba)', 'supplier_rank': 1})
        cls.sin_lista = Partner.create({'name': 'Proveedor sin lista (prueba)', 'supplier_rank': 1})
        cls.con_lista = Partner.create({'name': 'TOMACO (prueba)', 'supplier_rank': 1})
        cls.product = cls.env['product.product'].create({
            'name': 'PEGAMENTO PARA ROSCAS (prueba)',
            'type': 'consu',
            'purchase_ok': True,
            'standard_price': 42.82,
        })
        cls.seller = cls.env['product.supplierinfo'].create({
            'partner_id': cls.con_lista.id,
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'price': 139.15,
            'discount': 5.0,
        })

    def _po(self, partner, lines):
        return self.env['purchase.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, vals) for vals in lines],
        })

    # --- con la opción activa ---------------------------------------------------------------------------

    def test_generar_orden_sin_precio(self):
        """Como "Generar Orden": solo producto y cantidad -> 0, no el costo (42.82)."""
        po = self._po(self.por_definir, [{'product_id': self.product.id, 'product_qty': 6}])
        self.assertEqual(po.order_line.price_unit, 0.0)
        self.assertEqual(po.order_line.discount, 0.0)

    def test_proveedor_con_lista_no_pone_precio(self):
        po = self._po(self.con_lista, [{'product_id': self.product.id, 'product_qty': 6}])
        self.assertEqual(po.order_line.price_unit, 0.0)
        self.assertEqual(po.order_line.discount, 0.0, "el descuento de la lista tampoco se aplica solo")

    def test_cambio_de_proveedor_no_pone_precio(self):
        po = self._po(self.por_definir, [{'product_id': self.product.id, 'product_qty': 6}])
        po.partner_id = self.sin_lista
        self.assertEqual(po.order_line.price_unit, 0.0, "sin la opción, aquí ponía el costo")
        po.partner_id = self.con_lista
        self.assertEqual(po.order_line.price_unit, 0.0, "sin la opción, aquí ponía 139.15")
        po.order_line.product_qty = 10
        self.assertEqual(po.order_line.price_unit, 0.0)

    def test_precio_tecleado_en_pantalla_se_respeta(self):
        """Flujo de pantalla: el comprador teclea el precio y luego cambia proveedor y cantidad."""
        po_form = Form(self.env['purchase.order'])
        po_form.partner_id = self.por_definir
        with po_form.order_line.new() as line:
            line.product_id = self.product
            line.product_qty = 6
            self.assertEqual(line.price_unit, 0.0)
            line.price_unit = 150.0
        po = po_form.save()
        self.assertEqual(po.order_line.price_unit, 150.0)
        with Form(po) as f:
            f.partner_id = self.con_lista
        self.assertEqual(po.order_line.price_unit, 150.0)
        with Form(po) as f:
            with f.order_line.edit(0) as line:
                line.product_qty = 10
        self.assertEqual(po.order_line.price_unit, 150.0)

    def test_linea_nueva_en_pantalla(self):
        po_form = Form(self.env['purchase.order'])
        po_form.partner_id = self.con_lista
        with po_form.order_line.new() as line:
            line.product_id = self.product
            self.assertEqual(line.price_unit, 0.0)
        self.assertEqual(po_form.save().order_line.price_unit, 0.0)

    def test_precio_explicito_y_duplicar(self):
        po = self._po(self.por_definir, [{'product_id': self.product.id, 'product_qty': 6, 'price_unit': 99.0}])
        self.assertEqual(po.order_line.price_unit, 99.0)
        self.assertEqual(po.copy().order_line.price_unit, 99.0)

    def test_catalogo(self):
        po = self._po(self.con_lista, [])
        po._update_order_line_info(self.product.id, 3)
        self.assertEqual(po.order_line.price_unit, 0.0)

    def test_reabastecimiento_linea_nueva(self):
        po = self._po(self.con_lista, [])
        vals = self.env['purchase.order.line']._prepare_purchase_order_line(
            self.product, 5, self.product.uom_id, self.company, self.con_lista, po)
        self.assertEqual(vals['price_unit'], 0.0)
        self.assertEqual(vals['discount'], 0.0)

    def test_reabastecimiento_suma_a_linea_existente(self):
        po = self._po(self.con_lista, [{'product_id': self.product.id, 'product_qty': 2}])
        po.order_line.price_unit = 150.0
        res = self.env['stock.rule']._update_purchase_order_line(
            self.product, 3, self.product.uom_id, self.company, {'supplier': self.seller, 'move_dest_ids': []}, po.order_line)
        self.assertEqual(res['price_unit'], 150.0)

    # --- sin la opción: Odoo estándar sin cambios ------------------------------------------------------

    def test_sin_opcion_comportamiento_estandar(self):
        self.company.ferba_compra_precio_manual = False
        po = self._po(self.con_lista, [{'product_id': self.product.id, 'product_qty': 6}])
        self.assertAlmostEqual(po.order_line.price_unit, 139.15, places=2)
        self.assertEqual(po.order_line.discount, 5.0)
        po2 = self._po(self.sin_lista, [{'product_id': self.product.id, 'product_qty': 6}])
        self.assertAlmostEqual(po2.order_line.price_unit, 42.82, places=2)
        vals = self.env['purchase.order.line']._prepare_purchase_order_line(
            self.product, 5, self.product.uom_id, self.company, self.con_lista, po)
        self.assertAlmostEqual(vals['price_unit'], 139.15, places=2)
