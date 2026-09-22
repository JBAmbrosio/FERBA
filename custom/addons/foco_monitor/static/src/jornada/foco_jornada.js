/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, useRef, useExternalListener } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];
const MESES = ["ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic"];

const SEG = {
    activo: "Actividad",
    llamada: "En llamada",
    sin_input: "Sin teclado ni mouse",
    bloqueado: "Sesión bloqueada",
    apagado: "Equipo apagado",
    suspendido: "Equipo suspendido",
    sin_explicar: "Sin explicación",
};

const PIN = {
    encendido: "Encendido",
    apagado: "Apagado",
    apagado_inesperado: "Apagado inesperado",
    apagado_solicitado: "Apagado solicitado",
    suspendido: "Suspensión",
    reanudado: "Reanudación",
    agente_inicio: "Inició sesión",
    agente_fin: "Cerró sesión",
    bloqueo: "Bloqueo",
    desbloqueo: "Desbloqueo",
};

// Marcas que valen una línea completa: el equipo cambió de estado de verdad.
const PIN_FUERTE = ["encendido", "apagado", "apagado_inesperado", "apagado_solicitado"];

export class FocoJornada extends Component {
    static template = "foco_monitor.Jornada";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        this.state = useState({
            loading: true, dark: false,
            vista: "semana",            // semana | mes
            empleados: [], empleadoId: null,
            filas: [], ventana: [7, 20],
            ancla: this.ymd(new Date()),
            detalle: null,
            uso: null,                  // el desglose del dia abierto en la ficha
        });
        onWillStart(() => this.load());
        onMounted(() => this.detectTheme());
        useExternalListener(window, "keydown", (ev) => {
            if (ev.key === "Escape" && this.state.detalle) this.state.detalle = null;
            if (this.state.detalle) return;
            if (ev.key === "ArrowLeft") this.mover(-1);
            if (ev.key === "ArrowRight") this.mover(1);
        });
    }

    detectTheme() {
        let el = this.root.el && this.root.el.parentElement;
        for (let i = 0; el && i < 12; i++, el = el.parentElement) {
            const m = getComputedStyle(el).backgroundColor.match(/[\d.]+/g);
            if (m && m.length >= 3 && !(m.length === 4 && parseFloat(m[3]) === 0)) {
                const lum = (0.299 * +m[0] + 0.587 * +m[1] + 0.114 * +m[2]) / 255;
                this.state.dark = lum < 0.5;
                return;
            }
        }
        this.state.dark = false;
    }

    ymd(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    parse(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }

    /** Lunes de la semana que contiene esa fecha. */
    lunes(d) {
        const x = new Date(d);
        x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
        return x;
    }

    get rango() {
        const a = this.parse(this.state.ancla);
        if (this.state.vista === "mes") {
            const ini = new Date(a.getFullYear(), a.getMonth(), 1);
            const fin = new Date(a.getFullYear(), a.getMonth() + 1, 0);
            // Se completan las semanas para que el mes sean filas de 7.
            return [this.ymd(this.lunes(ini)), this.ymd(this.domingo(fin))];
        }
        const ini = this.lunes(a);
        const fin = new Date(ini); fin.setDate(ini.getDate() + 6);
        return [this.ymd(ini), this.ymd(fin)];
    }
    domingo(d) { const x = new Date(d); x.setDate(x.getDate() + (7 - (x.getDay() || 7))); return x; }

    async load() {
        this.state.loading = true;
        if (!this.state.empleados.length) {
            const pcs = await this.orm.searchRead(
                "foco.computer", [["employee_id", "!=", false]], ["employee_id"]);
            const vistos = {};
            for (const p of pcs) vistos[p.employee_id[0]] = p.employee_id[1];
            this.state.empleados = Object.entries(vistos)
                .map(([id, name]) => ({ id: +id, name }))
                .sort((a, b) => a.name.localeCompare(b.name));
            if (!this.state.empleadoId && this.state.empleados.length) {
                this.state.empleadoId = this.state.empleados[0].id;
            }
        }
        if (!this.state.empleadoId) {
            this.state.filas = [];
            this.state.loading = false;
            return;
        }
        const [desde, hasta] = this.rango;
        const res = await this.orm.call("foco.workday", "cintas",
            [[this.state.empleadoId], desde, hasta]);
        this.state.ventana = res.ventana;
        this.state.filas = (res.dias || []).map((f) => this.decorar(f));
        this.state.loading = false;
    }

    /** El tiempo pasa a ser VERTICAL: cada dia es una columna y la hora es la
     *  altura. Asi las horas de entrada quedan en una linea que se lee de un
     *  lado a otro, en vez de ser bordes izquierdos que hay que comparar de
     *  renglon en renglon. */
    decorar(f) {
        const [lo, hi] = this.state.ventana;
        const span = Math.max(0.001, hi - lo);
        const y = (h) => ((Math.min(hi, Math.max(lo, h)) - lo) / span) * 100;
        const d = this.parse(f.date);
        const hoy = this.ymd(new Date());
        return {
            ...f,
            dow_label: DIAS[(d.getDay() + 6) % 7],
            num: d.getDate(),
            mes_label: MESES[d.getMonth()],
            es_hoy: f.date === hoy,
            futuro: f.date > hoy,
            franjas: (f.shift || []).map((s) => ({ top: y(s[0]), alto: y(s[1]) - y(s[0]) })),
            tramos: (f.segments || []).map((s) => ({
                ...s,
                top: y(s.a), alto: Math.max(0.5, y(s.b) - y(s.a)),
                titulo: `${this.hhmm(s.a)} – ${this.hhmm(s.b)} · ${SEG[s.k] || s.k}` +
                    ` · ${this.dur(s.b - s.a)}` + (s.motivo ? ` · ${s.motivo}` : "") +
                    (s.estado === "pendiente" ? " · por justificar" : ""),
            })),
            marcas: (f.pins || []).map((p) => ({
                ...p, top: y(p.h), fuerte: PIN_FUERTE.includes(p.k), titulo: p.texto,
            })),
            inicio_txt: f.first === null ? "" : this.hhmm(f.first),
            fin_txt: f.last === null ? "" : this.hhmm(f.last),
            activo_txt: f.active > 0.008 ? this.dur(f.active) : "",
            fuera_txt: f.off_shift > 0.008 ? this.dur(f.off_shift) : "",
            sin_explicar: f.unexplained || 0,
        };
    }

    // --- la escala vertical ------------------------------------------------
    get horas() {
        const [lo, hi] = this.state.ventana;
        const span = Math.max(0.001, hi - lo);
        const paso = span > 13 ? 2 : 1;
        const out = [];
        for (let h = Math.ceil(lo); h <= Math.floor(hi); h++) {
            if (h % paso) continue;
            out.push({ h, top: ((h - lo) / span) * 100, label: String(h).padStart(2, "0") });
        }
        return out;
    }

    /** La mediana, no el promedio.
     *
     *  Un dia que empezo a las 11 mueve el promedio media hora y deja una
     *  "entrada tipica" que no ocurrio nunca. La mediana describe el dia
     *  normal, que es lo que la palabra promete. */
    mediana(xs) {
        if (!xs.length) return null;
        const s = [...xs].sort((a, b) => a - b);
        const m = Math.floor(s.length / 2);
        return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
    }

    get resumen() {
        const f = this.state.filas.filter((x) => !x.futuro);
        const con = f.filter((x) => x.state === "completo");
        const sum = (k) => f.reduce((a, x) => a + (x[k] || 0), 0);
        const ent = this.mediana(con.filter((x) => x.first !== null).map((x) => x.first));
        const sal = this.mediana(con.filter((x) => x.last !== null).map((x) => x.last));
        const [lo, hi] = this.state.ventana;
        const span = Math.max(0.001, hi - lo);
        const y = (h) => ((Math.min(hi, Math.max(lo, h)) - lo) / span) * 100;
        return {
            dias: f.length, medidos: con.length,
            entrada: ent === null ? "—" : this.hhmm(ent),
            salida: sal === null ? "—" : this.hhmm(sal),
            entrada_y: ent === null ? null : y(ent),
            salida_y: sal === null ? null : y(sal),
            activo_txt: this.dur(sum("active")),
            fuera: sum("off_shift"), fuera_txt: this.dur(sum("off_shift")),
            llamada: sum("call"), llamada_txt: this.dur(sum("call")),
            sin_explicar: f.reduce((a, x) => a + (x.unexplained || 0), 0),
            sin_detalle: f.filter((x) => x.state === "sin_detalle").length,
            solo_equipo: f.filter((x) => x.state === "solo_equipo").length,
        };
    }

    get empleadoNombre() {
        const e = this.state.empleados.find((x) => x.id === this.state.empleadoId);
        return e ? e.name : "";
    }

    get periodoLabel() {
        const [a, b] = this.rango;
        const da = this.parse(a), db = this.parse(b);
        if (this.state.vista === "mes") {
            const m = this.parse(this.state.ancla);
            return `${MESES[m.getMonth()]} ${m.getFullYear()}`;
        }
        const mismoMes = da.getMonth() === db.getMonth();
        return mismoMes
            ? `${da.getDate()}–${db.getDate()} ${MESES[da.getMonth()]}`
            : `${da.getDate()} ${MESES[da.getMonth()]} – ${db.getDate()} ${MESES[db.getMonth()]}`;
    }

    /** El mes, en semanas: pequeños múltiplos de la misma columna. */
    get semanas() {
        const out = [];
        let fila = [];
        for (const f of this.state.filas) {
            fila.push(f);
            if (fila.length === 7) { out.push(fila); fila = []; }
        }
        if (fila.length) out.push(fila);
        return out;
    }

    setVista(v) { if (v === this.state.vista) return; this.state.vista = v; this.load(); }
    setEmpleado(ev) { this.state.empleadoId = +ev.target.value; this.load(); }

    mover(paso) {
        const d = this.parse(this.state.ancla);
        if (this.state.vista === "mes") d.setMonth(d.getMonth() + paso, 1);
        else d.setDate(d.getDate() + paso * 7);
        this.state.ancla = this.ymd(d);
        this.load();
    }
    hoy() { this.state.ancla = this.ymd(new Date()); this.load(); }

    /** Abre la ficha y trae EN QUE se fue ese dia.
     *
     *  El uso se pide aqui y no en `load()` a proposito: son siete dias, y
     *  traer el desglose de los siete para que se mire uno seria pagar seis de
     *  mas en cada cambio de semana.
     *
     *  Se comprueba que la ficha siga siendo la misma antes de escribir: si
     *  alguien cierra o abre otro dia mientras la consulta viaja, la respuesta
     *  vieja llegaria despues y pintaria el dia equivocado.
     */
    async abrir(f) {
        if (!f || f.state === "sin_dato") return;
        this.state.detalle = f;
        this.state.uso = null;
        let r;
        try {
            r = await this.orm.call("foco.usage", "desglose",
                [[this.state.empleadoId], f.date, f.date]);
        } catch {
            r = { filas: [] };
        }
        if (this.state.detalle === f) this.state.uso = r;
    }
    cerrar() { this.state.detalle = null; this.state.uso = null; }

    /** El detalle completo, ya posado en esta persona y ese dia. */
    abrirUso() {
        this.action.doAction("foco_monitor.foco_uso_client", {
            additionalContext: {
                foco_employee_id: this.state.empleadoId,
                foco_desde: this.state.detalle ? this.state.detalle.date : null,
                foco_hasta: this.state.detalle ? this.state.detalle.date : null,
            },
        });
    }

    abrirAusencias() {
        this.action.doAction("foco_monitor.foco_absence_action", {
            additionalContext: { search_default_pendientes: 1 },
        });
    }

    hhmm(h) {
        if (h === null || h === undefined) return "—";
        const m = Math.round(h * 60);
        return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    }
    dur(h) {
        const m = Math.round((h || 0) * 60);
        if (!m) return "0m";
        return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
    }
    segLabel(k) { return SEG[k] || k; }
    pinLabel(k) { return PIN[k] || k; }
}

registry.category("actions").add("foco_jornada", FocoJornada);
