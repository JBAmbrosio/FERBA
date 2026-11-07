# -*- coding: utf-8 -*-
"""En que estado de Mexico cae un punto.

Los poligonos son los de Natural Earth 1:10m admin-1 (DOMINIO PUBLICO),
simplificados a ~300 m (data/mx_estados.json). El MISMO archivo viaja dentro del
APK para pintar las zonas en el mapa del telefono, asi que la zona que ve el
vendedor y la que guarda Odoo salen de la misma geometria.

No es un modelo: solo funciones, cargadas una vez por proceso.
"""
import json
import math
import os
from functools import lru_cache

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'data', 'mx_estados.json')

# Un pin puede caer FUERA de todo poligono sin estar mal: en la costa (la
# geometria simplificada recorta esteros y bahias) o en el mar a unos km cuando
# el enlace de Google Maps apuntaba a la costa. Medido el 14-oct-2026 con los 179
# clientes con pin: 6 caen fuera, el mas lejano a 20.7 km, y en los 6 el estado
# mas cercano es el de su municipio. Mas alla de este radio no se adivina.
TOL_COSTA_KM = 50.0


@lru_cache(maxsize=1)
def _estados():
    with open(RUTA, encoding='utf-8') as fh:
        fc = json.load(fh)
    out = []
    for f in fc['features']:
        polys = f['geometry']['coordinates']
        xs = [x for p in polys for x, _y in p[0]]
        ys = [y for p in polys for _x, y in p[0]]
        out.append((f['properties']['code'], f['properties']['name'], polys,
                    (min(xs), min(ys), max(xs), max(ys))))
    return tuple(out)


def _dentro(x, y, anillo):
    c = False
    j = len(anillo) - 1
    for i in range(len(anillo)):
        xi, yi = anillo[i]
        xj, yj = anillo[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def _km_a_anillo(lat, lon, anillo):
    """Distancia aproximada (km) del punto al borde del anillo."""
    kx = 111.32 * math.cos(math.radians(lat))
    ky = 110.57
    mejor = float('inf')
    for i in range(len(anillo) - 1):
        ax, ay = (anillo[i][0] - lon) * kx, (anillo[i][1] - lat) * ky
        bx, by = (anillo[i + 1][0] - lon) * kx, (anillo[i + 1][1] - lat) * ky
        dx, dy = bx - ax, by - ay
        n2 = dx * dx + dy * dy
        t = 0.0 if n2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / n2))
        px, py = ax + t * dx, ay + t * dy
        d = px * px + py * py
        if d < mejor:
            mejor = d
    return math.sqrt(mejor)


def estado_de(lat, lon):
    """Codigo del estado (el de Odoo: 'SON', 'BCN', 'CMX'...) donde cae el punto.

    Devuelve (codigo, km): km = 0 si cae dentro; si cae fuera de todo poligono,
    el estado mas cercano y a cuantos km esta. (None, None) si no hay punto o si
    el estado mas cercano queda a mas de TOL_COSTA_KM (fuera de Mexico)."""
    if not (lat or lon):
        return None, None
    for code, _name, polys, (x0, y0, x1, y1) in _estados():
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        for poly in polys:
            if _dentro(lon, lat, poly[0]) and not any(_dentro(lon, lat, h) for h in poly[1:]):
                return code, 0.0
    mejor = (None, float('inf'))
    for code, _name, polys, (x0, y0, x1, y1) in _estados():
        # descarte barato: la caja del estado ya esta mas lejos que el mejor
        margen = mejor[1] / 100.0 + 0.5
        if lon < x0 - margen or lon > x1 + margen or lat < y0 - margen or lat > y1 + margen:
            continue
        for poly in polys:
            d = _km_a_anillo(lat, lon, poly[0])
            if d < mejor[1]:
                mejor = (code, d)
    if mejor[0] and mejor[1] <= TOL_COSTA_KM:
        return mejor[0], round(mejor[1], 1)
    return None, None
