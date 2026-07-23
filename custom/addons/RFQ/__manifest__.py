
{

    'name': 'Requisición',
    'version' :'19.0.0.0.0',
    'author': 'company',
    'summary': 'El modelo de RFQ permite crear solicitudes a inventario',
    'depends': [
            'base',
            'mail',
            'stock',
            'mrp',
            'sale',
            'project',
            'analytic',
    ],
    'data': [
        'security/ir.model.access.csv',
        #'data/ir_sequence.xml',
        'views/rfq_view_form.xml',  
        'views/rfq_view_list.xml',
    ],




    'application': True,
    'installable': True
}



