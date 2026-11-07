# -*- coding: utf-8 -*-
"""Catálogo del sitio de Ferba: categorías de maquinaria, fichas de máquina y
frutos.

La entrega de diseño del cliente (nov-2026) no son páginas planas: es un
catálogo de tres niveles.

    Maquinaria (landing)  ->  Categoría (etapa)  ->  Ficha de máquina
    Frutos    (landing)  ->  Fruto               ->  (mismas fichas)

Una máquina pertenece a UNA categoría (su etapa) y a MUCHOS frutos. Su tarjeta
—nombre, tres chips, miniatura— se reutiliza en su categoría y en cada fruto
que la lista; su ficha es ÚNICA y vive en una sola URL canónica bajo su
categoría. Por eso el modelo es compartido y no se duplica contenido.

Las imágenes NO se guardan en la base: se extraen de los PDFs a WebP bajo
`static/src/img/catalogo/...` y aquí se referencia el nombre de archivo (como
hace el resto del sitio con sus `.webp`). Los campos `*_imagen` son, pues, Char
con una ruta relativa a `/ferba_website/static/src/img/`.
"""
from odoo import fields, models


# ---------------------------------------------------------------------------
#  Categoría de maquinaria (la "etapa")
# ---------------------------------------------------------------------------
class FerbaCategoria(models.Model):
    _name = 'ferba.categoria'
    _description = 'Ferba — categoría de maquinaria (etapa)'
    _order = 'secuencia, id'
    _rec_name = 'nombre'

    nombre = fields.Char(required=True, translate=True)
    slug = fields.Char(required=True, index=True,
                       help="Segmento de URL: /maquinaria/<slug>. Único.")
    secuencia = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    # Hero
    hero_antetitulo = fields.Char(translate=True, help="Miga: 'Maquinaria'.")
    hero_titulo = fields.Char(translate=True)
    hero_subtitulo = fields.Char(translate=True)
    hero_intro = fields.Html(translate=True, sanitize=False)
    hero_imagen = fields.Char(help="Ruta bajo static/src/img/, p.ej. catalogo/empaque/hero.webp")

    # Bloque narrativo de la etapa (texto a la izquierda, visual a la derecha)
    narrativa_antetitulo = fields.Char(translate=True)
    narrativa_titulo = fields.Char(translate=True)
    narrativa_intro = fields.Html(translate=True, sanitize=False)
    flujo_ids = fields.One2many('ferba.categoria.flujo', 'categoria_id')
    flujo_estilo = fields.Selection([
        ('circulos', 'Círculos numerados en hilera'),
        ('zigzag', 'Ilustraciones numeradas en zigzag (recepción)'),
        ('fila', 'Fotos en fila con flechas (lavado)'),
        ('rejilla', 'Rejilla 2x2 numerada con icono (secado)'),
        ('serpentina', 'Serpentina de 6 iconos con chevrones (transportadores, empaque)'),
        ('tarjetas', 'Tarjetas grises con foto (selección)'),
    ], default='circulos', help="Cómo se dibuja el visual de la narrativa, como lo hace cada PDF.")

    # Estructura de equipos
    subgrupo_ids = fields.One2many('ferba.categoria.subgrupo', 'categoria_id')
    maquina_ids = fields.One2many('ferba.maquina', 'categoria_id')
    equipos_antetitulo = fields.Char(translate=True, default="Nuestros equipos")
    equipos_titulo = fields.Char(translate=True, help="'Equipos para recepción y alimentación'.")
    equipos_columnas = fields.Selection([('2', '2 columnas'), ('3', '3 columnas')], default='3')
    equipos_numerados = fields.Boolean(help="Badge rojo 01, 02… en cada tarjeta (selección).")

    # Sección extra oscura entre equipos y factores (lavado: "Soluciones para producto";
    # secado: "Dos procesos que preparan el producto"). Sus puntos son factores con seccion='extra'.
    extra_antetitulo = fields.Char(translate=True)
    extra_titulo = fields.Char(translate=True)

    # "Factores que definen tu solución"
    factor_antetitulo = fields.Char(translate=True)
    factor_titulo = fields.Char(translate=True)
    factor_tema = fields.Selection([('oscuro', 'Oscuro'), ('claro', 'Claro')], default='oscuro')
    factor_ids = fields.One2many('ferba.categoria.factor', 'categoria_id')

    # Selección: sección "Calibrado + FlexQuality" y botón "Ver FlexQuality" en el hero.
    flexquality = fields.Boolean()

    def factores(self):
        self.ensure_one()
        return self.factor_ids.filtered(lambda f: f.seccion == 'factor')

    def extras(self):
        self.ensure_one()
        return self.factor_ids.filtered(lambda f: f.seccion == 'extra')

    # Banda de cierre
    cierre_titulo = fields.Char(translate=True)
    cierre_texto = fields.Text(translate=True)

    # SEO
    meta_titulo = fields.Char(translate=True)
    meta_descripcion = fields.Char(translate=True)

    _sql_constraints = [
        ('slug_uniq', 'unique(slug)', 'El slug de categoría debe ser único.'),
    ]


