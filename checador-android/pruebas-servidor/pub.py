"""pub.py borrar | pub.py <nombre> <version_code> <apk> | pub.py estado"""
import base64, json, sys, urllib.request

B = 'http://127.0.0.1:18069'


def llama(ruta, cuerpo, cookie=None):
    h = {'Content-Type': 'application/json'}
    if cookie:
        h['Cookie'] = cookie
    with urllib.request.urlopen(urllib.request.Request(B + ruta, json.dumps(cuerpo).encode(), h), timeout=120) as f:
        return json.loads(f.read()), f.headers.get('Set-Cookie', '')


_, ck = llama('/web/session/authenticate', {'jsonrpc': '2.0', 'params': {'db': 'checador2', 'login': 'admin', 'password': 'admin'}})
cookie = ck.split(';')[0]


def odoo(modelo, metodo, args, kw=None):
    return llama('/web/dataset/call_kw', {'jsonrpc': '2.0', 'params': {
        'model': modelo, 'method': metodo, 'args': args, 'kwargs': kw or {}}}, cookie)[0]


if sys.argv[1] == 'estado':
    for c in odoo('ferba.checador', 'search_read', [[['name', '=', 'Tableta prueba']]],
                  {'fields': ['version_app', 'actualizacion', 'ultima_senal']})['result']:
        print('TABLETA version=%s | actualizacion=%s | senal=%s' % (c['version_app'], c['actualizacion'], c['ultima_senal']))
    for v in odoo('ferba.checador.app', 'search_read', [[]], {'fields': ['name', 'version_code', 'activa', 'instaladas']})['result']:
        print('VERSION', v['name'], 'codigo', v['version_code'], 'publicada=%s' % v['activa'], 'instaladas=%s' % v['instaladas'])
    sys.exit()
ids = odoo('ferba.checador.app', 'search', [[]])['result']
if ids:
    odoo('ferba.checador.app', 'unlink', [ids])
if sys.argv[1] != 'borrar':
    r = odoo('ferba.checador.app', 'create', [{'name': sys.argv[1], 'version_code': int(sys.argv[2]),
                                                'apk': base64.b64encode(open(sys.argv[3], 'rb').read()).decode()}])
    print('PUBLICADA', sys.argv[1], sys.argv[2], r.get('result') or r.get('error', {}).get('data', {}).get('message'))
ch = odoo('ferba.checador', 'search', [[['name', '=', 'Tableta prueba']]])['result']
odoo('ferba.checador', 'write', [ch, {'pin_mantenimiento': '9999'}])
