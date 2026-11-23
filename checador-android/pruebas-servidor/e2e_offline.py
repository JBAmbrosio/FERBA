"""Marcas hechas sin red: hora real, plazo, conflicto, NIP y reintentos. Uso: e2e_offline.py <codigo>"""
import base64, hashlib, json, sys, urllib.request, urllib.error, uuid
from datetime import datetime, timedelta, timezone

B = 'http://127.0.0.1:18069'
CODIGO = sys.argv[1]
ANA, BETO, CARLA = 2, 3, 4
H = 3600 * 1000
ok = mal = 0


def llama(ruta, cuerpo=None, token=None, cookie=None):
    h = {'Content-Type': 'application/json'}
    if token:
        h['X-Checador-Token'] = token
    if cookie:
        h['Cookie'] = cookie
    r = urllib.request.Request(B + ruta, json.dumps(cuerpo or {}).encode(), h)
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read()), f.headers.get('Set-Cookie', '')
    except urllib.error.HTTPError as e:
        d = e.read()
        try:
            return e.code, json.loads(d), ''
        except Exception:
            return e.code, {'crudo': d[:200].decode('utf8', 'replace')}, ''


def prueba(nombre, cond, detalle=''):
    global ok, mal
    ok, mal = (ok + 1, mal) if cond else (ok, mal + 1)
    print(('OK   ' if cond else 'FALLA') + ' ' + nombre + ('' if cond else '  -> %s' % (detalle,)))


def marca(emp, nip, hace_ms=None, fecha=None, uid=None, online=False):
    c = {'empleado': emp, 'nip': nip, 'uuid': uid or str(uuid.uuid4())}
    if not online:
        c['offline'] = True
        if hace_ms is not None:
            c['hace_ms'] = hace_ms
        if fecha:
            c['fecha_dispositivo'] = fecha
    return llama('/ferba/checador/v1/marcar', c, token=T)[1]


def hace(r):
    """Horas entre la fecha que registro el servidor y ahora."""
    f = datetime.strptime(r['fecha_utc'], '%Y-%m-%d %H:%M:%S')
    s = datetime.strptime(r['servidor_utc'], '%Y-%m-%d %H:%M:%S')
    return (s - f).total_seconds() / 3600


s, r, _ = llama('/ferba/checador/v1/alta', {'codigo': CODIGO, 'equipo': {'modelo': 'prueba offline'}})
T = r.get('token')
prueba('alta', s == 200 and T, (s, r))

s, r, _ = llama('/ferba/checador/v1/sync', {}, token=T)
cfg = r.get('config', {})
prueba('sync trae las reglas para validar sin red', cfg.get('offline') is True and cfg.get('max_fallos') == 5
       and cfg.get('minutos_bloqueo') == 5 and cfg.get('segundos_doble_marca') == 120, cfg)
emp = {e['id']: e for e in r.get('empleados', [])}
v = emp[ANA].get('nip_v', '')
p = v.split('$')
bien = len(p) == 4 and p[0] == 'v1' and base64.b64encode(
    hashlib.pbkdf2_hmac('sha256', b'1234', base64.b64decode(p[2]), int(p[1]))).decode() == p[3]
prueba('la huella de Ana valida su NIP 1234', bien, v)
mala = len(p) == 4 and base64.b64encode(
    hashlib.pbkdf2_hmac('sha256', b'1235', base64.b64decode(p[2]), int(p[1]))).decode() == p[3]
prueba('la huella rechaza otro NIP', not mala)
prueba('quien no tiene NIP no tiene huella', emp[CARLA].get('nip_v') == '', emp[CARLA])
prueba('la sincronizacion no trae el NIP en claro', '"1234"' not in json.dumps(r) and '"4321"' not in json.dumps(r))
prueba('huellas distintas por empleado (sal propia)', emp[ANA]['nip_v'] != emp[BETO]['nip_v'])

r = marca(ANA, '1234', hace_ms=3 * H)
prueba('entrada de hace 3 h queda con su hora real', r.get('resultado') == 'entrada' and 2.98 < hace(r) < 3.02, r)
r = marca(ANA, '1234', hace_ms=3 * H - 30_000)
prueba('segunda marca 30 s despues (tambien sin red) = doble', r.get('resultado') == 'doble', r)
u = str(uuid.uuid4())
r = marca(ANA, '1234', hace_ms=1 * H, uid=u)
prueba('salida de hace 1 h cierra esa asistencia', r.get('resultado') == 'salida' and 0.98 < hace(r) < 1.02
       and 1.95 < r.get('horas_hoy', 0) < 2.05, r)