class FerbaCategoriaSubgrupo(models.Model):
    """Subgrupo dentro de una categoría (solo Empaque y Transportadores agrupan;
    las demás son planas y no tienen subgrupos)."""
    _name = 'ferba.categoria.subgrupo'
    _description = 'Ferba — subgrupo de categoría'
    _order = 'secuencia, id'
    _rec_name = 'nombre'

    categoria_id = fields.Many2one('ferba.categoria', required=True, ondelete='cascade')
    nombre = fields.Char(required=True, translate=True)
    secuencia = fields.Integer(default=10)


class FerbaCategoriaFactor(models.Model):
    """Una de las tarjetas de 'Factores que definen tu solución'."""
    _name = 'ferba.categoria.factor'
    _description = 'Ferba — factor de categoría'
    _order = 'secuencia, id'
    _rec_name = 'titulo'

    categoria_id = fields.Many2one('ferba.categoria', required=True, ondelete='cascade')
    seccion = fields.Selection([('factor', 'Factor'), ('extra', 'Sección extra')], default='factor', required=True)
    icono = fields.Char(help="Ruta del icono SVG bajo static/src/img/.")
    titulo = fields.Char(required=True, translate=True)
    subtitulo = fields.Char(translate=True, help="Línea chica bajo el título ('Bins o cajas').")
    texto = fields.Text(translate=True)
    enlaces = fields.Char(help="Enlaces de la sección extra: 'Texto>/url|Texto>/url'.")
    secuencia = fields.Integer(default=10)

    @property
    def enlace_lista(self):
        self.ensure_one()
        out = []
        for e in (self.enlaces or '').split('|'):
            if not e.strip():
                continue
            txt, _, href = e.partition('>')
            out.append((txt.strip(), href.strip() or '#'))
        return out


class FerbaCategoriaFlujo(models.Model):
    """Paso del diagrama de flujo de la etapa (icono + etiqueta)."""
    _name = 'ferba.categoria.flujo'
    _description = 'Ferba — paso del flujo de la etapa'
    _order = 'secuencia, id'
    _rec_name = 'etiqueta'

    categoria_id = fields.Many2one('ferba.categoria', required=True, ondelete='cascade')
    icono = fields.Char()
    imagen = fields.Char(help="Ilustración o foto del paso (recepción, lavado, selección).")
    etiqueta = fields.Char(required=True, translate=True)
    subetiqueta = fields.Char(translate=True, help="Línea chica bajo la etiqueta ('Bins o Cajas').")
    secuencia = fields.Integer(default=10)


# ---------------------------------------------------------------------------
#  Fruto
# ---------------------------------------------------------------------------
class FerbaFruto(models.Model):
    _name = 'ferba.fruto'
    _description = 'Ferba — fruto'
    _order = 'secuencia, id'
    _rec_name = 'nombre'

    nombre = fields.Char(required=True, translate=True)
    slug = fields.Char(required=True, index=True,
                       help="Segmento de URL: /frutos/<slug>. Único.")
    secuencia = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    hero_antetitulo = fields.Char(translate=True)
    hero_titulo = fields.Char(translate=True)
    hero_subtitulo = fields.Char(translate=True)
    hero_intro = fields.Html(translate=True, sanitize=False)
    hero_imagen = fields.Char()

    # Las máquinas que este fruto lista (inverso del M2M en ferba.maquina).
    maquina_ids = fields.Many2many(
        'ferba.maquina', 'ferba_maquina_fruto_rel', 'fruto_id', 'maquina_id',
        string='Equipos')

    cierre_titulo = fields.Char(translate=True)
    cierre_texto = fields.Text(translate=True)

    meta_titulo = fields.Char(translate=True)
    meta_descripcion = fields.Char(translate=True)

    _sql_constraints = [
        ('slug_uniq', 'unique(slug)', 'El slug de fruto debe ser único.'),
    ]


