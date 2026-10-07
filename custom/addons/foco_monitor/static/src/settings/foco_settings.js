/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Antes esta pantalla EDITABA un horario global de medicion. Desde que RRHH
// asigna calendarios laborales no gobernaba a nadie, y desde el 28-sep-2026 la
// jornada la dice el CHECADOR para quien lo usa. Ahora solo dice, por persona,
// de donde sale su jornada, y manda a editarla donde vive: Asistencias o el
// calendario de RRHH. Duplicar eso aqui seria tener dos verdades.
const FUENTE = {
    checador: "Checador",
    calendario: "Calendario laboral",
    ninguno: "Sin jornada",
};

export class FocoSettings extends Component {
    static template = "foco_monitor.Settings";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        this.FUENTE = FUENTE;
        this.state = useState({
            loading: true, dark: false,
            personas: [], conteo: { checador: 0, calendario: 0, ninguno: 0 }, ventana: 30,
        });
        onWillStart(() => this.load());
        onMounted(() => this.detectTheme());
    }

    async load() {
        const r = await this.orm.call("foco.settings", "jornada_fuentes", []);
        this.state.personas = r.personas || [];
        this.state.conteo = r.conteo || { checador: 0, calendario: 0, ninguno: 0 };
        this.state.ventana = r.ventana_dias || 30;
        this.state.loading = false;
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
    }

    /** "2 con checador · 1 con calendario · 1 sin jornada", solo lo que exista. */
    get resumen() {
        const c = this.state.conteo;
        const p = (n, s, pl) => `${n} ${n === 1 ? s : pl}`;
        const partes = [];
        if (c.checador) partes.push(p(c.checador, "persona con checador", "personas con checador"));
        if (c.calendario) partes.push(p(c.calendario, "con calendario laboral", "con calendario laboral"));
        if (c.ninguno) partes.push(p(c.ninguno, "sin jornada", "sin jornada"));
        return partes.join(" · ");
    }

    /** Lo que explica la fuente de esa persona, en una linea. */
    detalle(p) {
        if (p.fuente === "checador") {
            return p.ultima_checada ? `última checada ${this.fecha(p.ultima_checada)}` : "";
        }
        if (p.fuente === "calendario") return p.calendario;
        return p.calendario
            ? `${p.calendario} · sin franjas utilizables`
            : "sin checador en los últimos días y sin calendario laboral";
    }

    fecha(ymd) {
        const [y, m, d] = ymd.split("-").map(Number);
        const meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
        return `${d} ${meses[m - 1]}${y !== new Date().getFullYear() ? " " + y : ""}`;
    }

    /** El calendario se edita en RRHH, no aqui. */
    abrirCalendario(p) {
        if (!p.calendario_id) return this.abrirEmpleado(p);
        this.action.doAction({
            type: "ir.actions.act_window", name: p.calendario, res_model: "resource.calendar",
            res_id: p.calendario_id, views: [[false, "form"]], target: "current",
        });
    }

    /** Las asistencias de esa persona, en el modulo que las registra. */
    abrirAsistencias(p) {
        this.action.doAction({
            type: "ir.actions.act_window", name: `Asistencias · ${p.nombre}`,
            res_model: "hr.attendance", domain: [["employee_id", "=", p.id]],
            views: [[false, "list"], [false, "form"]], target: "current",
        });
    }

    abrirEmpleado(p) {
        this.action.doAction({
            type: "ir.actions.act_window", name: p.nombre, res_model: "hr.employee",
            res_id: p.id, views: [[false, "form"]], target: "current",
        });
    }
}

registry.category("actions").add("foco_settings", FocoSettings);
