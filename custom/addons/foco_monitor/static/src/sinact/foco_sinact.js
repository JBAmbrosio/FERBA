/** @odoo-module **/

/**
 * "Sin actividad", explicado. Solo lo medido.
 *
 * El tablero tenia un tramo gris punteado que era una RESTA: jornada menos
 * activo menos huecos de 15 min o mas. Hasta el agente 2026.10.14 las pausas
 * de menos de 15 min se detectaban y se tiraban, asi que ese tiempo no tenia
 * hora y al hacerle clic se abria el detalle de uso, que no dice nada de el.
 * El dueno pidio "una pestana solo para explicar ese tiempo, sin suposiciones
 * ni falsos positivos".
 *
 * Esta pagina dibuja la linea del dia de la persona con lo que el agente
 * MIDIO -cada pausa con su hora, antes de la primera actividad, despues de la
 * ultima- y pone al lado la cuenta del tablero. Lo que no se midio se marca
 * "no medido": nunca se rellena.
 */

import { Component, useState, onWillStart, onMounted, useRef, useExternalListener } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

// Como se llama cada tramo de la linea del dia, en el orden de la leyenda.
const TIPO = {
    activo: "Con actividad",
    idle: "Pausa sin teclado ni mouse",
    locked: "Sesión bloqueada",
    offline: "Equipo apagado o agente detenido",
    hueco: "Hueco de 15 min o más",
    antes: "Antes de su primera actividad",
    despues: "Después de su última actividad",
    sin_datos: "Aún sin datos del equipo",
    no_medido: "No medido",
};
// El tipo en la lista de pausas, donde ya se sabe que es una pausa.
const TIPO_PAUSA = {
    idle: "Sin teclado ni mouse",
    locked: "Sesión bloqueada",
    offline: "Equipo apagado o agente detenido",
};