# ---------------------------------------------------------------------------
#  Máquina (la ficha)
# ---------------------------------------------------------------------------
class FerbaMaquina(models.Model):
    _name = 'ferba.maquina'
    _description = 'Ferba — ficha de máquina'
    _order = 'secuencia, id'
    _rec_name = 'nombre'

    nombre = fields.Char(required=True, translate=True)
    slug = fields.Char(required=True, index=True,
                       help="Segmento de URL único a nivel global: "
                            "/maquinaria/<categoria>/<slug>.")
    secuencia = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    categoria_id = fields.Many2one('ferba.categoria', required=True, ondelete='restrict')
    subgrupo_id = fields.Many2one('ferba.categoria.subgrupo', ondelete='set null',
                                  help="Opcional: solo en categorías que agrupan.")
    fruto_ids = fields.Many2many(
        'ferba.fruto', 'ferba_maquina_fruto_rel', 'maquina_id', 'fruto_id',
        string='Frutos que la listan')

    # Tarjeta (reutilizada en la categoría y en los frutos)
    tarjeta_imagen = fields.Char(help="Miniatura de la tarjeta.")
    tarjeta_texto = fields.Text(translate=True, help="Descripción corta en la tarjeta de la categoría (recepción, lavado, selección…).")
    # Ícono del MENÚ de máquinas, que un administrador sube a mano desde el backend.
    # A diferencia del resto de imágenes (rutas a .webp bajo static/), ésta vive en la
    # base para que pueda corregirse sin tocar archivos ni volver a desplegar. Si está
    # cargada, REEMPLAZA a `tarjeta_imagen` SOLO en la tarjeta del menú de máquinas; si
    # se deja vacía, se usa el render de siempre, así nada se rompe.
    menu_icono = fields.Image(
        string="Ícono del menú (manual)", max_width=1024, max_height=1024,
        help="Imagen que el administrador sube para la tarjeta del menú de máquinas. "
             "Si se deja vacía, se usa el render de 'Miniatura de la tarjeta'.")
    menu_icono_tam = fields.Integer(
        string="Tamaño del ícono (px)", default=110,
        help="Alto máximo del ícono en la tarjeta del menú, en píxeles. Súbelo o bájalo "
             "para ajustar cómo se ve. Solo aplica cuando hay un 'Ícono del menú' cargado.")
    chips = fields.Char(help="Hasta 3 etiquetas separadas por coma: 'Bolsa, Integración, Empaque continuo'.")

    # Hero de la ficha
    hero_titulo = fields.Char(translate=True, help="Título del hero si difiere del nombre (p. ej. 'Volcador rotativo de bins').")
    hero_badge_icono = fields.Char(help="Icono del badge flotante del hero (ruta bajo static/src/img/).")
    hero_badge_prefijo = fields.Char(translate=True, help="Línea chica arriba del valor: 'Hasta'.")
    hero_badge_valor = fields.Char(translate=True, help="Valor grande del badge flotante: '1,440'. Si está vacío no se pinta.")
    hero_badge_etiqueta = fields.Char(translate=True, help="Línea chica bajo el valor: 'cajas/hora'.")
    lema = fields.Char(translate=True, help="Lema bajo el nombre en el hero.")
    intro = fields.Html(translate=True, sanitize=False)
    hero_imagen = fields.Char()
    hero_tema = fields.Selection([
        ('oscuro', 'Oscuro: foto a sangre a la derecha'),
        ('claro', 'Claro: fondo blanco con el render 3D a la derecha (transportadores)'),
    ], default='oscuro')
    cierre_imagen = fields.Char(help="Foto de la banda roja de cierre; vacío = la foto común de los PDF.")
    destacado_ids = fields.One2many('ferba.maquina.destacado', 'maquina_id',
                                    help="Los 3 destacados del hero (valor + etiqueta).")
    destacados_estilo = fields.Selection([
        ('badge', 'Badge: icono + etiqueta corta (AJUSTABLE · MODULAR…)'),
        ('valor', 'Valor grande + etiqueta (200 BINS/HORA · ACERO INOXIDABLE…)'),
        ('pre', 'Etiqueta chica ARRIBA + valor grande (HASTA · 200 BANDEJAS/HORA)'),
    ], default='badge')

    # Cuerpo (bloques genéricos, se conservan por compatibilidad)
    bloque_ids = fields.One2many('ferba.maquina.bloque', 'maquina_id')

    # --- Sección "protege toda la línea" (render + 3 puntos con icono) ---
    protege_antetitulo = fields.Char(translate=True)
    protege_titulo = fields.Char(translate=True)
    protege_intro = fields.Html(translate=True, sanitize=False)
    protege_imagen = fields.Char()

    # --- EL RETO (tarjetas oscuras numeradas con ilustración) ---
    reto_antetitulo = fields.Char(translate=True, default="El reto")
    reto_titulo = fields.Html(translate=True, sanitize=False,
                              help="Puede llevar <u> para subrayar en rojo.")
    reto_intro = fields.Text(translate=True)

    # --- CÓMO FUNCIONA (pasos numerados + diagrama) ---
    paso_ids = fields.One2many('ferba.maquina.paso', 'maquina_id')
    pasos_estilo = fields.Selection([
        ('foto', 'Número rojo sobre la foto, título y texto debajo'),
        ('tarjeta', 'Tarjeta con borde: "01 Título" arriba, foto, texto (línea de siembra)'),
    ], default='foto')
    pasos_antetitulo = fields.Char(translate=True, default="Cómo funciona")
    pasos_titulo = fields.Char(translate=True)
    pasos_intro = fields.Text(translate=True)
    diagrama_imagen = fields.Char()
    diagrama_leyenda = fields.Char(
        translate=True,
        help="Etiquetas del diagrama separadas por |: 'Producto menor al calibre|Producto que continúa'.")

    # --- AJUSTE REGULABLE (render + A/B/C) ---
    ajuste_antetitulo = fields.Char(translate=True)
    ajuste_titulo = fields.Char(translate=True)
    ajuste_intro = fields.Text(translate=True)
    ajuste_imagen = fields.Char()
    ajuste_caption = fields.Char(translate=True, help="Título bajo el render (p. ej. 'Mecanismo de regulación').")
    ajuste_subcaption = fields.Char(translate=True, help="Línea bajo el título del render, dentro de la tarjeta.")
    ajuste_nota = fields.Char(translate=True, help="Nota en cursiva al pie de la sección.")

    # --- DATOS TÉCNICOS (tabla de specs + checklist/beneficios + chips) ---
    datos_antetitulo = fields.Char(translate=True, default="Datos técnicos")
    datos_tema = fields.Selection([('claro', 'Claro'), ('oscuro', 'Oscuro')],
                                  default='claro', help="Fondo de la sección de datos técnicos.")
    beneficios_antetitulo = fields.Char(translate=True, default="Beneficios principales")
    beneficios_titulo = fields.Char(translate=True,
                                    help="Título de los beneficios (columna de datos o sección propia).")
    beneficios_pos = fields.Selection([
        ('datos', 'En la columna derecha de datos técnicos'),
        ('seccion', 'Sección propia (título a la izquierda, tarjetas a la derecha)'),
    ], default='datos', help="Dónde se pintan los puntos de sección 'beneficio'.")
    beneficios_tema = fields.Selection([('oscuro', 'Oscuro'), ('claro', 'Claro (gris con tarjetas blancas)')],
                                       default='oscuro', help="Fondo de la sección propia de beneficios.")

    # --- Bloques de 4 columnas con separador rojo (línea de siembra) ---
    sistema_antetitulo = fields.Char(translate=True, help="Tarjeta negra a la derecha de los datos: 'Una línea diseñada como sistema'.")
    sistema_titulo = fields.Char(translate=True)
    construccion_antetitulo = fields.Char(translate=True, help="Sección blanca de 4 columnas: 'Características de construcción'.")
    construccion_titulo = fields.Char(translate=True)
    specs_titulo = fields.Char(translate=True, default="Especificaciones principales")
    spec_ids = fields.One2many('ferba.maquina.spec', 'maquina_id')
    specs_foto = fields.Char(help="Foto grande junto a la tabla de specs (opcional).")
    caracteristicas_titulo = fields.Char(translate=True, default="Características destacadas")

    # --- DIBUJO TÉCNICO (hornos, envasadora): plano con cotas bajo el hero/split ---
    dibujo_imagen = fields.Char(help="Plano con cotas (ruta bajo static/src/img/). Se pinta en una sección blanca tras el split.")

    # --- CALIBRADO + FLEXQUALITY (calibradores): dos secciones fijas del PDF ---
    flexquality = fields.Boolean(help="Pinta las secciones 'Lleva la selección más allá del calibrado' "
                                      "y 'Del campo a calidad comercial' (idénticas en los PDF de calibradores).")
    fq_foto = fields.Char(help="Foto grande de 'Del campo a calidad comercial'.")
    fq_calibre_icono = fields.Char(help="Icono de la tarjeta 'Calibre' (pepino, tomate…).")

    # --- APLICACIONES ---
    aplica_antetitulo = fields.Char(translate=True, default="Aplicaciones")
    aplica_titulo = fields.Char(translate=True)
    aplica_intro = fields.Text(translate=True)

    # Puntos flexibles de varias secciones (se filtran por `seccion` en la vista)
    punto_ids = fields.One2many('ferba.maquina.punto', 'maquina_id')

    def puntos(self, seccion):
        """Puntos de una sección, ordenados. Uso en QWeb: maquina.puntos('reto')."""
        self.ensure_one()
        return self.punto_ids.filtered(lambda p: p.seccion == seccion)

    # Galería
    galeria_antetitulo = fields.Char(translate=True)
    galeria_titulo = fields.Char(translate=True)
    media_ids = fields.One2many('ferba.maquina.media', 'maquina_id')

    # Banda de cierre
    cierre_titulo = fields.Char(translate=True)
    cierre_texto = fields.Text(translate=True)

    # SEO
    meta_titulo = fields.Char(translate=True)
    meta_descripcion = fields.Char(translate=True)

    _sql_constraints = [
        ('slug_uniq', 'unique(slug)', 'El slug de máquina debe ser único.'),
    ]

    @property
    def chip_lista(self):
        self.ensure_one()
        return [c.strip() for c in (self.chips or '').split(',') if c.strip()]


