"""Categorizacion obligatoria de productos: alta oculta, aviso, categorizacion, permisos y guardas."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.ferba_producto_categorizacion.hooks import PARAM_RAIZ

RESUMEN = 'Categorizar producto'


@tagged('post_install', '-at_install')
class TestCategorizacion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.ferba_categorizacion_obligatoria = True
        Cat = cls.env['product.category']
        cls.raiz = Cat.create({'name': 'RAIZ CONTABLE (prueba)'})
        cls.cat = Cat.create({'name': 'HOJA A (prueba)', 'parent_id': cls.raiz.id})
        cls.cat2 = Cat.create({'name': 'HOJA B (prueba)', 'parent_id': cls.raiz.id})
        cls.fuera = Cat.create({'name': 'FUERA DE LA RAIZ (prueba)'})
        cls.env['ir.config_parameter'].sudo().set_param(PARAM_RAIZ, str(cls.raiz.id))
        cls.clave = cls.env['product.unspsc.code'].search([('applies_to', '=', 'product')], limit=1)
        cls.cat.with_company(cls.company).ferba_unspsc_code_id = cls.clave
        empresas = [(6, 0, [cls.company.id])]
        cls.alta = new_test_user(
            cls.env, login='ferba_alta_prueba', company_id=cls.company.id, company_ids=empresas,
            groups='base.group_user,product.group_product_manager,sales_team.group_sale_salesman')
        cls.gestor = new_test_user(
            cls.env, login='ferba_gestor_prueba', company_id=cls.company.id, company_ids=empresas,
            groups='base.group_user,product.group_product_manager,ferba_categoria_sat.group_gestor_categorias')

    def _alta(self, **vals):
        vals.setdefault('name', 'PRODUCTO (prueba categorizacion)')
        return self.env['product.template'].with_user(self.alta).create(vals)

    def test_alta_queda_oculta_y_avisa(self):
        p = self._alta(categ_id=self.cat.id)
        self.assertEqual(p.ferba_categorizacion, 'pendiente')
        self.assertFalse(p.active)
        self.assertFalse(p.categ_id, 'quien no categoriza no deja categoria')
        self.assertEqual(p.ferba_categ_sugerida_id, self.cat, 'lo que traia queda como sugerencia')
        variantes = p.with_context(active_test=False).product_variant_ids
        self.assertTrue(variantes)
        self.assertFalse(variantes.filtered('active'))
        self.assertFalse(self.env['product.template'].search([('id', '=', p.id)]))
        self.assertTrue(p.activity_ids.filtered(
            lambda a: a.user_id == self.gestor and a.summary.startswith(RESUMEN)))

    def test_categorizar_activa_y_pone_clave(self):
        p = self._alta()
        p.with_user(self.gestor)._ferba_categorizar(self.cat)
        self.assertEqual(p.ferba_categorizacion, 'categorizado')
        self.assertTrue(p.active)
        self.assertEqual(p.categ_id, self.cat)
        self.assertEqual(p.unspsc_code_id, self.clave)
        self.assertEqual(p.ferba_categorizado_por_id, self.gestor)
        self.assertTrue(p.product_variant_ids.filtered('active'))
        self.assertTrue(self.env['product.product'].with_user(self.alta).search([('product_tmpl_id', '=', p.id)]),
                        'la variante tambien queda activa y se encuentra')
        self.assertFalse(p.activity_ids.filtered(lambda a: a.summary and a.summary.startswith(RESUMEN)))

    def test_asistente_varios_con_sugerencia(self):
        p1 = self._alta(name='P1 (prueba)', categ_id=self.cat.id)
        p2 = self._alta(name='P2 (prueba)', categ_id=self.cat.id)
        Wizard = self.env['ferba.producto.categorizar'].with_user(self.gestor).with_context(
            active_model='product.template', active_ids=(p1 | p2).ids, active_test=False)
        wiz = Wizard.create({})
        self.assertEqual(wiz.categ_id, self.cat, 'propone la sugerencia comun')
        wiz.action_aplicar()
        self.assertEqual((p1 | p2).mapped('ferba_categorizacion'), ['categorizado', 'categorizado'])
        self.assertTrue(all((p1 | p2).mapped('active')))

    def test_solo_categorizador_cambia_categoria(self):
        p = self._alta()
        p.with_user(self.gestor)._ferba_categorizar(self.cat)
        with self.assertRaises(AccessError):
            p.with_user(self.alta).write({'categ_id': self.cat2.id})
        # El formulario manda la misma categoria en cada guardado: eso no es un cambio.
        p.with_user(self.alta).write({'categ_id': self.cat.id, 'description': 'nota (prueba)'})
        with self.assertRaises(AccessError):
            self._alta(name='OTRO (prueba)')._ferba_categorizar(self.cat)
        p.with_user(self.gestor).write({'categ_id': self.cat2.id})
        self.assertEqual(p.categ_id, self.cat2)

    def test_categoria_fuera_de_la_raiz(self):
        p = self._alta()
        with self.assertRaises(UserError):
            p.with_user(self.gestor)._ferba_categorizar(self.fuera)
        with self.assertRaises(UserError):
            p.with_user(self.gestor).write({'categ_id': self.raiz.id})

    def test_no_se_desarchiva_a_mano(self):
        p = self._alta()
        with self.assertRaises(UserError):
            p.with_user(self.gestor).write({'active': True})
        with self.assertRaises(UserError):
            p.with_context(active_test=False).product_variant_ids.with_user(self.gestor).write({'active': True})

    def test_no_entra_en_una_venta(self):
        p = self._alta()
        variante = p.with_context(active_test=False).product_variant_ids[:1]
        cliente = self.env['res.partner'].create({'name': 'CLIENTE (prueba)'})
        with self.assertRaises(UserError):
            self.env['sale.order'].create({
                'partner_id': cliente.id,
                'order_line': [(0, 0, {'product_id': variante.id, 'product_uom_qty': 1})],
            })

    def test_categorizador_da_de_alta_ya_categorizado(self):
        p = self.env['product.template'].with_user(self.gestor).create(
            {'name': 'DIRECTO (prueba)', 'categ_id': self.cat.id})
        self.assertEqual(p.ferba_categorizacion, 'categorizado')
        self.assertTrue(p.active)
        self.assertEqual(p.unspsc_code_id, self.clave)

    def test_rechazo(self):
        p = self._alta()
        with self.assertRaises(UserError):
            p.with_user(self.gestor)._ferba_rechazar('  ')
        p.with_user(self.gestor)._ferba_rechazar('Duplicado (prueba)')
        self.assertEqual(p.ferba_categorizacion, 'rechazado')
        self.assertFalse(p.active)
        self.assertEqual(p.ferba_categorizacion_motivo, 'Duplicado (prueba)')
        with self.assertRaises(UserError):
            p.with_user(self.gestor)._ferba_rechazar('otra vez (prueba)')

    def test_alta_masiva_un_solo_aviso(self):
        productos = self.env['product.template'].with_user(self.alta).create(
            [{'name': 'MASIVO %d (prueba)' % i} for i in range(7)])
        self.assertEqual(set(productos.mapped('ferba_categorizacion')), {'pendiente'})
        self.assertFalse(any(productos.mapped('active')))
        self.assertFalse(productos.activity_ids, 'en altas masivas no hay una actividad por producto')

    def test_alta_rapida_de_variante_queda_oculta(self):
        pid, _nombre = self.env['product.product'].with_user(self.alta).name_create('RAPIDO (prueba)')
        variante = self.env['product.product'].with_context(active_test=False).browse(pid)
        self.assertFalse(variante.active)
        self.assertEqual(variante.product_tmpl_id.ferba_categorizacion, 'pendiente')

    def test_sin_la_opcion_no_cambia_nada(self):
        self.company.ferba_categorizacion_obligatoria = False
        p = self._alta(categ_id=self.fuera.id)
        self.assertEqual(p.ferba_categorizacion, 'categorizado')
        self.assertTrue(p.active)
        self.assertEqual(p.categ_id, self.fuera)
        p.with_user(self.alta).write({'categ_id': self.cat.id})
        self.assertEqual(p.categ_id, self.cat)

    def test_filtro_sin_categoria_contable(self):
        Template = self.env['product.template']
        viejo = Template.create({'name': 'VIEJO (prueba)', 'categ_id': self.fuera.id})
        bueno = Template.create({'name': 'BUENO (prueba)', 'categ_id': self.cat.id})
        encontrados = Template.search([('id', 'in', (viejo | bueno).ids), ('ferba_sin_categoria_contable', '=', True)])
        self.assertEqual(encontrados, viejo)
        encontrados = Template.search([('id', 'in', (viejo | bueno).ids), ('ferba_sin_categoria_contable', '=', False)])
        self.assertEqual(encontrados, bueno)
