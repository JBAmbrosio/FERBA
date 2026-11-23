"""Publicacion y descarga de versiones de la app. Uso: e2e_app.py <codigo> <apk>"""
import base64, hashlib, json, sys, urllib.request, urllib.error

B = 'http://127.0.0.1:18069'
CODIGO, APK = sys.argv[1], sys.argv[2]
crudo = open(APK, 'rb').read()
SHA = hashlib.sha256(crudo).hexdigest()
ok = mal = 0


def llama(ruta, cuerpo=None, token=None, cookie=None, get=False):
    h = {'Content-Type': 'application/json'}
    if token:
        h['X-Checador-Token'] = token
    if cookie:
        h['Cookie'] = cookie
    r = urllib.request.Request(B + ruta, None if get else json.dumps(cuerpo or {}).encode(), h)
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            d = f.read()
            return f.status, (json.loads(d) if 'json' in f.headers.get('Content-Type', '') else d), f.headers.get('Set-Cookie', '')
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200], ''


def prueba(nombre, cond, detalle=''):
    global ok, mal
    ok, mal = (ok + 1, mal) if cond else (ok, mal + 1)
    print(('OK   ' if cond else 'FALLA') + ' ' + nombre + ('' if cond else '  -> %s' % (str(detalle)[:300],)))


s, r, ck = llama('/web/session/authenticate', {'jsonrpc': '2.0', 'params': {'db': 'checador2', 'login': 'admin', 'password': 'admin'}})
cookie = ck.split(';')[0]


def odoo(modelo, metodo, args, kw=None):
    return llama('/web/dataset/call_kw', {'jsonrpc': '2.0', 'params': {
        'model': modelo, 'method': metodo, 'args': args, 'kwargs': kw or {}}}, cookie=cookie)[1]


viejas = odoo('ferba.checador.app', 'search', [[]]).get('result') or []
if viejas:
    odoo('ferba.checador.app', 'unlink', [viejas])

s, r, _ = llama('/ferba/checador/v1/alta', {'codigo': CODIGO, 'equipo': {'modelo': 'prueba app'}})
T = r.get('token')
yo = odoo('ferba.checador', 'search', [[['name', '=', 'Tableta e2e']]]).get('result')
otra = odoo('ferba.checador', 'search', [[['name', '=', 'Tableta prueba']]]).get('result')

s, r, _ = llama('/ferba/checador/v1/sync', {}, token=T)
prueba('sin versiones publicadas la sincronizacion no ofrece nada', s == 200 and r.get('app') is None, r.get('app'))

r = odoo('ferba.checador.app', 'create', [{'name': 'falso', 'version_code': 50, 'apk': base64.b64encode(b'esto no es un apk').decode()}])
prueba('un archivo que no es APK se rechaza al subirlo', 'error' in r, r)

b64 = base64.b64encode(crudo).decode()
r = odoo('ferba.checador.app', 'create', [{'name': '1.1.1', 'version_code': 3, 'apk': b64, 'apk_nombre': 'checador.apk'}])
vid = r.get('result')
prueba('se publica la version 1.1.1', isinstance(vid, int), r)
r = odoo('ferba.checador.app', 'create', [{'name': 'repetida', 'version_code': 3, 'apk': b64}])
prueba('no se puede repetir el numero de version', 'error' in r, r)
v = odoo('ferba.checador.app', 'read', [[vid], ['sha256', 'tamano']]).get('result', [{}])[0]
prueba('Odoo calcula la huella y el tamano del APK', v.get('sha256') == SHA and v.get('tamano') == len(crudo), v)

s, r, _ = llama('/ferba/checador/v1/sync', {'equipo': {'actualizacion': 'Descargando 1.1.1'}}, token=T)
a = r.get('app') or {}
prueba('la sincronizacion anuncia la version con su huella', a.get('version_code') == 3 and a.get('sha256') == SHA
       and a.get('version') == '1.1.1' and a.get('tamano') == len(crudo), a)
e = odoo('ferba.checador', 'read', [yo, ['actualizacion']]).get('result', [{}])[0]
prueba('Odoo guarda lo que reporta la tableta sobre su actualizacion', e.get('actualizacion') == 'Descargando 1.1.1', e)

s, d, _ = llama('/ferba/checador/v1/app/3', token=T, get=True)
prueba('la tableta descarga el APK identico', s == 200 and isinstance(d, bytes) and hashlib.sha256(d).hexdigest() == SHA, (s, len(d) if isinstance(d, bytes) else d))
s, d, _ = llama('/ferba/checador/v1/app/3', get=True)
prueba('sin credencial no se descarga', s == 401, s)
s, d, _ = llama('/ferba/checador/v1/app/3', token='inventado', get=True)
prueba('con credencial falsa tampoco', s == 401, s)
s, d, _ = llama('/ferba/checador/v1/app/99', token=T, get=True)
prueba('pedir otra version = no encontrada', s == 404, s)

odoo('ferba.checador.app', 'write', [[vid], {'checador_ids': [[6, 0, otra]]}])
s, r, _ = llama('/ferba/checador/v1/sync', {}, token=T)
prueba('publicada solo para otra tableta: a esta no se le ofrece', r.get('app') is None, r.get('app'))
s, d, _ = llama('/ferba/checador/v1/app/3', token=T, get=True)
prueba('y tampoco puede descargarla', s == 404, s)
odoo('ferba.checador.app', 'write', [[vid], {'checador_ids': [[6, 0, yo]]}])
s, r, _ = llama('/ferba/checador/v1/sync', {}, token=T)
prueba('publicada solo para esta tableta: si se le ofrece', (r.get('app') or {}).get('version_code') == 3, r.get('app'))

odoo('ferba.checador.app', 'write', [[vid], {'activa': False}])
s, r, _ = llama('/ferba/checador/v1/sync', {'equipo': {'actualizacion': ''}}, token=T)
prueba('al despublicarla deja de ofrecerse', r.get('app') is None, r.get('app'))
e = odoo('ferba.checador', 'read', [yo, ['actualizacion']]).get('result', [{}])[0]
prueba('y el estado reportado se limpia', not e.get('actualizacion'), e)

odoo('ferba.checador.app', 'unlink', [[vid]])
print('\n%d OK, %d FALLAS' % (ok, mal))
