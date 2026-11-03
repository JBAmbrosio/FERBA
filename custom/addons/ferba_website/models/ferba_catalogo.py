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

    # Bloque narrativo de la etapa (diagrama de flujo con iconos)
    narrativa_antetitulo = fields.Char(translate=True)
    narrativa_titulo = fields.Char(translate=True)
    narrativa_intro = fields.Html(translate=True, sanitize=False)
    flujo_ids = fields.One2many('ferba.categoria.flujo', 'categoria_id')

    # Estructura de equipos
    subgrupo_ids = fields.One2many('ferba.categoria.subgrupo', 'categoria_id')
    maquina_ids = fields.One2many('ferba.maquina', 'categoria_id')

    # "Factores que definen tu solución"
    factor_titulo = fields.Char(translate=True)
    factor_ids = fields.One2many('ferba.categoria.factor', 'categoria_id')

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
    icono = fields.Char(help="Ruta del icono SVG bajo static/src/img/.")
    titulo = fields.Char(required=True, translate=True)
    texto = fields.Text(translate=True)
    secuencia = fields.Integer(default=10)


class FerbaCategoriaFlujo(models.Model):
    """Paso del diagrama de flujo de la etapa (icono + etiqueta)."""
    _name = 'ferba.categoria.flujo'
    _description = 'Ferba — paso del flujo de la etapa'
    _order = 'secuencia, id'
    _rec_name = 'etiqueta'

    categoria_id = fields.Many2one('ferba.categoria', required=True, ondelete='cascade')
    icono = fields.Char()
    etiqueta = fields.Char(required=True, translate=True)
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
    chips = fields.Char(help="Hasta 3 etiquetas separadas por coma: 'Bolsa, Integración, Empaque continuo'.")

    # Hero de la ficha
    lema = fields.Char(translate=True, help="Lema bajo el nombre en el hero.")
    intro = fields.Html(translate=True, sanitize=False)
    hero_imagen = fields.Char()
    destacado_ids = fields.One2many('ferba.maquina.destacado', 'maquina_id',
                                    help="Los 3 destacados del hero (valor + etiqueta).")

    # Cuerpo
    bloque_ids = fields.One2many('ferba.maquina.bloque', 'maquina_id')
    paso_ids = fields.One2many('ferba.maquina.paso', 'maquina_id')
    pasos_antetitulo = fields.Char(translate=True)
    pasos_titulo = fields.Char(translate=True)

    # Datos técnicos
    specs_titulo = fields.Char(translate=True, default="Especificaciones principales")
    spec_ids = fields.One2many('ferba.maquina.spec', 'maquina_id')
    specs_foto = fields.Char(help="Foto grande junto a la tabla de specs.")

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
