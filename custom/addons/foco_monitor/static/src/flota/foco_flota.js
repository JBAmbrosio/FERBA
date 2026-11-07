/** @odoo-module **/

import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

/**
 * Tablero de Flota: lo que el ADMINISTRADOR de la flota necesita de un vistazo.
 * Operate mode: lo primero es la escaneabilidad -quien trae cada coche, que
 * documento esta por vencer, que responsiva falta firmar-; el color solo marca
 * lo que pide accion. Reusa el lenguaje visual del tablero Foco (foco_dashboard).
 */
export class FocoFlota extends Component {
    static template = "foco_monitor.FlotaDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.state = useState({ loading: true, dark: false, data: null });
        this.detectarTema();
        // El tema de Odoo puede cambiar sin recargar: se sigue por mutaciones.
        this._obs = new MutationObserver(() => this.detectarTema());
        for (const el of [document.documentElement, document.body]) {
            this._obs.observe(el, { attributes: true, attributeFilter: ["class", "style", "data-color-scheme"] });
        }
        onWillStart(() => this.cargar());
        onWillUnmount(() => this._obs && this._obs.disconnect());
    }

    async cargar() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("foco.vehiculo", "tablero_flota", []);
        } finally {
            this.state.loading = false;
        }
    }

    detectarTema() {
        const oscuro = (el) => {
            if (!el) return null;
            const m = getComputedStyle(el).backgroundColor.match(/[\d.]+/g);
            if (!m || m.length < 3) return null;
            if (m.length === 4 && parseFloat(m[3]) === 0) return null;
            return (0.299 * +m[0] + 0.587 * +m[1] + 0.114 * +m[2]) / 255 < 0.5;
        };
        let v = oscuro(document.body);
        if (v === null) v = oscuro(document.documentElement);
        if (v === null) v = !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
        this.state.dark = v;
    }

    // ------------------------------------------------------------- formato
    get d() { return this.state.data || {}; }
    get kpis() { return this.d.kpis || {}; }
    get alertas() { return this.d.alertas || {}; }
    get hayAlertas() {
        const a = this.alertas;
        return (a.docs_vencidos || []).length || (a.docs_por_vencer || []).length ||
               (a.multi_vehiculo || []).length;
    }

    dinero(n) {
        return (this.d.moneda || "") + (n || 0).toLocaleString("es-MX", { maximumFractionDigits: 0 });
    }
    fecha(s) {
        if (!s) return "";
        const dt = new Date(String(s).replace(" ", "T") + "Z");
        return isNaN(dt) ? s : dt.toLocaleDateString("es-MX", { day: "2-digit", month: "short" });
    }
    dias(s) {
        if (!s) return null;
        const dt = new Date(String(s).replace(" ", "T") + "Z");
        if (isNaN(dt)) return null;
        return Math.max(0, Math.floor((Date.now() - dt.getTime()) / 86400000));
    }
    // Texto de "lo trae desde": "desde el 20 oct · 6 días".
    desdeTxt(v) {
        if (!v.desde) return "";
        const n = this.dias(v.desde);
        const dd = this.fecha(v.desde);
        return n != null ? `desde el ${dd} · ${n} ${n === 1 ? "día" : "días"}` : `desde el ${dd}`;
    }
    // Clase de color de un documento (seguro/tenencia) por su estado.
    docClase(estado) {
        return { ok: "ff_ok", por_vencer: "ff_mid", vencido: "ff_low", na: "ff_na" }[estado] || "ff_na";
    }
    docTxt(estado, dias) {
        if (estado === "na") return "sin fecha";
        if (estado === "vencido") return `vencido${dias ? ` (${-dias} d)` : ""}`;
        if (estado === "por_vencer") return `en ${dias} d`;
        return `${dias} d`;
    }

    // ------------------------------------------------------------- acciones
    abrir(id) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "foco.vehiculo",
            res_id: id, views: [[false, "form"]], target: "current",
        });
    }
    nuevo() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "foco.vehiculo",
            name: "Nuevo vehículo", views: [[false, "form"]], target: "current",
        });
    }
    verLista() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "foco.vehiculo",
            name: "Flota", views: [[false, "list"], [false, "form"]], target: "current",
        });
    }
    entregar(ev, v) {
        ev.stopPropagation();
        // Se arma la accion aqui (con `views`) en vez de usar la del servidor:
        // doAction sobre un dict sin `views` revienta en _preprocessAction.
        this.action.doAction({
            type: "ir.actions.act_window",
            name: v.asignado ? "Entregar a otra persona" : "Entregar vehículo",
            res_model: "foco.vehiculo.asignacion",
            views: [[false, "form"]],
            target: "new",
            context: { default_vehiculo_id: v.id, default_odometro_entrega: v.odometro || 0 },
        }, { onClose: () => this.cargar() });
    }
    devolver(ev, v) {
        ev.stopPropagation();
        this.dialog.add(ConfirmationDialog, {
            title: "Devolver vehículo",
            body: `${v.tenedor} deja de traer ${v.name}. ¿Devolver el vehículo?`,
            confirmLabel: "Devolver",
            confirm: async () => {
                await this.orm.call("foco.vehiculo", "action_devolver", [[v.id]]);
                await this.cargar();
            },
            cancel: () => {},
        });
    }
}

registry.category("actions").add("foco_flota", FocoFlota);
