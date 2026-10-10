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

// El orden en que se lista a la gente: primero quien esta, luego quien se
// callo, luego quien nunca reporto y al final los retirados.
const RANK = { online: 0, silent: 1, never: 2, removed: 3 };
const DIR_TXT = { in: "entrante", out: "saliente", missed: "perdida", rejected: "rechazada", blocked: "bloqueada", other: "otra" };
const SITIO_TXT = { si: "en sitio", lejos: "lejos del cliente", sin_pin: "cliente sin ubicación" };
const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

/** El tablero movil (rediseno del 10-oct-2026, mismo orden que el de PC):
 *  cuatro cifras con su variacion; quien esta en linea, donde estan y que
 *  pide atencion; como uso el telefono cada quien; la linea del dia; la
 *  tabla por departamento; en que se va el telefono; la tendencia. Todo lo
 *  derivado se arma en `derivar()` a partir de las mismas filas que manda
 *  el servidor en un solo viaje. Pantalla de administracion. */
export class FocoMovil extends Component {
    static template = "foco_monitor.Movil";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true, refrescando: false, dark: false,
            days: 1, fecha: this.ymd(new Date()), sel: "all",
            // tal cual del servidor
            kpis: {}, prev: {}, devices: [], markers: [], visitMarkers: [], track: {}, calls: {}, apps: [], linea: null,
            // derivado para dibujar
            equipos: [], online: [], conectados: 0, atencion: [], atencionN: 0,
            barras: [], tl: { horas: [], cols: 1, ahora: null, filas: [] }, grupos: [], appsTop: [],
            // la ficha
            detail: null, open: false, ficha: null, fichaCargando: false,
            ultimo: null,
        });
        this.mapEl = useRef("map");
        this.cDir = useRef("cDir");
        this.cDia = useRef("cDia");
        this.map = null;
        this.capas = null;
        this.tile = null;
        this.graficas = {};
        this.cierre = null;

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
            this.tecla = (ev) => { if (ev.key === "Escape" && this.state.open) this.closeDetail(); };
            window.addEventListener("keydown", this.tecla);
        });
        onWillUnmount(() => {
            clearInterval(this.temporizador);
            if (this.observador) this.observador.disconnect();
            window.removeEventListener("keydown", this.tecla);
            if (this.cierre) clearTimeout(this.cierre);
            this.tirarGraficas();
            if (this.map) { try { this.map.remove(); } catch (e) { /* */ } this.map = null; }
        });
        useEffect(
            () => { this.pintar(); },
            () => [this.state.markers, this.state.visitMarkers, this.state.track, this.state.calls,
                   this.state.dark, this.state.sel, this.state.days, this.state.loading]
        );
    }

    // ------------------------------------------------------------- fechas
    ymd(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    deYmd(s) { const [y, m, d] = (s || "").split("-").map(Number); return new Date(y, m - 1, d); }
    get hoyYmd() { return this.ymd(new Date()); }
    get anclaEsHoy() { return this.state.fecha === this.hoyYmd; }
    get esHoy() { return this.state.days === 1 && this.anclaEsHoy; }
    get periodoLabel() {
        const fin = this.deYmd(this.state.fecha);
        if (this.state.days === 1) {
            const dia = `${DIAS[fin.getDay()]} ${fin.getDate()} de ${MESES[fin.getMonth()]}`;
            return this.anclaEsHoy ? `Hoy, ${dia}` : dia.charAt(0).toUpperCase() + dia.slice(1);
        }
        const ini = new Date(fin); ini.setDate(fin.getDate() - (this.state.days - 1));
        const mismoMes = ini.getMonth() === fin.getMonth();
        return `Del ${ini.getDate()}${mismoMes ? "" : " de " + MESES[ini.getMonth()]} al ${fin.getDate()} de ${MESES[fin.getMonth()]}`;
    }
    cambiarDia(n) {
        const d = this.deYmd(this.state.fecha); d.setDate(d.getDate() + n);
        if (this.ymd(d) > this.hoyYmd) return;
        this.state.fecha = this.ymd(d); this.load();
    }
    setFecha(ev) {
        const v = ev.target.value;
        if (!v || v > this.hoyYmd) { ev.target.value = this.state.fecha; return; }
        this.state.fecha = v; this.load();
    }
    verHoy() {
        if (this.esHoy) return;
        this.state.fecha = this.hoyYmd; this.state.days = 1; this.load();
    }
    setDays(n) { if (n === this.state.days) return; this.state.days = n; this.load(); }

    // -------------------------------------------------------------- datos
    async load() {
        if (!this.state.ultimo) this.state.loading = true;
        this.detectTheme();
        const fin = this.deYmd(this.state.fecha);
        const ini = new Date(fin); ini.setDate(fin.getDate() - (this.state.days - 1));
        const r = await this.orm.call("foco.mobile.device", "dashboard", [this.ymd(ini), this.ymd(fin)]);
        Object.assign(this.state, {
            kpis: r.kpis || {}, prev: r.prev || {}, devices: r.devices || [],
            markers: r.markers || [], visitMarkers: r.visit_markers || [], track: r.track || {},
            calls: r.calls || {}, apps: r.apps || [], linea: r.linea || null,
        });
        this.derivar();
        this.state.ultimo = new Date();
        this.state.loading = false;
    }

    async refrescar() {
        if (this.state.refrescando) return;
        this.state.refrescando = true;
        try { await this.load(); } finally { this.state.refrescando = false; }
    }

    /** Todo lo que dibujan las tarjetas, a partir de las filas del servidor. */
    derivar() {
        const dias = this.state.days;

        // 1) Una fila por telefono con lo que cada tarjeta necesita.
        const equipos = this.state.devices.map((d) => {
            const name = d.employee || d.name || "Sin persona";
            const screen = d.screen_hours || 0, work = d.work_hours || 0;
            let estadoTxt = "nunca reportó";
            if (d.estado === "online") estadoTxt = "en línea";
            else if (d.estado === "silent") estadoTxt = `sin señal ${this.sinceMin(d.since_min)}`;
            else if (d.estado === "removed") estadoTxt = `retirado${d.removed_at ? " el " + d.removed_at : ""}`;
            return {
                ...d, name, devname: d.name || "", hue: this.hue(name), initials: this.initials(name),
                tienePersona: !!d.employee_id, estadoTxt, screen, work,
                otras: Math.max(screen - work, 0),
                workPct: screen > 0 ? Math.round(work / screen * 100) : 0,
                batClase: this.batClase(d.battery),
                saludTxt: d.health === "ok" ? "en orden" : d.health === "unknown" ? "sin datos" : (d.health_issues || ""),
                fixTxt: d.last_fix_min == null ? "sin ubicación" : this.sinceMin(d.last_fix_min),
                rank: RANK[d.estado] ?? 9,
            };
        }).sort((a, b) => a.rank - b.rank || b.screen - a.screen || a.name.localeCompare(b.name));
        this.state.equipos = equipos;

        // 2) Quien esta: por departamento, los que estan arriba.
        const grupos = [];
        for (const e of equipos) {
            const k = e.dept || "Sin departamento";
            let g = grupos.find((x) => x.nombre === k);
            if (!g) { g = { nombre: k, personas: [] }; grupos.push(g); }
            g.personas.push(e);
        }
        this.state.online = grupos;
        this.state.conectados = equipos.filter((e) => e.estado === "online").length;

        // 3) Requiere atencion: una lista corta de cosas con nombre y numero.
        //    Cada renglon abre la pantalla donde se actua.
        const items = [];
        const agrega = (clave, titulo, filas, fmt, accion, tono) => {
            if (!filas.length) return;
            items.push({ clave, titulo, n: filas.length, tono, accion,
                         detalle: filas.slice(0, 4).map(fmt), mas: Math.max(filas.length - 4, 0) });
        };
        const vivos = equipos.filter((e) => e.estado !== "removed");
        agrega("salud_bad", "Teléfono con problema", vivos.filter((e) => e.health === "bad"),
               (e) => `${e.name} · ${e.health_issues}`, "abrirDispositivos", "low");
        agrega("sin_senal", "Sin señal", equipos.filter((e) => e.estado === "silent"),
               (e) => `${e.name} · ${this.sinceMin(e.since_min)}`, "abrirDispositivos", "mid");
        agrega("retirado", "Agente retirado del teléfono", equipos.filter((e) => e.estado === "removed"),
               (e) => `${e.name}${e.removed_reason ? " · " + e.removed_reason : ""}`, "abrirDispositivos", "low");
        agrega("bateria", "Batería baja", vivos.filter((e) => e.bateria_baja),
               (e) => `${e.name} · ${e.battery}%`, null, "mid");
        agrega("visitas_lejos", "Visita lejos del cliente", equipos.filter((e) => e.visitas_lejos),
               (e) => `${e.name} · ${e.visitas_lejos} ${e.visitas_lejos === 1 ? "visita" : "visitas"}`, "abrirVisitas", "mid");
        agrega("salud_warn", "Teléfono que pide un ajuste", vivos.filter((e) => e.health === "warn"),
               (e) => `${e.name} · ${e.health_issues}`, "abrirDispositivos", "mid");
        agrega("sin_persona", "Teléfono sin persona asignada", equipos.filter((e) => !e.tienePersona),
               (e) => e.name, "abrirDispositivos", "info");
        agrega("nunca", "Enrolado, pero nunca ha reportado", equipos.filter((e) => e.estado === "never"),
               (e) => e.name, "abrirDispositivos", "info");
        this.state.atencion = items;
        this.state.atencionN = items.reduce((s, i) => s + i.n, 0);

        // 4) Como uso el telefono cada quien: pantalla partida en apps de
        //    trabajo (registradas) y las demas, contra el que mas la uso.
        const conUso = equipos.filter((e) => e.screen > 0 || e.calls || e.visitas);
        const maxH = Math.max(1, ...conUso.map((e) => e.screen));
        this.state.barras = [...conUso].sort((a, b) => b.screen - a.screen).map((e) => {
            const w = (e.work / maxH * 100), o = (e.otras / maxH * 100);
            const partes = [`${e.calls} ${e.calls === 1 ? "llamada" : "llamadas"}`];
            if (e.calls_minutes) partes.push(this.fmtMinutos(e.calls_minutes));
            if (e.visitas) partes.push(`${e.visitas} ${e.visitas === 1 ? "visita" : "visitas"}`);
            return {
                id: e.id, name: e.name, hue: e.hue, initials: e.initials, employee_id: e.employee_id,
                segs: [{ k: "trabajo", left: "0", width: w.toFixed(2), label: `apps de trabajo ${this.fmt(e.work)}` },
                       { k: "otras", left: w.toFixed(2), width: o.toFixed(2), label: `otras apps ${this.fmt(e.otras)}` }],
                pantalla: this.fmt(e.screen), resumen: partes.join(" · "), pct: e.workPct,
                meter: e.screen === 0 ? "" : e.workPct >= 70 ? "good" : e.workPct >= 40 ? "mid" : "low",
                titulo: `pantalla ${this.fmt(e.screen)} · en apps de trabajo ${this.fmt(e.work)}`,
            };
        });

        // 5) La linea del dia (solo en un dia): pings, llamadas y visitas de
        //    cada telefono ya en porcentaje de la ventana comun.
        const lin = this.state.linea;
        if (lin && dias === 1 && (lin.dias || []).length) {
            const [lo, hi] = lin.ventana || [8, 20];
            const span = Math.max(hi - lo, 1);
            const pos = (t) => (Math.min(Math.max(t, lo), hi) - lo) / span * 100;
            const horas = [];
            const paso = span > 14 ? 2 : 1;
            for (let t = Math.ceil(lo); t <= Math.floor(hi); t += paso) horas.push({ t, label: `${String(t).padStart(2, "0")}:00`, left: pos(t).toFixed(2) });
            const ahora = new Date();
            const nowH = ahora.getHours() + ahora.getMinutes() / 60;
            const porDev = {};
            for (const e of equipos) porDev[e.id] = e;
            this.state.tl = {
                horas, cols: Math.max(Math.floor(hi) - Math.ceil(lo), 1),
                ahora: (this.esHoy && nowH >= lo && nowH <= hi) ? pos(nowH).toFixed(2) : null,
                filas: [...(lin.dias || [])]
                    .sort((a, b) => ((porDev[b.device_id] || {}).screen || 0) - ((porDev[a.device_id] || {}).screen || 0))
                    .map((d) => {
                        const e = porDev[d.device_id] || { id: d.device_id, name: "", hue: 0, initials: "?", employee_id: d.employee_id };
                        return {
                            id: e.id, name: e.name, hue: e.hue, initials: e.initials, employee_id: e.employee_id,
                            pings: (d.pings || []).map((h) => ({
                                left: pos(h).toFixed(2), width: Math.max(5 / 60 / span * 100, 0.35).toFixed(2),
                                titulo: `${this.hhmm(h)} · con señal de ubicación` })),
                            calls: (d.calls || []).map((c) => ({
                                k: c.dir, left: pos(c.a).toFixed(2), width: Math.max((c.b - c.a) / span * 100, 0.45).toFixed(2),
                                titulo: `${this.hhmm(c.a)} · llamada ${DIR_TXT[c.dir] || ""}${c.quien ? " · " + c.quien : ""}`
                                      + (c.dir === "missed" ? "" : ` · ${this.fmtMinutos((c.b - c.a) * 60)}`) })),
                            visitas: (d.visitas || []).map((v) => {
                                const abierta = v.b == null;
                                const b = abierta ? (this.esHoy ? Math.max(Math.min(nowH, hi), v.a + 0.1) : v.a + 0.25) : v.b;
                                return {
                                    abierta, left: pos(v.a).toFixed(2), width: Math.max((b - v.a) / span * 100, 0.6).toFixed(2),
                                    finLeft: abierta ? null : pos(v.b).toFixed(2),
                                    titulo: `${this.hhmm(v.a)}${abierta ? " · en curso" : "–" + this.hhmm(v.b)} · visita a ${v.cliente}`
                                          + (v.en_sitio ? ` · ${SITIO_TXT[v.en_sitio] || v.en_sitio}` : "")
                                          + (v.resultado ? ` · ${v.resultado}` : "") };
                            }),
                        };
                    }),
            };
        } else {
            this.state.tl = { horas: [], cols: 1, ahora: null, filas: [] };
        }

        // 6) La tabla por departamento, con cuantos estan en su cabecera.
        const porDep = {};
        for (const e of equipos) {
            const k = e.dept || "Sin departamento";
            const g = (porDep[k] = porDep[k] || { nombre: k, equipos: [], online: 0, screen: 0 });
            g.equipos.push(e);
            if (e.estado === "online") g.online++;
            g.screen += e.screen;
        }
        this.state.grupos = Object.values(porDep).sort((a, b) => b.online - a.online || b.screen - a.screen);

        // 7) Las apps del telefono como renglones con barra, como en la PC.
        const apps = this.state.apps || [];
        const maxA = Math.max(0.01, ...apps.map((a) => a.hours));
        this.state.appsTop = apps.map((a) => ({ ...a, pct: Math.round(a.hours / maxA * 100) }));
    }

    // ------------------------------------------------------ las cifras
    delta(k) {
        const a = this.state.kpis[k] || 0, b = (this.state.prev || {})[k] || 0;
        return a - b;
    }
    get pantallaDelta() { return this.delta("screen_hours"); }
    get llamadasDelta() { return this.delta("calls"); }
    get visitasDelta() { return this.delta("visitas"); }
    get trabajoPct() {
        const k = this.state.kpis;
        return k.screen_hours > 0 ? Math.round((k.work_hours || 0) / k.screen_hours * 100) : 0;
    }

    // ------------------------------------------------------------- ficha
    /** La fila completa de un telefono a partir de su id: las listas de
     *  "quien esta" y las barras guardan solo lo que dibujan. */
    equipoDe(id) {
        return (this.state.equipos || []).find((e) => e.id === id) || null;
    }

    async openDetail(e) {
        if (!e) return;
        if (this.cierre) { clearTimeout(this.cierre); this.cierre = null; }
        this.state.detail = e;
        this.state.open = false;
        this.state.ficha = null;
        this.state.fichaCargando = true;
        requestAnimationFrame(() => requestAnimationFrame(() => { this.state.open = true; }));
        try {
            const fin = this.deYmd(this.state.fecha);
            const ini = new Date(fin); ini.setDate(fin.getDate() - (this.state.days - 1));
            const f = await this.orm.call("foco.mobile.device", "ficha", [e.id, this.ymd(ini), this.ymd(fin)]);
            if (this.state.detail && this.state.detail.id === e.id) this.state.ficha = f || {};
        } catch (err) {
            this.state.ficha = { error: true };
        } finally {
            this.state.fichaCargando = false;
        }
    }
    closeDetail() {
        this.state.open = false;
        // el panel sale con transicion; se quita del DOM al terminar
        this.cierre = setTimeout(() => { if (!this.state.open) this.state.detail = null; this.cierre = null; }, 320);
    }

    /** Enter o espacio sobre una persona de las tarjetas abre su ficha,
     *  lo mismo que el clic. */
    teclaFicha(ev, e) {
        if (ev.target !== ev.currentTarget) return;
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.openDetail(e); }
    }

    /** Un renglon de «Requiere atencion» abre la pantalla donde se actua. */
    clickAtencion(item) {
        if (item.accion && typeof this[item.accion] === "function") this[item.accion]();
    }
    verLaptop() { this.action.doAction("foco_monitor.foco_dashboard_action"); }
    abrirDispositivos() { this.action.doAction("foco_monitor.foco_mobile_device_action"); }
    abrirVisitas() { this.action.doAction("foco_monitor.foco_visita_action"); }
    abrirApps() { this.action.doAction("foco_monitor.foco_mobile_app_action"); }
    abrirEquipo(e) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "foco.mobile.device",
                               res_id: e.id, views: [[false, "form"]], target: "current" });
    }
    abrirUbicaciones(e) {
        this.action.doAction({ type: "ir.actions.act_window", name: `Ubicaciones de ${e.name}`,
                               res_model: "foco.location", views: [[false, "list"], [false, "form"]],
                               domain: [["device_id", "=", e.id]] });
    }
    abrirCapturas(e) {
        this.action.doAction({ type: "ir.actions.act_window", name: `Capturas de ${e.name}`,
                               res_model: "foco.mobile.capture", views: [[false, "kanban"], [false, "list"], [false, "form"]],
                               domain: [["device_id", "=", e.id]] });
    }
    abrirVisitasDe(e) {
        const dom = e.employee_id
            ? ["|", ["device_id", "=", e.id], ["employee_id", "=", e.employee_id]]
            : [["device_id", "=", e.id]];
        this.action.doAction({ type: "ir.actions.act_window", name: `Visitas de ${e.name}`,
                               res_model: "foco.visita", views: [[false, "list"], [false, "form"]], domain: dom });
    }
    abrirVisita(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "foco.visita",
                               res_id: id, views: [[false, "form"]], target: "current" });
    }
    async pedirCaptura(e) {
        try {
            await this.orm.call("foco.mobile.device", "action_solicitar_captura", [[e.id]]);
            this.notification.add("Captura solicitada. Llegará en el próximo ciclo del teléfono.", { type: "success" });
        } catch (err) {
            this.notification.add("No se pudo pedir la captura.", { type: "danger" });
        }
    }
    verEnMapa(e) {
        this.state.sel = e.id;
        this.closeDetail();
        if (this.mapEl.el) this.mapEl.el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    seleccionar(id) { this.state.sel = (this.state.sel === id) ? "all" : id; }

    // -------------------------------------------------------------- tema
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
            verde: d ? "#22c55e" : "#16a34a", rojo: d ? "#ef4444" : "#dc3545",
            gris: d ? "#64748b" : "#94a3b8", ambar: "#f59e0b", morado: d ? "#a78bfa" : "#8b5cf6",
        };
    }

    // ------------------------------------------------------------- pintar
    async pintar() {
        if (this.state.loading) return;
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
        if (!L || !this.mapEl.el) return;
        if (this.map && this.map.getContainer() !== this.mapEl.el) {
            try { this.map.remove(); } catch (e) { /* */ }
            this.map = null;
        }
        if (!this.map) {
            this.map = L.map(this.mapEl.el, { zoomControl: true, attributionControl: true })
                .setView([23.63, -102.55], 4);   // Mexico, por si no hay puntos
            this.capas = L.layerGroup().addTo(this.map);
            this.tile = null;
        }
        // Teselas de Esri (World Gray Canvas), SIN API key: base gris limpia
        // clara/oscura que deja resaltar el recorrido.
        const url = this.state.dark
            ? "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
            : "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}";
        if (!this.tile || this.tileUrl !== url) {
            if (this.tile) this.map.removeLayer(this.tile);
            this.tile = L.tileLayer(url, { maxZoom: 16, attribution: "&copy; Esri" }).addTo(this.map);
            this.tileUrl = url;
        }

        this.capas.clearLayers();
        const t = this.tinta;
        const bounds = [];
        const sel = this.state.sel;
        // recorridos (una linea por telefono)
        for (const [did, pts] of Object.entries(this.state.track || {})) {
            if (sel !== "all" && String(sel) !== did) continue;
            if (pts && pts.length > 1) {
                L.polyline(pts, { color: t.accent, weight: 3, opacity: 0.65 }).addTo(this.capas);
            }
        }
        // visitas del periodo: un rombo pequeño en el cliente
        for (const v of this.state.visitMarkers || []) {
            if (sel !== "all" && sel !== v.device_id) continue;
            const col = v.en_sitio === "lejos" ? t.ambar : t.morado;
            L.circleMarker([v.lat, v.lon], { radius: 5, color: "#ffffff", weight: 1.5, fillColor: col, fillOpacity: 1 })
                .bindPopup(`<b>${this.esc(v.cliente)}</b><br>${this.esc(v.employee)} · ${this.esc(v.at)}<br>`
                           + `${this.esc(SITIO_TXT[v.en_sitio] || "")}${v.resultado ? " · " + this.esc(v.resultado) : ""}`
                           + `${v.estado === "en_curso" ? " · en curso" : ""}`)
                .addTo(this.capas);
            bounds.push([v.lat, v.lon]);
        }
        // ultima posicion de cada telefono
        for (const m of this.state.markers || []) {
            if (sel !== "all" && sel !== m.id) continue;
            if (!m.lat && !m.lon) continue;
            const col = m.estado === "online" ? t.verde : m.estado === "removed" ? t.rojo : t.gris;
            L.circleMarker([m.lat, m.lon], { radius: 8, color: "#ffffff", weight: 2, fillColor: col, fillOpacity: 1 })
                .bindPopup(`<b>${this.esc(m.employee || m.name || "")}</b><br>${this.esc(m.name || "")}<br>`
                           + `${this.esc(m.at || "")}${m.battery >= 0 ? " · " + m.battery + "%" : ""}`)
                .addTo(this.capas);
            bounds.push([m.lat, m.lon]);
        }
        if (bounds.length) {
            try { this.map.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 }); } catch (e) { /* */ }
        }
        // el contenedor pudo medir 0 al crearse: se recalcula
        setTimeout(() => { if (this.map) this.map.invalidateSize(); }, 120);
    }
    esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

    async pintarGraficas() {
        let Chart;
        try { Chart = await traerChart(); } catch (e) { return; }
        if (!Chart) return;
        this.tirarGraficas();
        const t = this.tinta;
        const tooltip = {
            backgroundColor: t.card, titleColor: t.ink, bodyColor: t.ink2,
            borderColor: t.linea, borderWidth: 1, padding: 10, cornerRadius: 8,
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
                    datasets: [{ data: partes.map((x) => x.v), backgroundColor: partes.map((x) => x.col),
                                 borderColor: t.card, borderWidth: 3, hoverOffset: 6 }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false, cutout: "66%",
                    animation: { duration: 420, easing: "easeOutQuart" },
                    plugins: {
                        legend: { display: true, position: "bottom",
                                  labels: { color: t.ink2, boxWidth: 10, boxHeight: 10, usePointStyle: true,
                                            pointStyle: "circle", font: { size: 11 }, padding: 12 } },
                        tooltip: { ...tooltip, callbacks: { label: (x) => ` ${x.label}: ${x.parsed}` } },
                    },
                },
                plugins: [centro],
            });
        }

        // 2) Llamadas por dia (barras), solo en rangos
        if (this.cDia.el) {
            const dias = (this.state.calls && this.state.calls.by_day) || [];
            this.graficas.dia = new Chart(this.cDia.el, {
                type: "bar",
                data: {
                    labels: dias.map((d) => this.ejeDia(d.dia)),
                    datasets: [{ label: "Llamadas", data: dias.map((d) => d.n),
                                 backgroundColor: t.accent, borderRadius: 5, maxBarThickness: 30 }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    animation: { duration: 420, easing: "easeOutQuart" },
                    plugins: { legend: { display: false }, tooltip },
                    scales: {
                        x: { grid: { display: false }, border: { color: t.linea },
                             ticks: { color: t.ink3, font: { size: 10.5 }, maxRotation: 0, autoSkipPadding: 12 } },
                        y: { grid: { color: t.linea, drawTicks: false }, border: { display: false },
                             ticks: { color: t.ink3, font: { size: 10.5 }, precision: 0 } },
                    },
                },
            });
        }
    }

    // ------------------------------------------------------------ utiles
    batClase(p) { return p >= 50 ? "good" : (p >= 20 ? "mid" : "low"); }
    batPct(p) { return Math.max(0, Math.min(100, p || 0)); }
    ejeDia(iso) {
        if (!iso) return "";
        const [y, m, d] = iso.split("-").map(Number);
        const f = new Date(y, m - 1, d);
        const dd = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
        return `${dd[f.getDay()]} ${f.getDate()}`;
    }
    corta(s, n = 46) { s = s || ""; return s.length > n ? s.slice(0, n - 2) + "…" : s; }
    horaUltimo() {
        if (!this.state.ultimo) return "";
        const d = this.state.ultimo;
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }
    initials(n) { return (n || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase(); }
    hue(n) { let h = 0; for (const ch of (n || "")) h = (h * 31 + ch.charCodeAt(0)) % 360; return h; }
    fmt(h) { const m = Math.round((h || 0) * 60); return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`; }
    fmtDelta(h) {
        const m = Math.round(Math.abs(h || 0) * 60);
        return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
    }
    fmtMinutos(m) {
        m = Math.round(m || 0);
        if (m < 60) return `${m} min`;
        return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
    }
    hhmm(x) {
        const m = Math.round((x || 0) * 60);
        return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    }
    sinceMin(m) {
        if (m == null) return "";
        if (m < 1) return "recién";
        if (m < 60) return `hace ${m} min`;
        if (m < 1440) return `hace ${Math.round(m / 60)} h`;
        return `hace ${Math.round(m / 1440)} d`;
    }
    dirTxt(k) { return DIR_TXT[k] || "otra"; }
    sitioTxt(k) { return SITIO_TXT[k] || ""; }
}

registry.category("actions").add("foco_movil", FocoMovil);
