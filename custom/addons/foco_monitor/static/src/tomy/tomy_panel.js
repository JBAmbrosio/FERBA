/** @odoo-module **/

import { Component, useState, useRef, onWillUnmount, useEffect, markup } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";

// Preguntas de arranque: las que un administrador hace a diario. Son texto
// que se manda tal cual; Tomy resuelve fechas y personas del lado del servidor.
const CHIPS = [
    "¿Qué hizo cada quien hoy?",
    "Resumen de la semana",
    "Top distracciones del equipo",
    "¿Quién tuvo más llamadas de trabajo?",
    "Ausencias pendientes de justificar",
];

// Cuanto se queda la pose de reaccion antes de volver a saludar.
const POSE_MS = 6000;

export class TomyPanel extends Component {
    static template = "foco_monitor.TomyPanel";
    static props = {
        dark: { type: Boolean, optional: true },
        rango: { optional: true },
        days: { optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            abierto: false, listo: false, cargando: false,
            texto: "", mensajes: [], threadId: null, pose: "saluda", nombre: "",
        });
        this.lista = useRef("lista");
        this.input = useRef("input");
        this.graficas = {};
        this._poseTimer = null;
        // Las graficas se dibujan DESPUES de que el mensaje existe en el DOM,
        // y se redibujan enteras si cambia el tema: Chart.js pinta la letra
        // con el color que tenia al crearse, no con el CSS. El scroll al
        // ultimo mensaje va aqui por la misma razon: OWL pinta en su propio
        // frame, y un requestAnimationFrame desde el handler llega antes.
        useEffect(
            () => { this.bajar(); this.dibujar(); },
            () => [this.state.mensajes.length, this.props.dark, this.state.abierto, this.state.cargando]
        );
        onWillUnmount(() => { this.tirar(); clearTimeout(this._poseTimer); });
    }

    get chips() { return CHIPS; }

    img(pose) { return `/foco_monitor/static/src/tomy/img/tomy_${pose}.png`; }

    // ------------------------------------------------------------ hilo
    async abrir() {
        this.state.abierto = true;
        this.pose("saluda");
        if (!this.state.listo) await this.cargarHilo(false);
        this.bajar();
        setTimeout(() => this.input.el && this.input.el.focus(), 260);
    }

    cerrar() { this.state.abierto = false; }

    async nueva() {
        if (this.state.cargando) return;
        await this.cargarHilo(true);
        this.pose("saluda");
        setTimeout(() => this.input.el && this.input.el.focus(), 50);
    }

    async cargarHilo(nuevo) {
        try {
            const r = await this.orm.call("foco.tomy.thread", "abrir", [nuevo]);
            this.tirar();
            this.state.threadId = r.thread_id;
            this.state.nombre = r.nombre || "";
            this.state.mensajes = (r.mensajes || []).map((m) => this.preparar(m));
        } catch (e) {
            this.state.mensajes = [this.mensajeError("No pude abrir la conversación: " + (e.message || e))];
        }
        this.state.listo = true;
        this.bajar();
    }

    preparar(m) {
        return {
            id: m.id, idReal: typeof m.id === "number", role: m.role,
            texto: m.content || "", html: this.aHtml(m.content || ""),
            artefactos: m.artefactos || [], fuentes: m.fuentes || [],
            fuera: !!m.fuera, error: !!m.error && !m.content, feedback: m.feedback || false,
            verFuentes: false,
        };
    }

    mensajeError(texto) {
        return { id: "e" + Date.now(), idReal: false, role: "assistant", texto, html: this.aHtml(texto),
                 artefactos: [], fuentes: [], fuera: false, error: true, feedback: false, verFuentes: false };
    }

    // ------------------------------------------------------------ preguntar
    tecla(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); this.enviar(); }
        if (ev.key === "Escape") this.cerrar();
    }

    async enviar(texto) {
        texto = (typeof texto === "string" ? texto : this.state.texto).trim();
        if (!texto || this.state.cargando) return;
        this.state.texto = "";
        this.state.mensajes.push({
            id: "u" + Date.now(), idReal: false, role: "user", texto, html: this.aHtml(texto),
            artefactos: [], fuentes: [], fuera: false, error: false, feedback: false, verFuentes: false,
        });
        this.state.cargando = true;
        this.pose("piensa");
        this.bajar();
        const ctx = {};
        if (this.props.rango && this.props.rango.length === 2) {
            ctx.desde = this.props.rango[0]; ctx.hasta = this.props.rango[1]; ctx.dias = this.props.days;
        }
        try {
            const r = await this.orm.call("foco.tomy.thread", "preguntar", [this.state.threadId, texto, ctx]);
            this.state.threadId = r.thread_id;
            this.state.mensajes.push(this.preparar(r));
            const conGrafica = (r.artefactos || []).some((a) => a.tipo === "grafica");
            this.pose(r.fuera || r.error ? "sorpresa" : conGrafica ? "rie" : "guino", POSE_MS);
        } catch (e) {
            // El error se muestra en la conversacion, no en el dialogo rojo de
            // Odoo: es parte de la platica, no una falla de la pantalla.
            this.state.mensajes.push(this.mensajeError("No pude contestar: " + (e.message || e)));
            this.pose("sorpresa", POSE_MS);
        } finally {
            this.state.cargando = false;
            this.bajar();
            setTimeout(() => this.input.el && this.input.el.focus(), 50);
        }
    }

    pose(p, volver) {
        clearTimeout(this._poseTimer);
        this.state.pose = p;
        if (volver) this._poseTimer = setTimeout(() => { this.state.pose = "saluda"; }, volver);
    }

    bajar() {
        requestAnimationFrame(() => {
            const el = this.lista.el;
            if (el) el.scrollTop = el.scrollHeight;
        });
    }

    toggleFuentes(m) { m.verFuentes = !m.verFuentes; }

    argumentos(f) {
        try { return JSON.stringify(f.argumentos || {}); } catch { return ""; }
    }

    abrirLiga(a) {
        if (!a || !a.action) return;
        this.action.doAction(a.action, { additionalContext: a.context || {} });
    }

    async feedback(m, v) {
        if (!m.idReal) return;
        m.feedback = m.feedback === v ? false : v;
        try { await this.orm.write("foco.tomy.message", [m.id], { feedback: m.feedback }); } catch { /* opinion perdida, nada mas */ }
    }

    imprimir(m) {
        if (!m.idReal) return;
        window.open(`/foco/tomy/imprimir/${m.id}`, "_blank");
    }

    // ------------------------------------------------------------ texto -> html
    /** Texto plano de Tomy (parrafos, guiones, **negritas**) a HTML escapado. */
    aHtml(txt) {
        const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
        let html = "", enLista = false, parrafo = [];
        const cerrarP = () => { if (parrafo.length) { html += `<p>${parrafo.join("<br/>")}</p>`; parrafo = []; } };
        const cerrarL = () => { if (enLista) { html += "</ul>"; enLista = false; } };
        for (const l of (txt || "").split(/\r?\n/)) {
            const t = l.trim();
            if (!t) { cerrarP(); cerrarL(); continue; }
            const li = t.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
            if (li) { cerrarP(); if (!enLista) { html += "<ul>"; enLista = true; } html += `<li>${inline(li[1])}</li>`; continue; }
            const h = t.match(/^#{1,4}\s+(.*)$/);
            if (h) { cerrarP(); cerrarL(); html += `<h4>${inline(h[1])}</h4>`; continue; }
            cerrarL();
            parrafo.push(inline(t));
        }
        cerrarP(); cerrarL();
        return markup(html);
    }

    // ------------------------------------------------------------ graficas
    get tinta() {
        const d = !!this.props.dark;
        return {
            ink: d ? "#e9edf4" : "#0f1520", ink2: d ? "#aab4c4" : "#5a6577", ink3: d ? "#7c8797" : "#8b95a6",
            linea: d ? "#2b3442" : "#e7eaf0", card: d ? "#171c27" : "#ffffff",
            paleta: d ? ["#6b9bff", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#38bdf8"]
                      : ["#2f6fed", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#0ea5e9"],
        };
    }

    tirar() {
        for (const k of Object.keys(this.graficas)) {
            try { this.graficas[k].destroy(); } catch { /* ya no existe */ }
            delete this.graficas[k];
        }
    }

    async dibujar() {
        if (!this.state.abierto) return;
        const pendientes = [];
        for (const m of this.state.mensajes) {
            (m.artefactos || []).forEach((a, i) => { if (a.tipo === "grafica") pendientes.push([m, a, i]); });
        }
        this.tirar();
        if (!pendientes.length) return;
        let Chart;
        try { await loadBundle("web.chartjs_lib"); Chart = window.Chart; } catch { return; }
        if (!Chart) return;
        for (const [m, a, i] of pendientes) {
            const id = `tomy_c_${m.id}_${i}`;
            const el = document.getElementById(id);
            if (!el) continue;
            try { this.graficas[id] = new Chart(el, this.config(a)); } catch { /* datos raros: sin grafica */ }
        }
    }

    config(a) {
        const t = this.tinta;
        const tipo = a.grafica === "lineas" ? "line" : a.grafica === "dona" ? "doughnut" : "bar";
        const series = a.series || [];
        const datasets = series.map((s, i) => {
            const col = t.paleta[i % t.paleta.length];
            if (tipo === "doughnut") {
                return { label: s.nombre, data: s.valores,
                         backgroundColor: (a.etiquetas || []).map((_, j) => t.paleta[j % t.paleta.length]),
                         borderColor: t.card, borderWidth: 2, hoverOffset: 5 };
            }
            return { label: s.nombre, data: s.valores, borderColor: col,
                     backgroundColor: tipo === "line" ? "transparent" : col,
                     borderWidth: 2, tension: 0.3, pointRadius: 2.5, borderRadius: 4, maxBarThickness: 26 };
        });
        const options = {
            responsive: true, maintainAspectRatio: false,
            animation: { duration: 380, easing: "easeOutQuart" },
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: { display: series.length > 1 || tipo === "doughnut", position: "bottom",
                          labels: { color: t.ink2, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "circle", font: { size: 11 } } },
                tooltip: { backgroundColor: t.card, titleColor: t.ink, bodyColor: t.ink2,
                           borderColor: t.linea, borderWidth: 1, padding: 8, cornerRadius: 8 },
            },
        };
        if (tipo !== "doughnut") {
            options.scales = {
                x: { grid: { display: false }, border: { color: t.linea },
                     ticks: { color: t.ink3, font: { size: 10.5 }, maxRotation: 0, autoSkip: true } },
                y: { beginAtZero: true, grid: { color: t.linea, drawTicks: false }, border: { display: false },
                     ticks: { color: t.ink3, font: { size: 10.5 }, callback: (v) => `${v}${a.unidad || ""}` } },
            };
        }
        return { type: tipo, data: { labels: a.etiquetas || [], datasets }, options };
    }
}
