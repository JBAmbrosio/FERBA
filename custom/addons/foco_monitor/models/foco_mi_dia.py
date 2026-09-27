"""«Mi dia»: lo que Foco midio HOY de una persona, contado PARA ELLA.

Es la otra cara del tablero. El tablero lo mira el administrador; esto lo mira
la persona en su propio equipo (`foco-agent.exe --mi-dia`) y contesta "que
cuenta y que no": sus horas de hoy y de la semana partidas en lo que suma a
favor, lo que no suma y lo que todavia nadie clasifico; los programas y sitios
de hoy con su categoria y su peso; lo que sigue sin clasificar esta semana; las
reglas de ese equipo dichas en palabras; y cuantos periodos tiene por
justificar.

Por que existe (comparacion del 27-sep-2026): ninguno de los productos con los
que se comparo Foco le ensena a la persona lo que mide de ella, y es lo que
mas baja el incentivo de hacer trampa. Quien ve que YouTube no cuenta y que un
sitio sin clasificar tampoco suma, deja de discutir el numero.

Reglas de esta pantalla:
  - Solo lo de la persona que pregunta: el equipo autenticado decide de quien
    es la pregunta. No hay parametro de empleado.
  - Los MISMOS numeros que el tablero: `active_hours`, `productive_hours` y la
    regla "el sitio manda sobre la app" de `_compute_category_id`. No hay un
    calculo aparte que pueda decir otra cosa.
  - Sin veredictos ni hechos de integridad: eso se revisa CON la persona, no
    lo lee sola en una ventana.
  - Los textos ("cuenta 50 %", "todavia no cuenta") salen del peso REAL de la
    categoria. Si el administrador cambia el peso, cambia el texto.
"""

from datetime import datetime, timedelta

import pytz

from odoo import api, models

DIAS_CORTOS = {1: 'lun', 2: 'mar', 3: 'mié', 4: 'jue', 5: 'vie', 6: 'sáb', 7: 'dom'}
DIAS_LARGOS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre']

# Medio minuto: lo que redondea a "0 min" y por tanto no se puede mostrar.
MINIMO_H = 0.0083
# Renglones por lista. Lo que no cabe se dice como "y N mas", nunca se pierde.
TOPE_LISTA = 10
TOPE_PENDIENTES = 8


def _hora(h):
    """14.5 -> '14:30'. Las reglas guardan la hora como flotante."""
    try:
        h = float(h or 0.0)
    except (TypeError, ValueError):
        h = 0.0
    horas = int(h)
    minutos = int(round((h - horas) * 60))
    if minutos == 60:
        horas, minutos = horas + 1, 0
    return '%02d:%02d' % (horas, minutos)


def _dias_texto(dias):
    """[1..5] -> 'de lunes a viernes'; todos -> ''; otros -> 'los lun, mié'."""
    limpios = []
    for d in dias or []:
        try:
            d = int(d)
        except (TypeError, ValueError):
            continue
        if 1 <= d <= 7 and d not in limpios:
            limpios.append(d)
    limpios.sort()
    if not limpios or len(limpios) == 7:
        return ''
    if limpios == [1, 2, 3, 4, 5]:
        return 'de lunes a viernes'
    if limpios == [6, 7]:
        return 'sábados y domingos'
    return 'los ' + ', '.join(DIAS_CORTOS[d] for d in limpios)


def frase_regla(r):
    """Una regla con horario o cuota, dicha en palabras para la persona.

    'youtube.com: permitido de lunes a viernes de 14:00 a 15:00'
    'youtube.com: hasta 30 min al día (hoy llevas 12 min)'
    'facebook.com: se bloquea al pasar 30 min al día (hoy llevas 41 min)'
    """
    patron = r.get('pattern') or ''
    permitir = r.get('action') == 'allow'
    cuando = _dias_texto(r.get('days'))
    if r.get('from') or r.get('to'):
        cuando = (cuando + ' ' if cuando else '') + 'de %s a %s' % (
            _hora(r.get('from')), _hora(r.get('to')))
    cuota = int(r.get('quota_min') or 0)
    if cuota:
        texto = ('hasta %d min al día' if permitir
                 else 'se bloquea al pasar %d min al día') % cuota
        if cuando:
            texto += ' ' + cuando
        usado = r.get('used_min')
        if usado is not None:
            texto += ' (hoy llevas %d min)' % int(usado)
    else:
        texto = ('permitido' if permitir else 'bloqueado') + (' ' + cuando if cuando else '')
    return '%s: %s' % (patron, texto)


def fecha_larga(d):
    return '%s %d de %s de %d' % (DIAS_LARGOS[d.weekday()], d.day, MESES[d.month - 1], d.year)


