/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount, useRef, useEffect } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS, loadCSS } from "@web/core/assets";

// Leaflet va VENDORIZADO en el modulo (static/lib/leaflet): un mapa que
// dependiera de un CDN se caeria cuando se cayera la red de un tercero.
let leafletOk = false;
async function traerLeaflet() {
    if (leafletOk && window.L) return window.L;
    await loadCSS("/foco_monitor/static/lib/leaflet/leaflet.css");
    await loadJS("/foco_monitor/static/lib/leaflet/leaflet.js");
    leafletOk = true;
    return window.L;
}

export class FocoVendedores extends Component {
    static template = "foco_monitor.Vendedores";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true, dark: false,
            clientes: [], kpis: {}, cat: { zonas: [], cultivos: [], vendedores: [], estatus: [] },
            estatusLabels: {}, estatusColor: {},
            f: { vendedor: "", zona: "", cultivo: "", estatus: "" },
            sel: null, ultimo: null,
        });
        this.mapEl = useRef("map");
        this.map = null;
        this.capa = null;
        this.tile = null;
        this.marcas = {};

        onWillStart(() => this.load());
        onMounted(() => {
            this.detectTheme();
            this.pintarMapa();
            this.observador = new MutationObserver(() => {
                const antes = this.state.dark;
                this.detectTheme();
                if (this.state.dark !== antes) this.pintarMapa();
            });
            this.observador.observe(document.body,
                { attributes: true, attributeFilter: ["class", "style", "data-color-scheme"] });
        });
        onWillUnmount(() => {
            if (this.observador) this.observador.disconnect();
            if (this.map) { try { this.map.remove(); } catch (e) { /* */ } this.map = null; }
        });
        useEffect(
            () => { this.pintarMapa(); },
            () => [this.state.clientes, this.state.dark]
        );
    }

    // --------------------------------------------------------------- datos
    filtrosActivos() {
        const f = this.state.f;
        const d = {};
        if (f.vendedor) d.vendedor = f.vendedor;
        if (f.zona) d.zona = f.zona;
        if (f.cultivo) d.cultivo = f.cultivo;
        if (f.estatus) d.estatus = f.estatus;
        return d;
    }

    async load() {
        if (!this.state.ultimo) this.state.loading = true;
        this.detectTheme();
        const r = await this.orm.call("res.partner", "foco_vendedores_panel",
            [this.filtrosActivos()]);
        this.state.clientes = r.clientes || [];
        this.state.kpis = r.kpis || {};
        this.state.cat = r.filtros || { zonas: [], cultivos: [], vendedores: [], estatus: [] };
        this.state.estatusLabels = r.estatus_labels || {};
        this.state.estatusColor = r.estatus_color || {};
        // si el cliente seleccionado ya no esta en el resultado, lo soltamos
        if (this.state.sel && !this.state.clientes.some((c) => c.id === this.state.sel.id)) {
            this.state.sel = null;
        }
        this.state.ultimo = new Date();
        this.state.loading = false;
    }

    async refrescar() { await this.load(); }

    setFiltro(key, ev) {
        this.state.f[key] = ev.target.value;
        this.load();
    }
    limpiar() {
        this.state.f = { vendedor: "", zona: "", cultivo: "", estatus: "" };
        this.load();
    }

    get hayFiltros() {
        const f = this.state.f;
        return !!(f.vendedor || f.zona || f.cultivo || f.estatus);
    }

    // --------------------------------------------------------------- tema
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

    // --------------------------------------------------------------- mapa
    async pintarMapa() {
        if (!this.mapEl.el) return;
        let L;
        try { L = await traerLeaflet(); } catch (e) { return; }
        if (!L) return;
        if (!this.map) {
            this.map = L.map(this.mapEl.el, { zoomControl: true, attributionControl: true })
                .setView([27.5, -110.5], 6);   // Sonora, por si no hay puntos
            this.capa = L.layerGroup().addTo(this.map);
        }
        const url = this.state.dark
            ? "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
            : "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}";
        if (this.tile) this.map.removeLayer(this.tile);
        this.tile = L.tileLayer(url, { maxZoom: 18, attribution: "&copy; Esri" }).addTo(this.map);

        this.capa.clearLayers();
        this.marcas = {};
        const bounds = [];
        for (const c of this.state.clientes) {
            if (!c.lat && !c.lng) continue;
            const sel = this.state.sel && this.state.sel.id === c.id;
            const mk = L.circleMarker([c.lat, c.lng], {
                radius: sel ? 11 : 7,
                color: sel ? "#111827" : "#ffffff",
                weight: sel ? 3 : 2,
                fillColor: c.color, fillOpacity: 1,
            }).addTo(this.capa);
            mk.bindTooltip(c.name, { direction: "top", offset: [0, -6] });
            mk.on("click", () => this.seleccionar(c));
            this.marcas[c.id] = mk;
            bounds.push([c.lat, c.lng]);
        }
        if (bounds.length && !this.state.sel) {
            try { this.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 }); } catch (e) { /* */ }
        }
        setTimeout(() => { if (this.map) this.map.invalidateSize(); }, 120);
    }

    seleccionar(c) {
        this.state.sel = c;
        if (this.map && (c.lat || c.lng)) {
            this.map.setView([c.lat, c.lng], Math.max(this.map.getZoom(), 12), { animate: true });
        }
        this.pintarMapa();
    }
    cerrarFicha() { this.state.sel = null; this.pintarMapa(); }

    // --------------------------------------------------------------- acciones de la ficha
    soloDigitos(s) { return (s || "").replace(/[^\d+]/g, ""); }
    telLink(phone) { return "tel:" + this.soloDigitos(phone); }
    waLink(phone) {
        let n = (phone || "").replace(/[^\d]/g, "");
        if (n.length === 10) n = "52" + n;            // Mexico sin lada internacional
        return "https://wa.me/" + n;
    }
    comoLlegar(c) {
        if (!c.lat && !c.lng) return "#";
        return `https://www.google.com/maps/dir/?api=1&destination=${c.lat},${c.lng}`;
    }
    abrirCliente(c) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "res.partner",
            res_id: c.id, views: [[false, "form"]], target: "current",
        });
    }
    abrirOportunidad(op) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "crm.lead",
            res_id: op.id, views: [[false, "form"]], target: "current",
        });
    }

    // --------------------------------------------------------------- formato
    estLabel(k) { return this.state.estatusLabels[k] || k; }
    estColor(k) { return this.state.estatusColor[k] || "#94a3b8"; }
    kpiEstatus(k) { return (this.state.kpis.por_estatus && this.state.kpis.por_estatus[k]) || 0; }
    dinero(v) {
        if (!v) return "";
        try { return new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 }).format(v); }
        catch (e) { return v; }
    }
    fechaCorta(iso) { return iso ? iso.slice(0, 10) : ""; }
    horaUltimo() {
        if (!this.state.ultimo) return "";
        const d = this.state.ultimo;
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }
}

registry.category("actions").add("foco_vendedores", FocoVendedores);
