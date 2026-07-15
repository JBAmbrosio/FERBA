{
'name': 'x_reporte',
'version': '1.0',
'author': 'company',
'category':'Tools', 
'summary': 'Gestiona configuraciones globales para los reportes qweb',
'depends':[

    'web',
] ,
'data':[
    'security/ir.model.access.csv',
    'views/view_form_reports_config_custom.xml',
    'views/view_list_reports_config_custom.xml',
],

'installable': True,
'application': True,
'license': 'LGPL-3',


}