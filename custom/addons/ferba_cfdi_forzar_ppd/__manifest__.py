{
    'name': 'FERBA - Forzar PPD por termino de pago (CFDI)',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations/EDI',
    'summary': 'Un termino de pago puede marcar "Forzar PPD" y sus facturas toman la Politica de pago CFDI PPD automaticamente.',
    'description': """
Forzar PPD por termino de pago
==============================
Odoo (l10n_mx_edi) decide la Politica de pago del CFDI (PUE / PPD) por el
vencimiento de la factura: un plazo corto en el mismo mes sale PUE y un plazo
largo o de mes posterior sale PPD. Para ventas a credito que en la practica se
liquidan de forma diferida (aunque el plazo "de papel" sea corto), eso deja la
factura en PUE cuando fiscalmente corresponde PPD.

Este modulo agrega al termino de pago la casilla "Forzar PPD (CFDI)". Cuando
esta activada, toda factura que use ese termino toma la Politica de pago PPD
automaticamente, sin importar el plazo ni la fecha de vencimiento.

- No modifica el modulo l10n_mx_edi: extiende el calculo llamando primero al
  estandar de Odoo y forzando PPD solo despues, solo en las facturas cuyo
  termino lo pide. A prueba de actualizaciones.
- El campo Politica de pago sigue siendo editable: el usuario puede cambiarlo a
  mano si una factura concreta es distinta.
- Trae listo un termino "7 Dias (PPD)" (100% a 7 dias, con la casilla activada).

RECORDATORIO FISCAL: una factura PPD obliga a emitir Complemento de Pago (REP)
por cada abono del cliente. Usar "Forzar PPD" solo en ventas a credito reales.
""",
    'author': 'FERBA',
    'license': 'LGPL-3',
    'depends': ['l10n_mx_edi'],
    'data': [
        'views/account_payment_term_views.xml',
        'data/account_payment_term_data.xml',
    ],
    'installable': True,
    'application': False,
}
