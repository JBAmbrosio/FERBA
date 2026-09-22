# -*- coding: utf-8 -*-
from odoo import fields, models

# Lista CERRADA de señales. Es a la vez el contrato del marcado
# (data-ferba-senal="...") y la validación del endpoint público: si un
# nombre no está aquí, no entra. Añadir una señal es añadir una línea.
SENALES = [
    ('interes_flexquality', 'Interés en FlexQuality'),
    ('interes_retrofit',    'Interés en modernizar su máquina'),
    ('interes_maquina',     'Interés en una etapa de la línea'),
    ('interes_fruto',       'Interés en un fruto'),
    ('contacto_directo',    'Contacto directo (teléfono, correo, WhatsApp)'),
    ('solicitud_enviada',   'Solicitud enviada'),
    ('descarga_ficha',      'Descarga de ficha o guía'),
]


class FerbaEvento(models.Model):
    """Señal de interés dejada por un visitante del sitio.

    No sustituye a website.visitor —que ya cuenta visitas, páginas y país—
    sino que lo complementa con las pocas acciones que, en una venta de
    maquinaria de seis cifras, dicen algo de verdad.

    El criterio para que algo sea una señal: que justifique una llamada.
    Por eso no hay "scroll", "tiempo en página" ni "rebote": con el volumen
    de visitas que tiene este sitio, esas métricas son ruido. Una fila aquí
    no es una estadística, es un renglón de una lista de llamadas.
    """
    _name = 'ferba.evento'
    _description = 'Ferba — señal de interés en el sitio'
    _order = 'create_date desc'
    _rec_name = 'nombre'

    nombre = fields.Selection(SENALES, string='Señal', required=True, index=True)
    detalle = fields.Char(
        string='Detalle',
        help="Qué en concreto: el canal del contacto, la etapa de la línea "
             "o el fruto. Es lo que convierte la señal en información útil.")
    url = fields.Char(string='Página')

    visitor_id = fields.Many2one(
        'website.visitor', string='Visitante', index=True, ondelete='cascade',
        help="Enlace con el visitante que Odoo ya venía siguiendo. Desde ahí "
             "se ve el recorrido completo y, si alguna vez se identifica, "
             "el contacto.")
    # Almacenado a propósito, no solo para poder filtrar por él: el día que
    # un visitante anónimo se identifica, Odoo recalcula este campo y TODAS
    # sus señales anteriores quedan etiquetadas con su nombre. Esa es media
    # capa 3 gratis.
    partner_id = fields.Many2one(related='visitor_id.partner_id', string='Contacto',
                                 store=True, readonly=True)
    pais_id = fields.Many2one(related='visitor_id.country_id', string='País', store=True, readonly=True)
    visitas = fields.Integer(related='visitor_id.visit_count', string='Visitas', readonly=True)

    # Atribución: de dónde venía cuando llegó por primera vez. Se guarda en
    # cada señal y no solo en la primera página, porque lo que se quiere
    # contestar es "¿qué campaña trae gente que además hace algo?".
    origen = fields.Char(string='Origen')
    campana = fields.Char(string='Campaña')
