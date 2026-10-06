{
    'name': 'FERBA - Control de Asistencia',
    'version': '19.0.1.0.0',
    'summary': 'Centro de control de asistencia: detecta marcas incompletas, '
               'permite corregirlas en linea y validarlas para la nomina, sin Excel.',
    'description': """
FERBA - Control de Asistencia
=============================
El checador alimenta las asistencias de Odoo, pero cuando alguien olvida marcar
entrada o salida las horas salen mal. Este modulo le da a RH UN solo lugar para:

- Ver las asistencias que NECESITAN REVISION (sin salida, jornada absurda,
  sin horas) con el motivo, en tiempo real.
- Corregir la entrada/salida EN LINEA (adios al Excel de ida y vuelta).
- VALIDAR cada asistencia cuando queda bien, para que la nomina salga limpia.

Se apoya en el modulo nativo de Asistencias; no cambia la nomina.
""",
    'author': 'FERBA',
    'category': 'Human Resources/Attendances',
    'license': 'LGPL-3',
    'depends': ['hr_attendance'],
    'data': [
        'security/ferba_asistencia_security.xml',
        'views/ferba_asistencia_views.xml',
    ],
    'application': False,
    'installable': True,
}
