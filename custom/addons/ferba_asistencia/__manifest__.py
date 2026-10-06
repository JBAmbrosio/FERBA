{
    'name': 'FERBA - Control de Asistencia',
    'version': '19.0.2.1.0',
    'summary': 'Centro de control de asistencia: detecta marcas incompletas y '
               'ausencias, permite corregirlas y validarlas para la nomina, sin Excel.',
    'description': """
FERBA - Control de Asistencia
=============================
El checador alimenta las asistencias de Odoo, pero cuando alguien olvida marcar
entrada o salida las horas salen mal. Este modulo le da a RH UN solo lugar para:

- Ver las asistencias que NECESITAN REVISION (sin salida, jornada absurda,
  sin horas) con el motivo, en tiempo real.
- Corregir la entrada/salida EN LINEA (adios al Excel de ida y vuelta).
- VALIDAR cada asistencia cuando queda bien, para que la nomina salga limpia.
- LISTA DE PERIODO: reconstruye el periodo completo (cada empleado, cada dia)
  cruzando el horario con las marcas para detectar a quien NO marco (ausente),
  quien marco a medias (incompleto), descansos, festivos y permisos.

Se apoya en el modulo nativo de Asistencias; no cambia la nomina.
""",
    'author': 'FERBA',
    'category': 'Human Resources/Attendances',
    'license': 'LGPL-3',
    'depends': ['hr_attendance', 'hr_holidays'],
    'data': [
        'security/ferba_asistencia_security.xml',
        'security/ir.model.access.csv',
        'views/ferba_asistencia_views.xml',
        'views/ferba_asistencia_dia_views.xml',
        'views/hr_employee_views.xml',
    ],
    'application': False,
    'installable': True,
}
