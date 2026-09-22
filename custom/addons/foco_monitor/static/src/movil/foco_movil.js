/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount, useRef, useEffect } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle, loadJS, loadCSS } from "@web/core/assets";

// Chart.js lo trae Odoo (mismo bundle que el tablero de PC). Leaflet va
// VENDORIZADO en el modulo (static/lib/leaflet): un mapa que dependiera de un
// CDN se caeria cuando se cayera la red de un tercero.
let ChartJS = null;
async function traerChart() {
    if (ChartJS) return ChartJS;
    await loadBundle("web.chartjs_lib");
    ChartJS = window.Chart;
    return ChartJS;
}
let leafletOk = false;
async function traerLeaflet() {
    if (leafletOk && window.L) return window.L;
    await loadCSS("/foco_monitor/static/lib/leaflet/leaflet.css");
    await loadJS("/foco_monitor/static/lib/leaflet/leaflet.js");
    leafletOk = true;
    return window.L;
}

export class FocoMovil extends Component {
    static template = "foco_monitor.Movil";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true, dark: false, days: 1, sel: "all",
            kpis: {}, devices: [], markers: [], track: {}, calls: {}, apps: [],
            ultimo: null,
        });
        this.mapEl = useRef("map");
        this.cDir = useRef("cDir");
        this.cDia = useRef("cDia");
        this.cApps = useRef("cApps");
        this.map = null;
        this.capas = null;
        this.tile = null;
        this.graficas = {};

        onWillStart(() => this.load());
        onMounted(() => {
            this.detectTheme();
            this.pintar();
            this.temporizador = setInterval(() => this.refrescar(), 10 * 60 * 1000);
            // el tema puede cambiar sin recargar: repinta mapa y graficas
            this.observador = new MutationObserver(() => {
                const antes = this.state.dark;
                this.detectTheme();
                if (this.state.dark !== antes) this.pintar();
            });
            this.observador.observe(document.body,
                { attributes: true, attributeFilter: ["class", "style", "data-color-scheme"] });
        });
        onWillUnmount(() => {
            clearInterval(this.temporizador);
            if (this.observador) this.observador.disconnect();
            this.tirarGraficas();
            if (this.map) { try { this.map.remove(); } catch (e) { /* */ } this.map = null; }
        });
        useEffect(
            () => { this.pintar(); },
            () => [this.state.markers, this.state.track, this.state.calls, this.state.apps,
                   this.state.dark, this.state.sel]
        );
    }

    // ------------------------------------------------------------- datos
    ymd(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    fmtMin(m) {
        m = Math.round(m || 0);
        return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
    }

    async load() {
        if (!this.state.ultimo) this.state.loading = true;
        this.detectTheme();
        const hoy = new Date();
        const ini = new Date(hoy);
        ini.setDate(hoy.getDate() - (this.state.days - 1));
        const r = await this.orm.call("foco.mobile.device", "dashboard",
            [this.ymd(ini), this.ymd(hoy)]);
        this.state.kpis = r.kpis || {};
        this.state.devices = r.devices || [];
        this.state.markers = r.markers || [];
        this.state.track = r.track || {};
        this.state.calls = r.calls || {};
        this.state.apps = r.apps || [];
        this.state.ultimo = new Date();
        this.state.loading = false;
    }

    async refrescar() { await this.load(); }
    setDays(n) { if (n === this.state.days) return; this.state.days = n; this.load(); }
    seleccionar(id) { this.state.sel = (this.state.sel === id) ? "all" : id; }

    // Cambiar al tablero de laptop sin pasar por el menu.
    verLaptop() { this.action.doAction("foco_monitor.foco_dashboard_action"); }

    // Nivel de bateria -> clase de color y ancho de relleno.
    batClase(p) { return p >= 50 ? "good" : (p >= 20 ? "mid" : "low"); }
    batPct(p) { return Math.max(0, Math.min(100, p || 0)); }

    detectTheme() {
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

    get tinta() {
        const d = this.state.dark;
        return {
            ink: d ? "#e9edf4" : "#0f1520", ink2: d ? "#aab4c4" : "#5a6577",
            ink3: d ? "#7c8797" : "#8b95a6", linea: d ? "#2b3442" : "#e7eaf0",
            card: d ? "#171c27" : "#ffffff", accent: d ? "#6b9bff" : "#2f6fed",
            verde: d ? "#34d399" : "#10b981", rojo: d ? "#f87171" : "#ef4444",
            gris: d ? "#64748b" : "#94a3b8", ambar: "#f59e0b",
        };
    }

    // ------------------------------------------------------------- pintar
    async pintar() {
        await this.pintarMapa();
        await this.pintarGraficas();
    }

    tirarGraficas() {
        for (const k of Object.keys(this.graficas)) {
            try { this.graficas[k].destroy(); } catch (e) { /* */ }
            delete this.graficas[k];
        }
    }

    async pintarMapa() {
        if (!this.mapEl.el) return;
        let L;
        try { L = await traerLeaflet(); } catch (e) { return; }
        if (!L) return;
        if (!this.map) {
            this.map = L.map(this.mapEl.el, { zoomControl: true, attributionControl: true })
                .setView([23.63, -102.55], 4);   // Mexico, por si no hay puntos
            this.capas = L.layerGroup().addTo(this.map);
        }
        // Teselas de Esri (World Gray Canvas), SIN API key y sin el look del OSM
        // plano: base gris limpia clara/oscura que deja resaltar el recorrido.
        // (CARTO empezo a exigir key en sus basemaps, de ahi el "API KEY REQUIRED".)
        const url = this.state.dark
            ? "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
            : "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}";
        if (this.tile) { this.map.removeLayer(this.tile); }
        this.tile = L.tileLayer(url, {
            maxZoom: 16, attribution: "&copy; Esri",
        }).addTo(this.map);

        this.capas.clearLayers();
        const bounds = [];
        // recorridos (una linea por equipo)
        for (const [did, pts] of Object.entries(this.state.track || {})) {
            if (this.state.sel !== "all" && String(this.state.sel) !== did) continue;
            if (pts && pts.length > 1) {
                L.polyline(pts, { color: this.tinta.accent, weight: 3, opacity: 0.65 }).addTo(this.capas);
            }
        }
        // marcadores de ultima posicion
        for (const m of this.state.markers || []) {
            if (this.state.sel !== "all" && this.state.sel !== m.id) continue;
            if (!m.lat && !m.lon) continue;
            const col = m.online ? this.tinta.verde : this.tinta.gris;
            const mk = L.circleMarker([m.lat, m.lon], {
                radius: 8, color: "#ffffff", weight: 2, fillColor: col, fillOpacity: 1,
            }).addTo(this.capas);
            mk.bindPopup(
                `<b>${m.employee || m.name || ""}</b><br>${m.name || ""}<br>` +
                `${m.at || ""} · ${m.battery >= 0 ? m.battery + "%" : ""}`);
            bounds.push([m.lat, m.lon]);
        }
        if (bounds.length) {
            try { this.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 }); } catch (e) { /* */ }
        }
        // el contenedor pudo medir 0 al crearse: se recalcula
        setTimeout(() => { if (this.map) this.map.invalidateSize(); }, 120);
    }

    async pintarGraficas() {
        let Chart;
        try { Chart = await traerChart(); } catch (e) { return; }
        if (!Chart) return;
        this.tirarGraficas();
        const t = this.tinta;
        const baseLeyenda = {
            display: true, position: "bottom",
            labels: { color: t.ink2, boxWidth: 10, boxHeight: 10, usePointStyle: true,
                      pointStyle: "circle", font: { size: 11 }, padding: 12 },
        };

        // 1) Llamadas por direccion (dona)
        if (this.cDir.el) {
            const c = this.state.calls || {};
            const otras = (c.rejected || 0) + (c.blocked || 0) + (c.other || 0);
            const partes = [
                { et: "Entrantes", v: c.in || 0, col: t.verde },
                { et: "Salientes", v: c.out || 0, col: t.accent },
                { et: "Perdidas", v: c.missed || 0, col: t.rojo },
                { et: "Otras", v: otras, col: t.gris },
            ].filter((x) => x.v > 0);
            const total = partes.reduce((a, x) => a + x.v, 0);
            const centro = {
                id: "centroLlam",
                afterDraw: (ch) => {
                    const arc = ch.getDatasetMeta(0).data[0]; if (!arc) return;
                    const ctx = ch.ctx; ctx.save();
                    ctx.textAlign = "center"; ctx.textBaseline = "middle";
                    ctx.fillStyle = t.ink; ctx.font = "700 26px system-ui, sans-serif";
                    ctx.fillText(String(total), arc.x, arc.y - 6);
                    ctx.fillStyle = t.ink3; ctx.font = "600 11px system-ui, sans-serif";
                    ctx.fillText("LLAMADAS", arc.x, arc.y + 15);
                    ctx.restore();
                },
            };
            this.graficas.dir = new Chart(this.cDir.el, {
                type: "doughnut",
                data: {
                    labels: partes.map((x) => x.et),
                    datasets: [{
                        data: partes.map((x) => x.v),
                        backgroundColor: partes.map((x) => x.col),
                        borderColor: t.card, borderWidth: 3, hoverOffset: 6,
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false, cutout: "66%",
                    animation: { duration: 420, easing: "easeOutQuart" },
                    plugins: {
                        legend: baseLeyenda,
                        tooltip: {
                            backgroundColor: t.card, titleColor: t.ink, bodyColor: t.ink2,
                            borderColor: t.linea, borderWidth: 1, padding: 10, cornerRadius: 8,
                            callbacks: { label: (x) => ` ${x.label}: ${x.parsed}` },
                        },
                    },
                },
                plugins: [centro],
            });
        }

        // 2) Llamadas por dia (barras)
        if (this.cDia.el) {
            const dias = (this.state.calls && this.state.calls.by_day) || [];
            this.graficas.dia = new Chart(this.cDia.el, {
                type: "bar",
                data: {
                    labels: dias.map((d) => this.ejeDia(d.dia)),
                    datasets: [{
                        label: "Llamadas", data: dias.map((d) => d.n),
                        backgroundColor: t.accent, borderRadius: 5, maxBarThickness: 30,
                    }],
                },
                options: this.opcionesBar(t),
            });
        }

        // 3) Top de apps del movil (barras horizontales)
        if (this.cApps.el) {
            const apps = (this.state.apps || []).slice(0, 8);
            this.graficas.apps = new Chart(this.cApps.el, {
                type: "bar",
                data: {
                    labels: apps.map((a) => this.corta(a.label)),
                    datasets: [{
                        label: "Horas", data: apps.map((a) => a.hours),
                        backgroundColor: t.verde, borderRadius: 4, maxBarThickness: 22,
                    }],
                },
                options: {
                    indexAxis: "y",
                    responsive: true, maintainAspectRatio: false,
                    animation: { duration: 420, easing: "easeOutQuart" },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: t.card, titleColor: t.ink, bodyColor: t.ink2,
                            borderColor: t.linea, borderWidth: 1, padding: 10, cornerRadius: 8,
                            callbacks: { label: (x) => ` ${this.fmtMin((x.parsed.x || 0) * 60)}` },
                        },
                    },
                    scales: {
                        x: { grid: { color: t.linea, drawTicks: false }, border: { display: false },
                             ticks: { color: t.ink3, font: { size: 10.5 }, callback: (v) => `${v}h` } },
                        y: { grid: { display: false }, border: { color: t.linea },
                             ticks: { color: t.ink2, font: { size: 11 } } },
                    },
                },
            });
        }
    }

    opcionesBar(t) {
        return {
            responsive: true, maintainAspectRatio: false,
            animation: { duration: 420, easing: "easeOutQuart" },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: t.card, titleColor: t.ink, bodyColor: t.ink2,
                    borderColor: t.linea, borderWidth: 1, padding: 10, cornerRadius: 8,
                },
            },
            scales: {
                x: { grid: { display: false }, border: { color: t.linea },
                     ticks: { color: t.ink3, font: { size: 10.5 }, maxRotation: 0, autoSkipPadding: 12 } },
                y: { grid: { color: t.linea, drawTicks: false }, border: { display: false },
                     ticks: { color: t.ink3, font: { size: 10.5 }, precision: 0 } },
            },
        };
    }

    ejeDia(iso) {
        if (!iso) return "";
        const [y, m, d] = iso.split("-").map(Number);
        const f = new Date(y, m - 1, d);
        const dd = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
        return `${dd[f.getDay()]} ${f.getDate()}`;
    }
    corta(s) { s = s || ""; return s.length > 18 ? s.slice(0, 17) + "…" : s; }
    horaUltimo() {
        if (!this.state.ultimo) return "";
        const d = this.state.ultimo;
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }
    sinceTxt(s) {
        if (!s) return "sin señal";
        return s.replace("T", " ").slice(0, 16);
    }
}

registry.category("actions").add("foco_movil", FocoMovil);
