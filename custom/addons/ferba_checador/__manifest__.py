{
    'name': 'FERBA - Checador',
    'version': '19.0.1.2.0',
    'summary': 'Checador en tableta con app propia: la tableta se da de alta una '
               'vez con un codigo y marca entradas y salidas con foto y NIP, sin '
               'usuario de Odoo y sin direccion publica.',
    'description': """
FERBA - Checador
================
Sustituye al "Modo quiosco" abierto en un navegador con un usuario de Odoo.

- Cada tableta es un CHECADOR dado de alta con un codigo de un solo uso. Sin esa
  alta el servidor no contesta. Se revoca desde Odoo si la tableta se pierde.
- El trabajador toca su foto, escribe su NIP y la tableta toma una foto.
- NIP obligatorio, con bloqueo temporal tras varios intentos fallidos.
- Cada intento (aceptado, rechazado o NIP fallido) queda en una bitacora que no
  se edita. Las marcas aceptadas caen en las Asistencias nativas de Odoo.
""",
    'author': 'FERBA',
    'category': 'Human Resources/Attendances',
    'license': 'LGPL-3',
    'depends': ['hr_attendance', 'mail'],
    'data': [
        'security/ferba_checador_security.xml',
        'security/ir.model.access.csv',
        'views/ferba_checador_views.xml',
        'data/ferba_checador_data.xml',
    ],
    'application': False,
    'installable': True,
}
