{
    'name': 'FERBA - Acuse de solicitud de cancelación CFDI',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations/EDI',
    'summary': 'Guarda el acuse del SAT que devuelve SW al solicitar la cancelación de un CFDI',
    'description': """
Al solicitar la cancelación de un CFDI con el PAC SW, el servicio de SW responde con el acuse
que emite el SAT (campo data.acuse). Odoo (l10n_mx_edi, método _sw_cancel) solo revisa el código
de estatus y descarta ese acuse. Este módulo lo conserva:

* Registro "Acuse de cancelación" con el XML original firmado por el SAT y sus datos leídos.
* Adjunto del XML y nota en el historial de la factura o pago.
* Botón en la factura y menú Contabilidad > Clientes > Acuses de cancelación.
* PDF legible del acuse para enviarlo al cliente.

No cambia el flujo de cancelación de Odoo: si guardar el acuse fallara, la cancelación sigue igual
y se deja una nota en la factura.
""",
    'author': 'FERBA',
    'depends': ['l10n_mx_edi'],
    'data': [
        'security/ir.model.access.csv',
        'security/ferba_cfdi_acuse_security.xml',
        'report/ferba_cfdi_acuse_report.xml',
        'views/ferba_cfdi_acuse_views.xml',
        'views/account_move_views.xml',
    ],
    'installable': True,
    'license': 'LGPL-3',
}
