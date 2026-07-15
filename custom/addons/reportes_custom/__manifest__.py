# -*- coding: utf-8 -*-
{
    'name': 'Personalización de reportes qweb',
    'version': '1.0.1',
    'author': 'SUINER',
    'category': 'Sales',
    'summary': 'Cambia la arquitectura de los diseños nativos de qweb',
    'depends': [
        'web',
        'sale',
        'x_reporte',
        
        

    ],
    'data': [
        #SALEORDEN
        'views/report_saleorder_document_template_custom_ferba.xml',
        'views/external_layout_standar_template_custom_ferba.xml',
       
        
    ],
    'assets':{
        'web.report_assets_pdf':[
            'custom_reports/static/src/scss/styles_custom_reports.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}