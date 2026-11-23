import base64, json, sys, time, urllib.request, urllib.error, uuid

B = 'http://127.0.0.1:18069'
CODIGO = sys.argv[1]
ANA, BETO, CARLA = 2, 3, 4
# JPEG minimo valido (1x1)
JPG = base64.b64encode(bytes.fromhex(
    'ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbfcffd9')).decode()


def llama(ruta, cuerpo=None, token=None, get=False):
    h = {'Content-Type': 'application/json'}
    if token:
        h['X-Checador-Token'] = token
    r = urllib.request.Request(B + ruta, None if get else json.dumps(cuerpo or {}).encode(), h)
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            d = f.read()
            return f.status, (json.loads(d) if 'json' in f.headers.get('Content-Type', '') else d)
    except urllib.error.HTTPError as e:
        d = e.read()
        try:
            return e.code, json.loads(d)
        except Exception:
            return e.code, d[:200]


ok = mal = 0
def prueba(nombre, cond, detalle=''):
    global ok, mal
    if cond:
        ok += 1
    else:
        mal += 1
    print(('OK   ' if cond else 'FALLA') + ' ' + nombre + ('' if cond else '  -> %s' % (detalle,)))


for _ in range(60):
    try:
        urllib.request.urlopen(B + '/web/login', timeout=5)
        break
    except Exception:
        time.sleep(2)

s, r = llama('/ferba/checador/v1/sync', {})
prueba('sync sin token = 401', s == 401, (s, r))
s, r = llama('/ferba/checador/v1/sync', {}, token='inventado')
prueba('sync con token falso = 401', s == 401, (s, r))
s, r = llama('/ferba/checador/v1/marcar', {'empleado': ANA, 'nip': '1234', 'uuid': str(uuid.uuid4())})
prueba('marcar sin token = 401', s == 401, (s, r))
s, r = llama('/ferba/checador/v1/foto/%d' % ANA, get=True)
prueba('foto sin token = 401', s == 401, (s, r))
s, r = llama('/ferba/checador/v1/alta', {'codigo': '00000000', 'equipo': {}})
prueba('alta con codigo malo = 403', s == 403, (s, r))

s, r = llama('/ferba/checador/v1/alta', {'codigo': CODIGO, 'equipo': {'version_app': '1.0.0', 'modelo': 'prueba', 'android': '14'}})
prueba('alta con codigo bueno = 200 + token', s == 200 and r.get('token'), (s, r))
T = r.get('token') if isinstance(r, dict) else None
print('     alta ->', {k: v for k, v in r.items() if k != 'token'} if isinstance(r, dict) else r)
s, r2 = llama('/ferba/checador/v1/alta', {'codigo': CODIGO, 'equipo': {}})
prueba('el codigo no sirve dos veces = 403', s == 403, (s, r2))

s, r = llama('/ferba/checador/v1/sync', {'equipo': {'bateria': 80, 'cargando': True, 'version_app': '1.0.0'}}, token=T)
prueba('sync = 200 con empleados y config', s == 200 and len(r.get('empleados', [])) >= 3 and 'config' in r, (s, r))
if s == 200:
    print('     config ->', r['config'])
    for e in r['empleados']:
        print('     ', e)
    carla = [e for e in r['empleados'] if e['id'] == CARLA]
    prueba('sync marca a quien no tiene NIP', carla and carla[0]['tiene_nip'] is False, carla)
    prueba('sync no expone el NIP', 'pin' not in json.dumps(r['empleados']).lower().replace('tiene_nip', ''), '')

s, r = llama('/ferba/checador/v1/foto/%d' % ANA, token=T, get=True)
prueba('foto con token = 200 con bytes', s == 200 and isinstance(r, bytes) and len(r) > 50, (s, str(r)[:80]))

s, r = llama('/ferba/checador/v1/marcar', {'empleado': CARLA, 'nip': '', 'uuid': str(uuid.uuid4())}, token=T)
prueba('sin NIP + NIP vacio = rechazado (sin_nip)', r.get('ok') is False and r.get('resultado') == 'sin_nip', (s, r))

s, r = llama('/ferba/checador/v1/marcar', {'empleado': ANA, 'nip': '0000', 'uuid': str(uuid.uuid4())}, token=T)
prueba('NIP malo = nip_fallido con restantes=4', r.get('resultado') == 'nip_fallido' and r.get('restantes') == 4, (s, r))

u = str(uuid.uuid4())
s, r = llama('/ferba/checador/v1/marcar', {'empleado': ANA, 'nip': '1234', 'uuid': u, 'foto': JPG}, token=T)
prueba('NIP bueno = entrada', r.get('ok') is True and r.get('resultado') == 'entrada', (s, r))
print('     marcar ->', r)
s, rr = llama('/ferba/checador/v1/marcar', {'empleado': ANA, 'nip': '1234', 'uuid': u, 'foto': JPG}, token=T)
prueba('mismo uuid (reintento) = misma respuesta, sin marca nueva', rr.get('resultado') == 'entrada' and rr.get('ok') is True, (s, rr))
s, r = llama('/ferba/checador/v1/marcar', {'empleado': ANA, 'nip': '1234', 'uuid': str(uuid.uuid4())}, token=T)
prueba('segunda marca inmediata = doble (rechazada)', r.get('resultado') == 'doble' and r.get('ok') is False, (s, r))

for i in range(5):
    s, r = llama('/ferba/checador/v1/marcar', {'empleado': BETO, 'nip': '9999', 'uuid': str(uuid.uuid4())}, token=T)
prueba('5o NIP malo avisa del bloqueo y hasta cuando', r.get('restantes') == 0 and r.get('hasta_utc'), (s, r))
print('     5o fallo ->', r)
s, r = llama('/ferba/checador/v1/marcar', {'empleado': BETO, 'nip': '4321', 'uuid': str(uuid.uuid4())}, token=T)
prueba('bloqueado: ni el NIP bueno pasa', r.get('resultado') == 'bloqueado' and r.get('ok') is False, (s, r))

s, r = llama('/ferba/checador/v1/marcar', {'empleado': 999999, 'nip': '1', 'uuid': str(uuid.uuid4())}, token=T)
prueba('empleado inexistente no truena (no 500)', s != 500, (s, r))
s, r = llama('/ferba/checador/v1/marcar', {'empleado': ANA, 'nip': '1234', 'uuid': str(uuid.uuid4()), 'foto': 'A' * 700000}, token=T)
prueba('foto gigante no truena (no 500)', s != 500, (s, r))
s, r = llama('/ferba/checador/v1/marcar', {'empleado': ANA, 'nip': '1234', 'uuid': str(uuid.uuid4()), 'foto': 'no-es-base64!!'}, token=T)
prueba('foto basura no truena (no 500)', s != 500, (s, r))

print('TOKEN=' + (T or ''))
print('\n%d OK, %d FALLAS' % (ok, mal))