r2 = marca(ANA, '1234', hace_ms=1 * H + 5000, uid=u)
prueba('reenviar la misma marca no la duplica', r2.get('repetido') is True and r2.get('resultado') == 'salida', r2)

r = marca(ANA, '1234', hace_ms=100 * H)
prueba('marca de hace 100 h = fuera de plazo, no crea asistencia', r.get('resultado') == 'vencida' and r.get('ok') is False, r)

for i in range(6):
    r = marca(BETO, '0000', hace_ms=10 * 60_000 + i * 1000)
prueba('NIP malo llegado sin red queda en bitacora', r.get('resultado') == 'nip_fallido', r)
r = marca(BETO, '4321', online=True)
prueba('6 NIP malos sin red NO bloquean la tarjeta en el servidor', r.get('resultado') == 'entrada', r)
r = marca(BETO, '4321', hace_ms=2 * H)
prueba('marca vieja con una entrada posterior ya abierta = conflicto', r.get('resultado') == 'conflicto' and r.get('ok') is False, r)

r = marca(CARLA, '', hace_ms=60_000)
prueba('sin NIP tampoco pasa sin red', r.get('resultado') == 'sin_nip', r)

pasado = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
r = marca(ANA, '1234', fecha=pasado)
prueba('tras reiniciar se usa la hora del reloj de la tableta', r.get('resultado') == 'entrada' and 0.49 < hace(r) < 0.51, r)

futuro = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S')
r = marca(ANA, '1234', fecha=futuro)
prueba('reloj de la tableta adelantado: nunca se acepta una hora futura',
       r.get('resultado') == 'salida' and -0.01 < hace(r) < 0.01, r)

r = marca(ANA, '1234', hace_ms='basura', fecha='no-es-fecha')
prueba('datos de hora ilegibles = vencida, sin tronar', r.get('resultado') == 'vencida', r)

# Apagar el modo sin conexion desde Odoo y volver a intentar.
s, r, ck = llama('/web/session/authenticate', {'jsonrpc': '2.0', 'params': {'db': 'checador2', 'login': 'admin', 'password': 'admin'}})
cookie = ck.split(';')[0]


def odoo(modelo, metodo, args, kw=None):
    return llama('/web/dataset/call_kw', {'jsonrpc': '2.0', 'params': {
        'model': modelo, 'method': metodo, 'args': args, 'kwargs': kw or {}}}, cookie=cookie)[1]


ids = odoo('ferba.checador', 'search', [[]]).get('result') or []
odoo('ferba.checador', 'write', [ids, {'permite_offline': False}])
r = marca(BETO, '4321', hace_ms=60_000)
prueba('con el modo apagado en Odoo, la marca sin red no se acepta', r.get('resultado') == 'vencida', r)
s, r, _ = llama('/ferba/checador/v1/sync', {}, token=T)
prueba('con el modo apagado no se entregan huellas', r['config'].get('offline') is False
       and all(e.get('nip_v') == '' for e in r['empleados']), r['config'])
odoo('ferba.checador', 'write', [ids, {'permite_offline': True}])

ev = odoo('ferba.checador.evento', 'search_read', [[['offline', '=', True]]],
          {'fields': ['employee_id', 'resultado', 'fecha', 'create_date', 'hora_dispositivo', 'attendance_id']}).get('result', [])
prueba('la bitacora marca las hechas sin conexion', len(ev) >= 12, len(ev))
del_reloj = [e for e in ev if e['hora_dispositivo']]
prueba('y distingue las que usaron el reloj de la tableta', len(del_reloj) >= 2, len(del_reloj))
at = odoo('hr.attendance', 'search_read', [[['employee_id', '=', ANA]]],
          {'fields': ['check_in', 'check_out', 'worked_hours', 'in_mode', 'out_mode'], 'order': 'check_in'}).get('result', [])
for a in at:
    print('     asistencia Ana:', a['check_in'], '->', a['check_out'], 'horas=%.2f' % a['worked_hours'], a['in_mode'], a['out_mode'])
prueba('Ana: una asistencia de 2 h (hace 3 h a hace 1 h) y otra de 30 min', len(at) == 2
       and abs(at[0]['worked_hours'] - 2) < 0.02 and abs(at[1]['worked_hours'] - 0.5) < 0.02, at)

print('\n%d OK, %d FALLAS' % (ok, mal))