export class FocoSinAct extends Component {
    static template = "foco_monitor.SinAct";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        const ctx = (this.props.action && this.props.action.context) || {};
        this.state = useState({
            loading: true, dark: false, error: null,
            empleadoId: ctx.foco_employee_id || null,
            fecha: ctx.foco_fecha || this.ymd(new Date()),
            empleados: [], datos: null, filtro: "",
        });
        onWillStart(() => this.load());
        onMounted(() => this.detectTheme());
        useExternalListener(window, "keydown", (ev) => {
            if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
            if (ev.key === "ArrowLeft") this.mover(-1);
            if (ev.key === "ArrowRight") this.mover(1);
        });
    }

    detectTheme() {
        let el = this.root.el && this.root.el.parentElement;
        for (let i = 0; el && i < 12; i++, el = el.parentElement) {
            const m = getComputedStyle(el).backgroundColor.match(/[\d.]+/g);
            if (m && m.length >= 3 && !(m.length === 4 && parseFloat(m[3]) === 0)) {
                this.state.dark = (0.299 * +m[0] + 0.587 * +m[1] + 0.114 * +m[2]) / 255 < 0.5;
                return;
            }
        }
        this.state.dark = false;
    }

    ymd(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    parse(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }

    async load() {
        this.state.loading = true;
        this.state.error = null;
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
            this.state.datos = null;
            this.state.loading = false;
            return;
        }
        const r = await this.orm.call("foco.pausa", "explicar",
            [this.state.empleadoId, this.state.fecha]);
        if (!r || !r.ok) {
            this.state.error = (r && r.error) === "sin_acceso"
                ? "No tienes acceso al equipo de esa persona." : "No se pudo cargar.";
            this.state.datos = null;
        } else {
            this.state.datos = r;
        }
        this.state.loading = false;
    }

    // ---------------------------------------------------------- navegacion
    mover(delta) {
        const d = this.parse(this.state.fecha);
        d.setDate(d.getDate() + delta);
        const v = this.ymd(d);
        if (v > this.ymd(new Date())) return;
        this.state.fecha = v;
        this.load();
    }
    setFecha(ev) {
        const v = ev.target.value;
        if (!v || v > this.ymd(new Date())) return;
        this.state.fecha = v;
        this.load();
    }
    setEmpleado(ev) {
        this.state.empleadoId = +ev.target.value || null;
        this.load();
    }
    setFiltro(k) { this.state.filtro = this.state.filtro === k ? "" : k; }
    volver() { this.action.doAction("foco_monitor.foco_dashboard_action"); }

    get hoy() { return this.ymd(new Date()); }
    get puedeAdelante() { return this.state.fecha < this.hoy; }
    get fechaLabel() {
        const d = this.parse(this.state.fecha);
        return `${DIAS[d.getDay()]} ${d.getDate()} de ${MESES[d.getMonth()]}`;
    }

    // ------------------------------------------------------------ formato
    fmt(h) {
        const m = Math.round((h || 0) * 60);
        if (m <= 0) return "0m";
        return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
    }
    fmtMin(m) {
        const s = Math.round((m || 0) * 60);
        if (s < 60) return `${s} s`;
        const mm = Math.floor(s / 60), ss = s % 60;
        if (mm >= 60) return `${Math.floor(mm / 60)}h ${String(mm % 60).padStart(2, "0")}m`;
        return ss ? `${mm} min ${ss} s` : `${mm} min`;
    }
    hm(t) { return t ? t.slice(0, 5) : "—"; }

    // ------------------------------------------------- la linea del dia
    get ventana() { return (this.state.datos && this.state.datos.ventana) || [8, 18]; }
    pos(h) {
        const [lo, hi] = this.ventana;
        return Math.max(0, Math.min(100, (h - lo) / Math.max(hi - lo, 0.5) * 100)).toFixed(3);
    }
    ancho(a, b) {
        const [lo, hi] = this.ventana;
        return Math.max(0, (Math.min(b, hi) - Math.max(a, lo)) / Math.max(hi - lo, 0.5) * 100).toFixed(3);
    }
    get horas() {
        const [lo, hi] = this.ventana;
        const out = [];
        for (let t = Math.ceil(lo); t <= Math.floor(hi); t++) {
            out.push({ t, label: `${String(t).padStart(2, "0")}:00`, left: this.pos(t) });
        }
        return out;
    }
    get tramos() {
        const d = this.state.datos;
        if (!d) return [];
        return d.tramos.map((t, i) => ({
            ...t, key: `${t.tipo}-${i}`,
            left: this.pos(t.a), width: this.ancho(t.a, t.b),
            titulo: `${TIPO[t.tipo] || t.tipo}${t.estado_label ? " · " + t.estado_label : ""}: `
                + `${t.ini.slice(0, 5)}–${t.fin.slice(0, 5)} (${this.fmtMin(t.min)})`,
            tenue: !!(this.state.filtro && this.state.filtro !== t.tipo),
        }));
    }
    get bandas() {
        const d = this.state.datos;
        if (!d) return [];
        return d.jornada.map(([a, b], i) => ({ key: `j${i}`, left: this.pos(a), width: this.ancho(a, b) }));
    }
    get comidas() {
        const d = this.state.datos;
        if (!d) return [];
        return d.comida.map(([a, b], i) => ({ key: `c${i}`, left: this.pos(a), width: this.ancho(a, b) }));
    }
    get marcas() {
        const d = this.state.datos;
        if (!d) return [];
        const out = [];
        if (d.primera_h !== null && d.primera_h !== undefined) {
            out.push({ k: "primera", left: this.pos(d.primera_h), label: this.hm(d.primera),
                       titulo: `Primera actividad del día: ${d.primera}` });
        }
        if (d.ultima_h !== null && d.ultima_h !== undefined) {
            out.push({ k: "ultima", left: this.pos(d.ultima_h), label: this.hm(d.ultima),
                       titulo: `Última actividad: ${d.ultima}` });
        }
        return out;
    }
    get leyenda() {
        const d = this.state.datos;
        if (!d) return [];
        const hay = new Set(d.tramos.map((t) => t.tipo));
        return Object.keys(TIPO).filter((k) => hay.has(k)).map((k) => ({ k, label: TIPO[k] }));
    }

    // -------------------------------------- de que esta hecho el gris, por caja
    // Probado fuera del equipo / probado en el equipo / sin prueba, con la
    // senal que pone cada minuto ahi. Lo decide Odoo (foco.pausa._dia) con una
    // regla con version; aqui solo se dibuja.
    get grupos() {
        const d = this.state.datos;
        if (!d || !d.lecturas) return [];
        const NOTA = {
            fuera: "una señal medida lo prueba",
            presente: "una señal medida prueba que estaba frente a la pantalla",
            sin_prueba: "nadie tocó el equipo y ninguna señal dice dónde estaba",
        };
        return ["fuera", "presente", "sin_prueba"]
            .map((caja) => ({
                caja, label: d.cajas_label[caja], nota: NOTA[caja], h: d.cajas[caja] || 0,
                items: d.lecturas.filter((l) => l.caja === caja).map((l) => ({
                    ...l,
                    veces: `${l.n} ${l.n === 1 ? "vez" : "veces"}`,
                    rangos: l.rangos.join(", "),
                    detalles: l.detalles.join(" · "),
                })),
            }))
            .filter((g) => g.items.length);
    }
    // La misma particion que el tablero (dashboard_summary): lo probado, acotado
    // al gris, y el resto sin prueba. Asi el numero de la barra es este.
    get resumen() {
        const d = this.state.datos;
        const gris = (d && d.resta.sin_actividad_h) || 0;
        const fuera = Math.min(gris, (d && d.cajas && d.cajas.fuera) || 0);
        const presente = Math.min(gris - fuera, (d && d.cajas && d.cajas.presente) || 0);
        return { fuera, presente, sin: Math.max(0, gris - fuera - presente) };
    }
    get carril() {
        const d = this.state.datos;
        if (!d || !d.carril) return [];
        return d.carril.map((c, i) => ({
            ...c, key: `c${i}`, left: this.pos(c.a), width: this.ancho(c.a, c.b),
        }));
    }
    get cuadre() {
        const d = this.state.datos;
        return d ? d.cuadre : null;
    }
    get cuadraAlMinuto() {
        const c = this.cuadre;
        return c && Math.abs(c.diferencia_h) * 60 < 1;
    }

    // ------------------------------------------------------- cada pausa
    get pausas() {
        const d = this.state.datos;
        if (!d) return [];
        const f = this.state.filtro;
        return d.pausas.filter((p) => !f || p.kind === f)
            .map((p) => ({ ...p, tipo: TIPO_PAUSA[p.kind] || p.kind_label,
                           frente: p.host || p.app || "" }));
    }
    get filtros() {
        const d = this.state.datos;
        if (!d) return [];
        return ["idle", "locked", "offline"].filter((k) => d.n[k])
            .map((k) => ({ k, label: TIPO[k], n: d.n[k], on: this.state.filtro === k }));
    }
}

registry.category("actions").add("foco_sinact", FocoSinAct);
