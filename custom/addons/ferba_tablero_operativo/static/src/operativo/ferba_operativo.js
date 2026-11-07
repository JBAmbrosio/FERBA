/** @odoo-module **/

import { Component, useState, onWillStart, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Colores de acento por dominio (deben coincidir con el SCSS).
const ACENTO = {
    ventas: "#5b6ee1",
    compras: "#1f8f88",
    almacen: "#cf8325",
    pagos: "#2e9e5b",
    neutro: "#8a8f98",
};
// Paleta para la dona de "ventas por tipo".
const SERIE = ["#5b6ee1", "#1f8f88", "#cf8325", "#b2537e", "#2e9e5b"];

export class FerbaOperativo extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, data: null, error: null, dark: false });
        onWillStart(() => this.cargar());
        onMounted(() => { this.state.dark = this._esOscuro(); });
    }

    async cargar() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("ferba.operativo.dashboard", "data", []);
            this.state.error = null;
        } catch (e) {
            this.state.error = (e && e.message) || String(e);
        }
        this.state.loading = false;
    }

    // ---- drill-down: abre los registros detras de una metrica
    abrir(model, domain, titulo) {
        if (!model) { return; }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: titulo || "Detalle",
            res_model: model,
            domain: domain || [],
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    // ---- formato
    get moneda() {
        return (this.state.data && this.state.data.meta && this.state.data.meta.moneda) || "$";
    }
    num(v) { return (v || 0).toLocaleString("es-MX"); }
    money(v) { return this.moneda + " " + Math.round(v || 0).toLocaleString("es-MX"); }
    // compacto para KPIs ($39.7M / $34.7k)
    moneyC(v) {
        v = v || 0;
        const a = Math.abs(v);
        if (a >= 1e6) return this.moneda + " " + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
        if (a >= 1e3) return this.moneda + " " + (v / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
        return this.moneda + " " + Math.round(v).toLocaleString("es-MX");
    }
    acento(key) { return ACENTO[key] || ACENTO.neutro; }

    // ---- barras
    maxCount(filas) { return Math.max(1, ...(filas || []).map((f) => f.count || 0)); }
    barPct(count, filas) { return Math.round(((count || 0) / this.maxCount(filas)) * 100); }

    // ---- dona (SVG): segmentos con stroke-dasharray sobre un circulo r=16
    donut(items, key) {
        const r = 16, C = 2 * Math.PI * r;
        const total = (items || []).reduce((s, it) => s + (it[key] || it.count || 0), 0);
        let acc = 0;
        const segs = (items || []).map((it, i) => {
            const val = it[key] || it.count || 0;
            const frac = total ? val / total : 0;
            const seg = { color: SERIE[i % SERIE.length], dash: frac * C,
                          gap: C - frac * C, offset: -acc * C, label: it.label,
                          val, pct: Math.round(frac * 100) };
            acc += frac;
            return seg;
        });
        return { r, C, total, segs };
    }

    // ---- tendencia (SVG): area + linea escaladas a un viewBox 0..W x 0..H
    trend(items) {
        const W = 520, H = 120, pad = 6;
        const vals = (items || []).map((it) => it.monto || 0);
        const max = Math.max(1, ...vals);
        const n = vals.length;
        const pts = vals.map((v, i) => {
            const x = n > 1 ? pad + (i * (W - 2 * pad)) / (n - 1) : W / 2;
            const y = H - pad - (v / max) * (H - 2 * pad);
            return [Math.round(x), Math.round(y)];
        });
        const line = pts.map((p) => p.join(",")).join(" ");
        const area = pts.length
            ? `${pts[0][0]},${H} ` + line + ` ${pts[pts.length - 1][0]},${H}`
            : "";
        const ult = vals.length ? vals[vals.length - 1] : 0;
        return { W, H, pts, line, area, max, ult };
    }

    _esOscuro() {
        try {
            let el = document.body;
            while (el) {
                const bg = getComputedStyle(el).backgroundColor || "";
                const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
                if (m && (m[4] === undefined || parseFloat(m[4]) > 0.1)) {
                    const lum = (0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3]) / 255;
                    return lum < 0.5;
                }
                el = el.parentElement;
            }
        } catch (e) { /* claro por defecto */ }
        return false;
    }
}

FerbaOperativo.template = "ferba_tablero_operativo.Dashboard";
registry.category("actions").add("ferba_operativo", FerbaOperativo);
