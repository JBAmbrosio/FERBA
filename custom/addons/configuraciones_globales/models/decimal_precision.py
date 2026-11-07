from odoo import api, models

import logging

_logger = logging.getLogger(__name__)

# Maximo de decimales que se muestran/guardan en todo Odoo.
# Pedido por el Ing. Francisco (junta 27-oct-2026): "despues de la coma, 2
# digitos maximo". Aplica a numeros de negocio (precio unitario, costos,
# descuentos, cantidades, pesos, contabilidad), no a datos tecnicos.
MAX_DECIMALES = 2


class DecimalPrecision(models.Model):
    """Fuerza 'maximo 2 decimales' de forma global.

    En Odoo la cantidad de decimales de los campos Float/Monetary no se define
    campo por campo, sino con el catalogo de Precisiones decimales
    (``decimal.precision``): Product Price, Discount, Account, cantidades,
    pesos, etc. Bajando todas esas precisiones a 2 se logra "2 decimales en
    todo Odoo" de una sola vez, incluyendo el precio unitario que salia con 5
    decimales.
    """

    _inherit = 'decimal.precision'

    @api.model
    def _ferba_cap_dos_decimales(self):
        """Baja a 2 toda precision decimal configurada por encima de 2.

        Se dispara al instalar y en CADA actualizacion del modulo
        (ver ``data/decimal_precision_cap.xml``), asi que tambien alcanza las
        precisiones que registren modulos nuevos en el futuro. Es idempotente:
        solo toca las que estan arriba de 2 y deja intactas las que ya cumplen.

        Odoo invalida su cache de precision al escribir ``digits`` (lo hace el
        propio modelo en ``write``), por lo que el cambio aplica sin reinicio.
        """
        por_bajar = self.search([('digits', '>', MAX_DECIMALES)])
        for prec in por_bajar:
            _logger.info(
                "FERBA 2 decimales: '%s' %s -> %s",
                prec.name, prec.digits, MAX_DECIMALES,
            )
            prec.digits = MAX_DECIMALES
        if por_bajar:
            _logger.info(
                "FERBA 2 decimales: %s precision(es) ajustada(s) a %s",
                len(por_bajar), MAX_DECIMALES,
            )
        return True
