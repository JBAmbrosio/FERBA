/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Editor de horarios laborales POR EMPLEADO. Escribe el horario NATIVO de Odoo
// (resource.calendar) de cada persona, que es de donde ya salen todas las
// metricas; aqui no hay una segunda verdad. Mismo lenguaje visual que el tablero.

const FUENTE_TXT = {
    checador: "Hoy manda el checador: en los días que marca, la hora real gana sobre este horario.",
    calendario: "Hoy manda este horario: la persona no usa el checador.",
    ninguno: "Sin horario ni checador: no hay jornada contra qué medir.",
};

export class FocoHorarios extends Component {
    static template = "foco_monitor.Horarios";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.root = useRef("root");
        this.state = useState({
            loading: true, dark: false, saving: false, savedAt: 0,
            empleados: [], q: "", tope: 40, plantillas: [],
            sel: null, emp: null, dirty: false, error: "",
        });
        onWillStart(() => this.cargar());
        onMounted(() => {
            this.detectTheme();
            this.observador = new MutationObserver(() => this.detectTheme());
            this.observador.observe(document.body,
                { attributes: true, attributeFilter: ["class", "style", "data-color-scheme"] });
        });
        onWillUnmount(() => this.observador && this.observador.disconnect());
    }

    // ----------------------------------------------------------- datos
    async cargar() {
        const r = await this.orm.call("foco.settings", "horarios_tablero", []);
        this.state.empleados = r.empleados || [];
        this.state.tope = r.tope_semana || 40;
        this.state.plantillas = r.plantillas || [];
        this.state.loading = false;
        if (!this.state.sel && this.state.empleados.length) {
            await this.seleccionar(this.state.empleados[0].id);
        }
    }

    async seleccionar(id) {
        if (this.state.dirty && !window.confirm("Hay cambios sin guardar. ¿Descartarlos?")) {
            return;
        }
        this.state.sel = id;
        this.state.error = "";
        this.state.dirty = false;
        this.state.emp = null;
        this.state.emp = await this.orm.call("foco.settings", "horario_empleado", [id]);
    }

    teclaFila(ev, id) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.seleccionar(id); }
    }

    get filtrados() {
        const q = (this.state.q || "").trim().toLowerCase();
        if (!q) return this.state.empleados;
        return this.state.empleados.filter(
            (e) => e.name.toLowerCase().includes(q) || (e.departamento || "").toLowerCase().includes(q));
    }

    // ----------------------------------------------------------- edicion
    marcar() { this.state.dirty = true; this.state.error = ""; this.state.savedAt = 0; }

    toggleDia(dia) {
        dia.trabaja = !dia.trabaja;
        if (dia.trabaja && dia.entrada === null) {
            dia.entrada = 9.0; dia.salida = 18.5;
            dia.comida_inicio = 13.0; dia.comida_fin = 14.0;
        }
        this.marcar();
    }

    setHora(dia, campo, valor) {
        dia[campo] = this.hhmm2f(valor);
        this.marcar();
    }

    toggleComida(dia) {
        if (dia.comida_inicio !== null) {
            dia.comida_inicio = null; dia.comida_fin = null;
        } else {
            dia.comida_inicio = 13.0; dia.comida_fin = 14.0;
        }
        this.marcar();
    }

    aplicarPlantilla(clave) {
        const p = this.state.plantillas.find((x) => x.clave === clave);
        if (!p || !this.state.emp) return;
        for (const dia of this.state.emp.dias) {
            const def = p.dias[String(dia.iso)];
            if (def) {
                dia.trabaja = true;
                dia.entrada = def.entrada; dia.salida = def.salida;
                dia.comida_inicio = def.comida_inicio; dia.comida_fin = def.comida_fin;
            } else {
                dia.trabaja = false;
                dia.entrada = dia.salida = dia.comida_inicio = dia.comida_fin = null;
            }
        }
        this.marcar();
    }

    async guardar() {
        if (this.state.saving || !this.state.emp) return;
        this.state.saving = true;
        this.state.error = "";
        try {
            const dias = this.state.emp.dias.map((d) => ({
                iso: d.iso, trabaja: d.trabaja, entrada: d.entrada, salida: d.salida,
                comida_inicio: d.comida_inicio, comida_fin: d.comida_fin,
            }));
            const emp = await this.orm.call("foco.settings", "horario_guardar", [this.state.sel, dias]);
            this.state.emp = emp;
            this.state.dirty = false;
            this.state.savedAt = Date.now();
            await this.refrescarLista();
        } catch (e) {
            // El servidor manda un UserError con el dia que no cuadra; lo mostramos tal cual.
            this.state.error = (e && e.data && e.data.message) || e.message || "No se pudo guardar.";
        } finally {
            this.state.saving = false;
        }
    }

    descartar() { if (this.state.sel) this.seleccionar(this.state.sel); }

    async refrescarLista() {
        const r = await this.orm.call("foco.settings", "horarios_tablero", []);
        this.state.empleados = r.empleados || [];
    }

    // ----------------------------------------------------------- derivados
    horasDia(dia) {
        if (!dia.trabaja || dia.entrada === null || dia.salida === null || dia.salida <= dia.entrada) {
            return 0;
        }
        let h = dia.salida - dia.entrada;
        if (dia.comida_inicio !== null && dia.comida_fin !== null && dia.comida_fin > dia.comida_inicio) {
            h -= (dia.comida_fin - dia.comida_inicio);
        }
        return Math.max(0, h);
    }
    get horasSemana() {
        if (!this.state.emp) return 0;
        return this.state.emp.dias.reduce((s, d) => s + this.horasDia(d), 0);
    }
    get sobreTope() { return this.horasSemana > (this.state.tope || 40) + 0.001; }
    get diasActivos() {
        return this.state.emp ? this.state.emp.dias.filter((d) => d.trabaja).length : 0;
    }
    get guardado() { return this.state.savedAt && (Date.now() - this.state.savedAt < 4000); }
    get fuenteTxt() { return this.state.emp ? (FUENTE_TXT[this.state.emp.fuente_hoy] || "") : ""; }

    // ----------------------------------------------------------- formato
    /** 18.5 -> "18:30"; null -> "" */
    f2hhmm(f) {
        if (f === null || f === undefined || f === false) return "";
        const m = Math.round(f * 60);
        return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    }
    /** "18:30" -> 18.5; vacío -> null */
    hhmm2f(s) {
        if (!s) return null;
        const [h, m] = s.split(":").map(Number);
        if (Number.isNaN(h)) return null;
        return h + (m || 0) / 60;
    }
    /** horas decimales -> "8 h 30 m" / "5 h" / "0 h" */
    hm(h) {
        const m = Math.round((h || 0) * 60);
        const hh = Math.floor(m / 60), mm = m % 60;
        return mm ? `${hh} h ${mm} m` : `${hh} h`;
    }
    iniciales(n) {
        return (n || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase();
    }
    hue(n) { let h = 0; for (const c of (n || "")) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }

    detectTheme() {
        const leer = (el) => {
            if (!el) return null;
            const m = getComputedStyle(el).backgroundColor.match(/[\d.]+/g);
            if (!m || m.length < 3) return null;
            if (m.length === 4 && parseFloat(m[3]) === 0) return null;
            return (0.299 * +m[0] + 0.587 * +m[1] + 0.114 * +m[2]) / 255 < 0.5;
        };
        let v = leer(document.body);
        if (v === null) v = leer(document.documentElement);
        if (v === null) {
            v = !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
        }
        this.state.dark = v;
    }
}

registry.category("actions").add("foco_horarios", FocoHorarios);
