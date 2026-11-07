/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Acentos por dominio (coinciden con el SCSS).
const ACENTO = {
    ventas: "#5b6ee1",
    compras: "#1f8f88",
    almacen: "#cf8325",
    pagos: "#2e9e5b",
    neutro: "#8a8f98",
};
// Paleta de la dona "ventas por tipo".
const SERIE = ["#5b6ee1", "#1f8f88", "#cf8325", "#b2537e", "#2e9e5b", "#8a8f98"];

export class FerbaOperativo extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        // Tema detectado ANTES del primer render (evita parpadeo y pinta los charts bien).
        this.state = useState({ loading: true, data: null, error: null, dark: this._esOscuro() });
        this.trendCanvas = useRef("trendCanvas");
        this.donutCanvas = useRef("donutCanvas");
        this.charts = {};
        this._needCharts = false;

        onWillStart(() => this.cargar());
        onMounted(() => this._dibujaCharts());
        onPatched(() => {
            if (this._needCharts) { this._needCharts = false; this._dibujaCharts(); }
        });
        onWillUnmount(() => this._destruyeCharts());
    }

    async cargar() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("ferba.operativo.dashboard", "data", []);
            this.state.error = null;
            this._needCharts = true; // redibuja al terminar el patch
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
    moneyC(v) {
        v = v || 0;
        const a = Math.abs(v);
        if (a >= 1e6) return this.moneda + " " + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
        if (a >= 1e3) return this.moneda + " " + (v / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
        return this.moneda + " " + Math.round(v).toLocaleString("es-MX");
    }
    acento(key) { return ACENTO[key] || ACENTO.neutro; }
    serie(i) { return SERIE[i % SERIE.length]; }

    // ---- barras (bloques de estatus)
    maxCount(filas) { return Math.max(1, ...(filas || []).map((f) => f.count || 0)); }
    barPct(count, filas) { return Math.round(((count || 0) / this.maxCount(filas)) * 100); }

    // ---- Chart.js ------------------------------------------------------------
    _ejeColor() { return this.state.dark ? "rgba(230,232,236,0.62)" : "rgba(26,29,35,0.55)"; }
    _gridColor() { return this.state.dark ? "rgba(255,255,255,0.08)" : "rgba(17,20,24,0.07)"; }
    _tipFondo() { return this.state.dark ? "rgba(17,19,26,0.94)" : "rgba(255,255,255,0.96)"; }
    _tipTexto() { return this.state.dark ? "#e6e8ec" : "#1a1d23"; }

    _destruyeCharts() {
        for (const k of Object.keys(this.charts)) {
            try { this.charts[k].destroy(); } catch (e) { /* noop */ }
        }
        this.charts = {};
    }

    _dibujaCharts() {
        const Chart = window.Chart;
        const d = this.state.data;
        if (!Chart || !d) { return; }
        this._destruyeCharts();
        const self = this;

        // ---- Tendencia: ventas confirmadas por mes (linea + area)
        if (this.trendCanvas.el && d.ventas.por_mes.length) {
            const pm = d.ventas.por_mes;
            this.charts.trend = new Chart(this.trendCanvas.el, {
                type: "line",
                data: {
                    labels: pm.map((m) => m.mes),
                    datasets: [{
                        data: pm.map((m) => m.monto),
                        parsing: false,
                        borderColor: ACENTO.ventas,
                        borderWidth: 2.5,
                        tension: 0.4,
                        pointRadius: 0,
                        pointHoverRadius: 5,
                        pointHoverBackgroundColor: ACENTO.ventas,
                        pointHoverBorderColor: self.state.dark ? "#1b1d26" : "#ffffff",
                        pointHoverBorderWidth: 2,
                        fill: true,
                        backgroundColor: (context) => {
                            const { ctx, chartArea } = context.chart;
                            if (!chartArea) { return "rgba(91,110,225,0.12)"; }
                            const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                            g.addColorStop(0, self.state.dark ? "rgba(91,110,225,0.45)" : "rgba(91,110,225,0.30)");
                            g.addColorStop(1, "rgba(91,110,225,0.00)");
                            return g;
                        },
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    animation: { duration: 650, easing: "easeOutCubic" },
                    layout: { padding: { top: 6 } },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: self._tipFondo(), titleColor: self._tipTexto(),
                            bodyColor: self._tipTexto(), borderColor: self._gridColor(),
                            borderWidth: 1, padding: 10, displayColors: false, cornerRadius: 10,
                            titleFont: { weight: "600" },
                            callbacks: {
                                title: (items) => {
                                    const m = pm[items[0].dataIndex];
                                    return m.mes + " " + m.anio + (m.parcial ? " (parcial)" : "");
                                },
                                label: (item) => self.money(item.raw),
                            },
                        },
                    },
                    scales: {
                        x: { grid: { display: false }, border: { display: false },
                             ticks: { color: self._ejeColor(), font: { size: 11 } } },
                        y: { display: false, beginAtZero: true, grace: "8%" },
                    },
                },
            });
        }

        // ---- Dona: ventas confirmadas por tipo
        if (this.donutCanvas.el && d.ventas.por_tipo.length) {
            const pt = d.ventas.por_tipo;
            this.charts.donut = new Chart(this.donutCanvas.el, {
                type: "doughnut",
                data: {
                    labels: pt.map((t) => t.label),
                    datasets: [{
                        data: pt.map((t) => t.amount_total),
                        backgroundColor: pt.map((t, i) => self.serie(i)),
                        borderColor: self.state.dark ? "rgba(20,22,30,0.55)" : "rgba(255,255,255,0.65)",
                        borderWidth: 2,
                        hoverOffset: 6,
                        spacing: 2,
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    cutout: "68%",
                    animation: { duration: 650, easing: "easeOutCubic" },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: self._tipFondo(), titleColor: self._tipTexto(),
                            bodyColor: self._tipTexto(), borderColor: self._gridColor(),
                            borderWidth: 1, padding: 10, cornerRadius: 10,
                            callbacks: {
                                label: (item) => {
                                    const total = item.dataset.data.reduce((a, b) => a + b, 0) || 1;
                                    const pct = Math.round((item.raw / total) * 100);
                                    return " " + self.moneyC(item.raw) + "  ·  " + pct + "%";
                                },
                            },
                        },
                    },
                },
            });
        }
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
