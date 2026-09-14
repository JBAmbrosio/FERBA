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

    # Marcado NO instalable el 14-sep-2026 porque tumbaba toda la build.
    #
    # El modulo es un esqueleto sin terminar y nunca pudo haber funcionado:
    #   - models/requisition_manufactura.py hereda de 'model.Models', que no
    #     existe (es 'models.Model'): NameError al importar -> el registro de
    #     Odoo no carga y se cae la instancia entera, no solo este modulo.
    #   - el modelo no declara un solo campo y usa '_descripction'.
    #   - 'data' declara views/request_componentes_proyect_list_custom.xml, que
    #     no existe en el repositorio.
    #   - sus dos XML estan malformados (sin declaracion <?xml?>, <record> sin
    #     id ni model, <xpath> sin expr ni position) y son identicos entre si.
    #   - hereda vistas de mrp sin declarar 'mrp' en depends.
    #
    # En Production convive sin dar problemas porque nunca se instalo. Una build
    # de desarrollo arranca con base limpia e instala lo que encuentra, y ahi
    # revienta. No se borro nada: al terminarlo, se vuelve a poner en True.
    'installable': False,
    'license': 'LGPL-3'
}