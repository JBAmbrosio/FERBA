E = env['hr.employee']
nombres = [('Ana Prueba', '1234'), ('Beto Prueba', '4321'), ('Carla Sin Nip', False)]
for n, pin in nombres:
    if not E.search([('name', '=', n)]):
        E.create({'name': n, 'pin': pin})
env.cr.execute("DELETE FROM ferba_checador_evento")
env.cr.execute("DELETE FROM hr_attendance")
env.cr.execute("UPDATE hr_employee SET checador_fallos = 0, checador_bloqueado_hasta = NULL")
env.invalidate_all()
C = env['ferba.checador']
c = C.search([('name', '=', 'Tableta prueba')]) or C.create({'name': 'Tableta prueba', 'company_id': env.company.id})
c.action_generar_codigo()
env['res.users'].browse(2).write({'password': 'admin'})
print('CODIGO=%s' % c.codigo_alta)
print('EMPLEADOS=%s' % E.search([('name', 'like', 'Prueba')]).mapped(lambda e: (e.id, e.name)))
print('SINNIP=%s' % E.search([('name', '=', 'Carla Sin Nip')]).id)
env.cr.commit()