class FerbaMaquinaDestacado(models.Model):
    """Uno de los 3 destacados del hero de la ficha (icono + valor + etiqueta)."""
    _name = 'ferba.maquina.destacado'
    _description = 'Ferba — destacado del hero de máquina'
    _order = 'secuencia, id'
    _rec_name = 'etiqueta'

    maquina_id = fields.Many2one('ferba.maquina', required=True, ondelete='cascade')
    icono = fields.Char()
    valor = fields.Char(translate=True, help="p.ej. '200' o 'Acero'.")
    etiqueta = fields.Char(translate=True, help="p.ej. 'bins/hora' o 'inoxidable'.")
    secuencia = fields.Integer(default=10)


class FerbaMaquinaSpec(models.Model):
    """Fila de la tabla 'Datos técnicos — Especificaciones principales'."""
    _name = 'ferba.maquina.spec'
    _description = 'Ferba — especificación de máquina'
    _order = 'secuencia, id'
    _rec_name = 'etiqueta'

    maquina_id = fields.Many2one('ferba.maquina', required=True, ondelete='cascade')
    etiqueta = fields.Char(required=True, translate=True)
    valor = fields.Char(translate=True)
    secuencia = fields.Integer(default=10)


class FerbaMaquinaPaso(models.Model):
    """Paso numerado del proceso ('Una solución integral')."""
    _name = 'ferba.maquina.paso'
    _description = 'Ferba — paso del proceso de máquina'
    _order = 'secuencia, id'
    _rec_name = 'titulo'

    maquina_id = fields.Many2one('ferba.maquina', required=True, ondelete='cascade')
    imagen = fields.Char()
    titulo = fields.Char(required=True, translate=True)
    texto = fields.Text(translate=True)
    secuencia = fields.Integer(default=10)


