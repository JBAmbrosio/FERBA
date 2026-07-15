{
'name': 'x_reporte',
'version': '1.0',
'author': 'SUINER',
'category':'sales', 
'summary': 'Gestiona configuraciones globales para los reportes qweb',
'deppends':[

    'web',
] ,
'data':[
    'secutity/ir.model.access.csv',
    'views/view_form_reports_config_custom.xml',
    'views/view_list_reports_config_custom.xml',
],

'installable': True,
'aplication': True,
'license': 'LGPL-3',


}