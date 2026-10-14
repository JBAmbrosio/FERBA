/** @odoo-module **/

/**
 * Tablero del perfil de RECURSOS HUMANOS (reunion del 14-oct-2026).
 *
 * Francesco quiere delegar una parte de la revision de Foco: la asistencia,
 * los periodos sin actividad y las justificaciones, "para que no vea mas
 * informacion". Esta pantalla es eso y nada mas: las dos tarjetas del tablero
 * -Asistencia del dia y Requiere atencion- con lo que les toca.
 *
 * Todo sale de UN metodo del servidor (foco.absence.tablero_rrhh), que tambien
 * arma la accion de cada renglon. Asi el tablero no puede llevar a una
 * pantalla que este perfil no deba ver: la puerta la decide el servidor.
 */

import { Component, useState, onWillStart, onMounted, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

export class FocoRrhh extends Component {
    static template = "foco_monitor.Rrhh";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        this.state = useState({
            loading: true, dark: false, error: null,
            fecha: this.ymd(new Date()), datos: null,
        });
        onWillStart(() => this.load());
        onMounted(() => this.detectTheme());
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
        try {
            const r = await this.orm.call("foco.absence", "tablero_rrhh", [this.state.fecha]);
            this.state.datos = r;
            this.state.fecha = r.fecha;
        } catch (e) {
            this.state.datos = null;
            this.state.error = (e && e.data && e.data.message) || "No se pudo cargar el tablero.";
        }
        this.state.loading = false;
    }

    // ---------------------------------------------------------- navegacion
    get hoy() { return this.ymd(new Date()); }
    get esHoy() { return this.state.fecha === this.hoy; }
    get puedeAdelante() { return this.state.fecha < this.hoy; }
    get fechaLabel() {
        const d = this.parse(this.state.fecha);
        return `${this.esHoy ? "Hoy, " : ""}${DIAS[d.getDay()]} ${d.getDate()} de ${MESES[d.getMonth()]}`;
    }
    mover(delta) {
        const d = this.parse(this.state.fecha);
        d.setDate(d.getDate() + delta);
        const v = this.ymd(d);
        if (v > this.hoy) return;
        this.state.fecha = v;
        this.load();
    }
    setFecha(ev) {
        const v = ev.target.value;
        if (!v || v > this.hoy) return;
        this.state.fecha = v;
        this.load();
    }
    verHoy() {
        if (this.esHoy) return;
        this.state.fecha = this.hoy;
        this.load();
    }

    // --------------------------------------------------------------- datos
    get asistencia() {
        const d = this.state.datos;
        if (!d) return [];
        return d.asistencia.map((p) => ({ ...p, ...this.avatar(p.name) }));
    }
    get atencion() { return (this.state.datos && this.state.datos.atencion) || []; }
    get equipo() { return (this.state.datos && this.state.datos.equipo) || {}; }

    /** Iniciales y tono estables por nombre (el mismo que en el tablero). */
    avatar(nombre) {
        const partes = (nombre || "").trim().split(/\s+/).filter(Boolean);
        const initials = ((partes[0] || "?")[0] + ((partes[1] || "")[0] || "")).toUpperCase();
        let h = 0;
        for (const ch of nombre || "") h = (h * 31 + ch.charCodeAt(0)) % 360;
        return { initials, hue: h };
    }

    abrir(accion) {
        if (accion) this.action.doAction(accion);
    }
    tecla(ev, accion) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.abrir(accion);
        }
    }
}

registry.category("actions").add("foco_rrhh", FocoRrhh);