class FerbaMaquinaBloque(models.Model):
    """Bloque de contenido variable del cuerpo de la ficha.

    Dos formas, elegidas con `tipo`:
      - split: antetítulo + título + cuerpo de texto a un lado y una imagen al
        otro ('cómo se integra a tu línea').
      - tarjetas: una rejilla de tarjetas con icono ('Beneficios principales',
        'Características del equipo').
    """
    _name = 'ferba.maquina.bloque'
    _description = 'Ferba — bloque del cuerpo de máquina'
    _order = 'secuencia, id'
    _rec_name = 'titulo'

    maquina_id = fields.Many2one('ferba.maquina', required=True, ondelete='cascade')
    secuencia = fields.Integer(default=10)
    tipo = fields.Selection([
        ('split', 'Texto + imagen'),
        ('tarjetas', 'Tarjetas con icono'),
    ], required=True, default='tarjetas')
    tema = fields.Selection([
        ('claro', 'Claro'),
        ('oscuro', 'Oscuro'),
    ], default='claro', help="Fondo de la sección.")
    antetitulo = fields.Char(translate=True)
    titulo = fields.Char(translate=True)
    cuerpo = fields.Html(translate=True, sanitize=False)
    imagen = fields.Char(help="Solo para tipo split.")
    item_ids = fields.One2many('ferba.maquina.bloque.item', 'bloque_id')


