{
'name': 'Requicisiones por proyecto',
'version': '19.0.0.0',
'author': 'SUINER',
'category': 'mrp',
'depends': ['web'],
'summary': 'El modelo geenera las requiciones por proyecto en almacén por proyecto',


'data':
    [
        'views/request_componentes_proyect_list_custom.xml',
        'views/request_componentes_proyect_form_custom.xml'
    ],



    'aplication': False,
    'installable': True
}