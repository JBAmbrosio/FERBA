{
'name': 'x_reporte',
'version': '1.1',
'author': 'company',
'category':'Tools',
'summary': 'Gestiona configuraciones globales para los reportes qweb (incluye politica de 2 decimales)',
'depends':[

    'web',
    # Sus reglas de acceso referencian hr.group_hr_user y hr.group_hr_manager.
    # Sin declarar 'hr' esos grupos no resuelven, group_id queda NULL y la regla
    # pasa a aplicar a TODOS los usuarios en vez de solo a RH.
    'hr',
] ,
'data':[
    'security/ir.model.access.csv',
    'views/view_form_reports_config_custom.xml',
    'views/view_list_reports_config_custom.xml',
    'data/decimal_precision_cap.xml',
],

'installable': True,
'application': True,
'license': 'LGPL-3',


}