class FerbaMaquinaBloqueItem(models.Model):
    """Una tarjeta con icono dentro de un bloque de tipo 'tarjetas'."""
    _name = 'ferba.maquina.bloque.item'
    _description = 'Ferba — tarjeta de bloque de máquina'
    _order = 'secuencia, id'
    _rec_name = 'titulo'

    bloque_id = fields.Many2one('ferba.maquina.bloque', required=True, ondelete='cascade')
    icono = fields.Char()
    titulo = fields.Char(required=True, translate=True)
    texto = fields.Text(translate=True)
    secuencia = fields.Integer(default=10)


class FerbaMaquinaPunto(models.Model):
    """Punto de una de las secciones fijas de la ficha (plantilla del PDF).

    Un solo modelo cubre varias secciones, discriminadas por `seccion`:
      - protege: los 3 puntos con icono de 'protege toda la línea' (icono+titulo+texto).
      - reto: las tarjetas oscuras numeradas de 'El reto' (imagen ilustrada+titulo+texto).
      - ajuste: A/B/C de 'Ajuste regulable' (la letra sale del orden; titulo+texto).
      - caracteristica: ítem del checklist de 'Características destacadas' (titulo).
      - chip: chip destacado al pie de datos técnicos (icono+titulo).
      - aplica: tarjeta de 'Aplicaciones' (imagen+titulo+texto).
    """
    _name = 'ferba.maquina.punto'
    _description = 'Ferba — punto de sección de máquina'
    _order = 'secuencia, id'
    _rec_name = 'titulo'

    maquina_id = fields.Many2one('ferba.maquina', required=True, ondelete='cascade')
    seccion = fields.Selection([
        ('protege', 'Protege la línea'),
        ('reto', 'El reto'),
        ('ajuste', 'Ajuste regulable (A/B/C)'),
        ('beneficio', 'Beneficio (tarjeta icono, col. de datos)'),
        ('caracteristica', 'Característica (checklist)'),
        ('chip', 'Chip destacado'),
        ('aplica', 'Aplicación'),
        ('sistema', 'Sistema (tarjeta negra de 4 columnas junto a los datos)'),
        ('construccion', 'Construcción (sección blanca de 4 columnas)'),
    ], required=True, index=True)
    icono = fields.Char(help="Ruta del icono/SVG bajo static/src/img/.")
    imagen = fields.Char(help="Imagen bajo static/src/img/ (reto, aplicaciones).")
    titulo = fields.Char(translate=True)
    subtitulo = fields.Char(translate=True, help="Línea corta en rojo bajo el título (beneficios).")
    texto = fields.Text(translate=True)
    secuencia = fields.Integer(default=10)


class FerbaMaquinaMedia(models.Model):
    """Elemento de la galería (imagen, o video con su miniatura)."""
    _name = 'ferba.maquina.media'
    _description = 'Ferba — galería de máquina'
    _order = 'secuencia, id'
    _rec_name = 'imagen'

    maquina_id = fields.Many2one('ferba.maquina', required=True, ondelete='cascade')
    imagen = fields.Char(help="Miniatura/imagen.")
    es_video = fields.Boolean()
    video_url = fields.Char(help="URL del video (YouTube/Vimeo) si es_video.")
    secuencia = fields.Integer(default=10)
