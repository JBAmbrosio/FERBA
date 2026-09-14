from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models


class FocoSettings(models.Model):
    _name = 'foco.settings'
    _description = 'Configuracion de Foco'

    name = fields.Char(default="Configuracion", readonly=True)
    sched_enabled = fields.Boolean(
        string="Aplicar horario", default=True,
        help="Si esta activo, los agentes SOLO miden dentro del horario. "
             "Si se apaga, miden todo el tiempo (24/7).")
    sched_from = fields.Float(
        string="Desde", default=9.0,
        help="Hora de inicio en formato 24h (9.0 = 09:00, 9.5 = 09:30).")
    sched_to = fields.Float(
        string="Hasta", default=20.0,
        help="Hora de fin en formato 24h (20.0 = 20:00).")
    day_mon = fields.Boolean("Lunes", default=True)
    day_tue = fields.Boolean("Martes", default=True)
    day_wed = fields.Boolean("Miercoles", default=True)
    day_thu = fields.Boolean("Jueves", default=True)
    day_fri = fields.Boolean("Viernes", default=True)
    day_sat = fields.Boolean("Sabado", default=False)
    day_sun = fields.Boolean("Domingo", default=False)

    installer = fields.Binary(string="Instalador (.exe)",
                              help="El FERBA-Foco-Setup.exe que descargan los empleados desde el correo.")
    installer_name = fields.Char(string="Nombre del instalador", default="FERBA-Foco-Setup.exe")

    @api.model
    def get_settings(self):
        return self.search([], limit=1) or self.create({})

    def schedule_dict(self):
        self.ensure_one()
        flags = [self.day_mon, self.day_tue, self.day_wed, self.day_thu,
                 self.day_fri, self.day_sat, self.day_sun]
        days = [i + 1 for i, on in enumerate(flags) if on]
        return {"enabled": self.sched_enabled, "from": self.sched_from,
                "to": self.sched_to, "days": days}

    @api.model
    def calendar_intervals(self, calendar):
        """Intervalos de trabajo por dia ISO (1=lunes), EXCLUYENDO la comida.

        Ojo: en resource.calendar la comida NO es un hueco, es una LINEA con
        day_period='lunch' (por ejemplo 12.0-13.0). Si no se excluye, se
        contaria como tiempo de trabajo.

        dayofweek de Odoo es '0'=lunes; el agente usa ISO 1=lunes.
        """
        out = {}
        if not calendar or calendar.two_weeks_calendar:
            return out          # calendario quincenal alternante: no soportado
        for att in calendar.attendance_ids:
            if att.day_period == 'lunch':
                continue
            try:
                iso = int(att.dayofweek) + 1
            except (TypeError, ValueError):
                continue
            if att.hour_to > att.hour_from:
                out.setdefault(iso, []).append((att.hour_from, att.hour_to))
        for iso in out:
            out[iso].sort()
        return out

    @api.model
    def lunch_intervals(self, calendar):
        """Franjas de comida por dia ISO. Sirven para PRESUGERIR el motivo."""
        out = {}
        if not calendar or calendar.two_weeks_calendar:
            return out
        for att in calendar.attendance_ids:
            if att.day_period != 'lunch':
                continue
            try:
                iso = int(att.dayofweek) + 1
            except (TypeError, ValueError):
                continue
            out.setdefault(iso, []).append((att.hour_from, att.hour_to))
        return out

    @api.model
    def _tz_for(self, employee):
        if employee:
            return (employee.tz
                    or employee.resource_calendar_id.tz
                    or self.env.company.resource_calendar_id.tz
                    or 'UTC')
        return self.env.company.resource_calendar_id.tz or 'UTC'

    def schedule_for(self, employee=None):
        """Jornada que se le manda al agente de ese equipo.

        Preferencia: calendario del empleado > horario global > apagado.
        """
        self.ensure_one()
        if not self.sched_enabled:
            return {"enabled": False, "intervals": {}, "source": "off", "tz": ""}
        calendar = employee.resource_calendar_id if employee else None
        intervals = self.calendar_intervals(calendar)
        if intervals:
            return {"enabled": True,
                    "tz": calendar.tz or self._tz_for(employee),
                    "source": "employee_calendar",
                    "intervals": {str(k): v for k, v in intervals.items()}}
        data = self.schedule_dict()
        data["source"] = "global"
        data["tz"] = self._tz_for(employee)
        return data

    @api.model
    def expected_seconds(self, employee, start_utc, stop_utc):
        """Segundos de JORNADA ESPERADA dentro de [start_utc, stop_utc].

        Es lo que permite no molestar al empleado por huecos que caen fuera de
        su jornada (o en su comida): ahi el esperado es 0.
        """
        if stop_utc <= start_utc:
            return 0.0
        calendar = employee.resource_calendar_id if employee else None
        intervals = self.calendar_intervals(calendar)
        if not intervals:
            # Sin calendario utilizable no se puede afirmar que estuviera libre:
            # se asume esperado para que alguien lo revise.
            return (stop_utc - start_utc).total_seconds()
        tz = pytz.timezone(self._tz_for(employee))
        ini = pytz.UTC.localize(start_utc).astimezone(tz)
        fin = pytz.UTC.localize(stop_utc).astimezone(tz)
        total = 0.0
        day = ini.date()
        while day <= fin.date():
            base = tz.localize(datetime.combine(day, time(0, 0)))
            for h_from, h_to in intervals.get(day.isoweekday(), []):
                span_a = base + timedelta(hours=h_from)
                span_b = base + timedelta(hours=h_to)
                lo = max(ini, span_a)
                hi = min(fin, span_b)
                if hi > lo:
                    total += (hi - lo).total_seconds()
            day += timedelta(days=1)
        return total
