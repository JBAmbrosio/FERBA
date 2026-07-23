
{

    'name': 'Requisición',
    'version' :'19.0.0.0.0',
    'author': 'SUINER',
    'category': 'tools',
    'summary': 'El modelo de RFQ permite crear solicitudes a inventario',
    'depends': [
            'web',
            'web_studio',
            'stock', 
            'studio_customization',
          
    ],
    'data': [
        #'security/ir.model.access.csv',
        'views/rfq_view_form.xml',  
        'views/rfq_view_list.xml',
    ],




    'application': True,
    'installable': True
}



