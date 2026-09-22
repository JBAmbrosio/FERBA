/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const DAYS = [
    ["day_mon", "Lu"], ["day_tue", "Ma"], ["day_wed", "Mi"], ["day_thu", "Ju"],
    ["day_fri", "Vi"], ["day_sat", "Sá"], ["day_sun", "Do"],
];
const DAY_FULL = { day_mon: "lunes", day_tue: "martes", day_wed: "miércoles",
    day_thu: "jueves", day_fri: "viernes", day_sat: "sábado", day_sun: "domingo" };

export class FocoSettings extends Component {
    static template = "foco_monitor.Settings";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notif = useService("notification");
        this.root = useRef("root");
        this.DAYS = DAYS;
        this.state = useState({
            loading: true, dark: false, saving: false, dirty: false,
            id: null, enabled: true, from: "09:00", to: "20:00",
            days: {}, cobertura: null,
        });
        onWillStart(() => this.load());
        onMounted(() => this.detectTheme());
    }

    async load() {
        const fields = ["sched_enabled", "sched_from", "sched_to",
            ...DAYS.map((d) => d[0])];
        const [recs, cobertura] = await Promise.all([
            this.orm.searchRead("foco.settings", [], fields, { limit: 1 }),
            // Quien se rige de verdad por esta pantalla: si el empleado tiene
            // calendario laboral, ese gana y este horario no le aplica.
            this.orm.call("foco.settings", "cobertura_horario", []),
        ]);
        this.state.cobertura = cobertura;
        const r = recs[0] || {};
        this.state.id = r.id;
        this.state.enabled = r.sched_enabled !== undefined ? r.sched_enabled : true;
        this.state.from = this.toHHMM(r.sched_from ?? 9);
        this.state.to = this.toHHMM(r.sched_to ?? 20);
        const days = {};
        for (const [k] of DAYS) days[k] = !!r[k];
        this.state.days = days;
        this.state.dirty = false;
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

    toHHMM(f) {
        const h = Math.floor(f || 0);
        const m = Math.round(((f || 0) - h) * 60);
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
    }
    toFloat(s) {
        const [h, m] = (s || "0:0").split(":").map(Number);
        return (h || 0) + (m || 0) / 60;
    }

    setEnabled(v) { this.state.enabled = v; this.state.dirty = true; }
    setTime(field, value) { if (value) { this.state[field] = value; this.state.dirty = true; } }
    toggleDay(k) { this.state.days[k] = !this.state.days[k]; this.state.dirty = true; }

    get summary() {
        if (!this.state.enabled) return "Los agentes miden todo el tiempo (24/7).";
        const on = DAYS.filter(([k]) => this.state.days[k]);
        if (!on.length) return "Sin días activos: los agentes no medirán.";
        let dias;
        const keys = on.map((d) => d[0]).join(",");
        if (keys === "day_mon,day_tue,day_wed,day_thu,day_fri") dias = "de lunes a viernes";
        else if (on.length === 7) dias = "todos los días";
        else dias = on.map((d) => DAY_FULL[d[0]]).join(", ");
        return `Los agentes medirán de ${this.state.from} a ${this.state.to}, ${dias}.`;
    }

    async save() {
        if (!this.state.id || this.state.saving) return;
        this.state.saving = true;
        const vals = {
            sched_enabled: this.state.enabled,
            sched_from: this.toFloat(this.state.from),
            sched_to: this.toFloat(this.state.to),
        };
        for (const [k] of DAYS) vals[k] = !!this.state.days[k];
        try {
            await this.orm.write("foco.settings", [this.state.id], vals);
            this.state.dirty = false;
            this.notif.add("Horario guardado. Los agentes lo aplicarán en su próxima sincronización.",
                { type: "success" });
        } catch (e) {
            this.notif.add("No se pudo guardar.", { type: "danger" });
        } finally {
            this.state.saving = false;
        }
    }

    get gobierna() {
        const c = this.state.cobertura;
        if (!c || !c.total) return "";
        const p = (n) => (n === 1 ? "persona monitoreada" : "personas monitoreadas");
        if (!c.con_calendario) return `Se aplica a ${c.total === 1 ? "la" : "las"} ${c.total} ${p(c.total)}.`;
        if (!c.global) return `No se aplica a nadie: ${c.total === 1 ? "la" : "las"} ${c.total} ${p(c.total)} se ${c.total === 1 ? "rige" : "rigen"} por su calendario laboral.`;
        return `Se aplica a ${c.global} de ${c.total} ${p(c.total)}.`;
    }

    /** Abre el calendario en Odoo. Foco NO lo edita: duplicarlo crearia dos
     *  verdades sobre la jornada de una persona. */
    abrirCalendario(id, nombre) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: nombre,
            res_model: "resource.calendar",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    discard() { this.load(); }
}

registry.category("actions").add("foco_settings", FocoSettings);