def semana_texto(desde, hasta):
    if desde.month == hasta.month:
        return 'del %d al %d de %s' % (desde.day, hasta.day, MESES[hasta.month - 1])
    return 'del %d de %s al %d de %s' % (desde.day, MESES[desde.month - 1],
                                         hasta.day, MESES[hasta.month - 1])


class FocoUsageMiDia(models.Model):
    _inherit = 'foco.usage'

    @api.model
    def _cuenta(self, cat):
        """Como pesa una categoria, dicho para la persona. Sale del peso real."""
        if not cat:
            return {'categoria': 'Sin clasificar', 'peso': None,
                    'cuenta': 'todavía no cuenta', 'clave': 'sin_clasificar'}
        peso = cat.weight or 0.0
        if cat.is_system:
            return {'categoria': cat.name, 'peso': 0.0, 'cuenta': 'no se mide', 'clave': 'sistema'}
        if peso >= 1.0:
            return {'categoria': cat.name, 'peso': peso, 'cuenta': 'cuenta 100 %', 'clave': 'a_favor'}
        if peso > 0.0:
            return {'categoria': cat.name, 'peso': peso,
                    'cuenta': 'cuenta %d %%' % int(round(peso * 100)), 'clave': 'parcial'}
        return {'categoria': cat.name, 'peso': 0.0, 'cuenta': 'no cuenta', 'clave': 'no_cuenta'}

    @api.model
    def _totales_persona(self, dominio):
        """Las mismas cubetas que `analitica`, mas lo que a la persona le sirve
        leer: sin input, fuera de turno y en llamada. El sistema no cuenta en
        ninguna (active_hours ya es 0 ahi; el sin input se salta aqui)."""
        t = {'activo': 0.0, 'productivo': 0.0, 'distraccion': 0.0, 'sin_clasificar': 0.0,
             'sin_input': 0.0, 'fuera_turno': 0.0, 'llamada': 0.0}
        for cat, shift, activo, productivo, sin_input, llamada in self._read_group(
                dominio, ['category_id', 'shift'],
                ['active_hours:sum', 'productive_hours:sum', 'fg_idle:sum', 'call_hours:sum']):
            if cat and cat.is_system:
                continue
            activo = activo or 0.0
            t['activo'] += activo
            t['productivo'] += productivo or 0.0
            t['sin_input'] += sin_input or 0.0
            t['llamada'] += llamada or 0.0
            if shift == 'off':
                t['fuera_turno'] += activo
            if not cat:
                t['sin_clasificar'] += activo
            elif (cat.weight or 0.0) <= 0.0:
                t['distraccion'] += activo
        # Lo activo que no es ni a favor, ni distraccion, ni sin clasificar: la
        # parte que NO suma de las categorias parciales (navegador al 50 %).
        t['otro'] = max(t['activo'] - t['productivo'] - t['distraccion'] - t['sin_clasificar'], 0.0)
        t['indice'] = round(100.0 * t['productivo'] / t['activo'], 1) if t['activo'] else None
        for k in list(t):
            if k != 'indice':
                t[k] = round(t[k], 3)
        return t

    @api.model
    def mi_dia(self, computer):
        """Todo lo que ensena la ventana, en UNA respuesta. Siempre un dict."""
        emp = computer.employee_id
        if not emp:
            return {'ok': False, 'error': 'sin_empleado',
                    'mensaje': 'Este equipo no está ligado a ningún empleado.'}
        zona = self.env['foco.settings'].sudo()._tzinfo_for(emp, computer) or pytz.UTC
        ahora = datetime.now(zona)
        hoy = ahora.date()
        lunes = hoy - timedelta(days=hoy.weekday())
        base = [('employee_id', '=', emp.id)]
        dom_hoy = base + [('date', '=', hoy)]
        dom_sem = base + [('date', '>=', lunes), ('date', '<=', hoy)]

        hoy_t = self._totales_persona(dom_hoy)
        sem_t = self._totales_persona(dom_sem)
        sem_t['dias'] = len(self._read_group(dom_sem, ['date:day'], ['__count']))
        sem_t['desde'] = lunes.isoformat()
        sem_t['hasta'] = hoy.isoformat()
        sem_t['texto'] = semana_texto(lunes, hoy)

        # --- programas y sitios de HOY, con su categoria y su peso -----------
        apps, sitios = {}, {}
        for app, site, host, activo, sin_input in self._read_group(
                dom_hoy, ['app_id', 'site_id', 'host'], ['fg_active:sum', 'fg_idle:sum']):
            if not app or (app.category_id and app.category_id.is_system):
                continue
            activo = activo or 0.0
            a = apps.get(app.id)
            if a is None:
                a = apps[app.id] = dict(self._cuenta(app.category_id),
                                        nombre=app.display_name, horas=0.0, sin_input=0.0)
            a['horas'] += activo
            a['sin_input'] += sin_input or 0.0
            host = (host or '').strip()
            if host:
                # La MISMA regla con la que se calcula el indice: el sitio
                # manda si esta clasificado; si no, hereda del navegador.
                propia = bool(site and site.category_id)
                cat = site.category_id if propia else app.category_id
                s = sitios.get(host)
                if s is None:
                    s = sitios[host] = dict(self._cuenta(cat), nombre=host, horas=0.0,
                                            heredada=not propia)
                s['horas'] += activo

        def cerrar(d, tope=TOPE_LISTA):
            lista = sorted(d.values(), key=lambda x: -x['horas'])
            visibles = [x for x in lista if x['horas'] >= MINIMO_H][:tope]
            resto = lista[len(visibles):]
            for x in visibles:
                x['horas'] = round(x['horas'], 3)
                if 'sin_input' in x:
                    x['sin_input'] = round(x['sin_input'], 3)
            cola = ({'n': len(resto), 'horas': round(sum(x['horas'] for x in resto), 3)}
                    if resto else None)
            return visibles, cola

        programas, programas_resto = cerrar(apps)
        sitios_v, sitios_resto = cerrar(sitios)

        # --- lo que sigue sin clasificar ESTA SEMANA -------------------------
        # Programas sin categoria y sitios sin la suya (aunque hereden la del
        # navegador): es lo que el administrador todavia no decidio.
        pend = {}
        for app, site, activo in self._read_group(
                dom_sem + [('fg_active', '>', 0)], ['app_id', 'site_id'], ['fg_active:sum']):
            activo = activo or 0.0
            if app and not app.category_id:
                p = pend.setdefault(('programa', app.id),
                                    {'tipo': 'programa', 'nombre': app.display_name, 'horas': 0.0})
                p['horas'] += activo
            if site and not site.category_id:
                p = pend.setdefault(('sitio', site.id),
                                    {'tipo': 'sitio', 'nombre': site.host, 'horas': 0.0})
                p['horas'] += activo
        sin_clasificar, sin_clasificar_resto = cerrar(pend, TOPE_PENDIENTES)

        # --- periodos por justificar -----------------------------------------
        pendientes = self.env['foco.absence'].sudo().pending_for_employee(emp)
        n_pend = len(pendientes)
        # La liga solo se renueva si hace falta: es un token con vencimiento.
        url = emp.sudo().foco_justify_url() if n_pend else ''

        # --- reglas de ESTE equipo, en palabras ------------------------------
        Policy = self.env['foco.policy'].sudo()
        pago = Policy.payload_for(computer)
        salvavidas = set(Policy._salvavidas())
        reglas = {'perfil': pago.get('policy') or '',
                  'activo': pago.get('version') not in ('off', 'vacio'),
                  'bloqueados': [], 'permitidos': [], 'condicionadas': [],
                  'navegadores_cerrados': list(pago.get('kill_productos') or [])}
        for r in pago.get('rules') or []:
            if r.get('pattern') in salvavidas:
                continue
            condicionada = (bool(r.get('quota_min')) or bool(r.get('from') or r.get('to'))
                            or len(r.get('days') or []) < 7)
            if condicionada:
                reglas['condicionadas'].append(frase_regla(r))
            elif r.get('action') == 'allow':
                reglas['permitidos'].append(r['pattern'])
            else:
                reglas['bloqueados'].append(r['pattern'])

        # --- la jornada de hoy y el estado del agente ------------------------
        def local(dt):
            if not dt:
                return ''
            return pytz.UTC.localize(dt).astimezone(zona).strftime('%H:%M')

        jornada = self.env['foco.workday'].sudo().search(
            [('employee_id', '=', emp.id), ('date', '=', hoy)], limit=1)
        jor = ({'primera': local(jornada.first_signal), 'ultima': local(jornada.last_signal),
                'esperadas': round(jornada.expected_hours or 0.0, 2)}
               if jornada else {})

        return {
            'ok': True,
            'empleado': emp.name,
            'fecha': hoy.isoformat(),
            'fecha_texto': fecha_larga(hoy),
            'hora': ahora.strftime('%H:%M'),
            'hoy': hoy_t,
            'semana': sem_t,
            'programas': programas, 'programas_resto': programas_resto,
            'sitios': sitios_v, 'sitios_resto': sitios_resto,
            'sin_clasificar': sin_clasificar, 'sin_clasificar_resto': sin_clasificar_resto,
            'ausencias_pendientes': n_pend,
            'url_justificar': url,
            'reglas': reglas,
            'jornada': jor,
            'agente': {'version': computer.agent_version or '',
                       'ultimo_envio': local(computer.last_seen)},
        }
