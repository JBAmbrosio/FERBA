/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Color de marca Odoo (primary = plum) + paleta sobria para la dona.
const BRAND = "#714B67";
const BRAND_RGB = "113, 75, 103";
const SERIE = ["#714B67", "#00A09D", "#E6A23C", "#5B6EB4", "#8F6FA8", "#9AA1AB"];

// Guia vertical en la linea: orienta la lectura al pasar el raton por un mes.
const CROSSHAIR = {
    id: "foCrosshair",
    afterDatasetsDraw(chart) {
        const act = chart.tooltip && chart.tooltip._active;
        if (!act || !act.length) { return; }
        const x = act[0].element.x;
        const { top, bottom } = chart.chartArea;
        const c = chart.ctx;
        c.save();
        c.beginPath();
        c.moveTo(x, top);
        c.lineTo(x, bottom);
        c.lineWidth = 1;
        c.strokeStyle = "rgba(" + BRAND_RGB + ", 0.35)";
        c.stroke();
        c.restore();
    },
};

export class FerbaOperativo extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        // Tema detectado ANTES del primer render (evita parpadeo y pinta los charts bien).
        this.state = useState({ loading: true, data: null, error: null, dark: this._esOscuro() });
        this.root = useRef("root");
        this.trendCanvas = useRef("trendCanvas");
        this.donutCanvas = useRef("donutCanvas");
        this.charts = {};
        this._needCharts = false;

        onWillStart(() => this.cargar());
        onMounted(() => this._postRender());
        onPatched(() => {
            if (this._needCharts) { this._needCharts = false; this._postRender(); }
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
    acento() { return BRAND; }
    serie(i) { return SERIE[i % SERIE.length]; }
    get totalTipo() {
        const pt = (this.state.data && this.state.data.ventas && this.state.data.ventas.por_tipo) || [];
        return pt.reduce((a, t) => a + (t.amount_total || 0), 0);
    }

    // ---- barras (bloques de estatus)
    maxCount(filas) { return Math.max(1, ...(filas || []).map((f) => f.count || 0)); }
    barPct(count, filas) { return Math.round(((count || 0) / this.maxCount(filas)) * 100); }

    // ---- Chart.js ------------------------------------------------------------
    _ejeColor() { return this.state.dark ? "rgba(228,228,228,0.62)" : "rgba(17,24,39,0.55)"; }
    _gridColor() { return this.state.dark ? "rgba(255,255,255,0.09)" : "#dee2e6"; }
    _tipFondo() { return this.state.dark ? "#2b2f3a" : "#ffffff"; }
    _tipTexto() { return this.state.dark ? "#e4e4e4" : "#111827"; }

    _postRender() {
        this._dibujaCharts();
        this._animaNumeros();
    }

    // Conteo animado de los numeros grandes (KPIs, flujo, tiles). Escribe
    // directo en el DOM (fuera de OWL) para no re-renderizar en cada frame.
    _animaNumeros() {
        const root = this.root.el;
        if (!root) { return; }
        const nodes = root.querySelectorAll("[data-count]");
        const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const fmt = (v) => Math.round(v).toLocaleString("es-MX");
        const ease = (t) => 1 - Math.pow(1 - t, 3); // easeOutCubic
        nodes.forEach((n) => {
            const target = parseFloat(n.getAttribute("data-count")) || 0;
            if (reduce || target <= 0) { n.textContent = fmt(target); return; }
            n.textContent = fmt(0); // arranca en cero antes del primer pintado
            const dur = 680;
            const t0 = performance.now();
            const step = (now) => {
                const p = Math.min(1, (now - t0) / dur);
                n.textContent = fmt(target * ease(p));
                if (p < 1) { requestAnimationFrame(step); }
            };
            requestAnimationFrame(step);
        });
    }

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
                plugins: [CROSSHAIR],
                data: {
                    labels: pm.map((m) => m.mes),
                    datasets: [{
                        data: pm.map((m) => m.monto),
                        borderColor: BRAND,
                        borderWidth: 2,
                        tension: 0.35,
                        pointRadius: 0,
                        pointHoverRadius: 5,
                        pointHoverBackgroundColor: BRAND,
                        pointHoverBorderColor: self.state.dark ? "#2b2f3a" : "#ffffff",
                        pointHoverBorderWidth: 2,
                        fill: true,
                        backgroundColor: (context) => {
                            const { ctx, chartArea } = context.chart;
                            if (!chartArea) { return "rgba(" + BRAND_RGB + ",0.10)"; }
                            const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                            g.addColorStop(0, self.state.dark ? "rgba(" + BRAND_RGB + ",0.40)" : "rgba(" + BRAND_RGB + ",0.22)");
                            g.addColorStop(1, "rgba(" + BRAND_RGB + ",0.00)");
                            return g;
                        },
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    animation: { duration: 650, easing: "easeOutCubic" },
                    layout: { padding: { top: 6 } },
                    // Clave del fix: la linea no tiene puntos visibles, asi que con
                    // intersect el tooltip nunca disparaba. En modo indice sale al
                    // pasar el raton por cualquier parte del mes.
                    interaction: { mode: "index", intersect: false },
                    hover: { mode: "index", intersect: false },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: self._tipFondo(), titleColor: self._tipTexto(),
                            bodyColor: self._tipTexto(), borderColor: self._gridColor(),
                            borderWidth: 1, padding: 10, displayColors: false, cornerRadius: 4,
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
                        borderColor: self.state.dark ? "#2b2f3a" : "#ffffff",
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
                            borderWidth: 1, padding: 10, cornerRadius: 4,
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
