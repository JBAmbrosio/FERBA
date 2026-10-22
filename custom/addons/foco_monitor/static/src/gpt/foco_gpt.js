/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount, useEffect, markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";

// Tommy GPT: el asistente de IA de uso general. Pantalla de chat con la misma
// apariencia que ChatGPT: barra lateral oscura con el historial de
// conversaciones que se repliega, y la platica a la derecha. Montado sobre la
// misma API de Claude que ya usa Foco. Se pueden adjuntar documentos e
// imagenes: el servidor los manda como bloques de contenido a Claude. El avatar
// es un tomate con audifonos que cambia segun la situacion.

const SUGERENCIAS = [
    "Redacta un correo formal para un cliente",
    "Resume este documento en 5 puntos",
    "Explícame esto de forma sencilla",
    "Revisa la ortografía y mejora este texto",
];

const MAX_MB = 12;                       // tope por archivo (el servidor tambien valida)
const LS_COLAPSADA = "fgpt_colapsada";   // recuerda si la barra quedo replegada
const BASE_IMG = "/foco_monitor/static/src/gpt/img";

export class FocoGpt extends Component {
    static template = "foco_monitor.FocoGpt";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.lista = useRef("lista");
        this.input = useRef("input");
        this.file = useRef("file");
        this.state = useState({
            loading: true, dark: true, puede: null, nombre: "",
            threadId: null, titulo: "Conversacion", mensajes: [], hilos: [],
            texto: "", cargando: false, adjuntos: [],
            sidebar: false, colapsada: this.leerColapsada(), arrastrando: false,
        });
        this._dragN = 0;   // profundidad de dragenter/leave (evita el parpadeo)
        this.graficas = {};   // instancias de Chart.js por mensaje
        onWillStart(() => this.cargar());
        onMounted(() => {
            this.detectTheme();
            this.observador = new MutationObserver(() => this.detectTheme());
            this.observador.observe(document.body,
                { attributes: true, attributeFilter: ["class", "style", "data-color-scheme"] });
        });
        // Las graficas se dibujan DESPUES de que el mensaje existe en el DOM, y se
        // redibujan si cambia el tema (Chart.js fija el color al crearse).
        useEffect(
            () => { this.bajar(); this.dibujar(); },
            () => [this.state.mensajes.length, this.state.cargando, this.state.dark]
        );
        onWillUnmount(() => { this.observador && this.observador.disconnect(); this.tirar(); });
    }

    get sugerencias() { return SUGERENCIAS; }

    img(nombre) { return `${BASE_IMG}/${nombre}.png`; }

    // Volver al menu principal de Odoo (la rejilla de aplicaciones). La pantalla
    // ocupa toda la ventana, asi que hace falta una salida clara.
    salir() { window.location.assign("/odoo"); }

    // Avatar del tomate segun la situacion.
    get avatarHero() { return this.img("tomy_saluda"); }
    get avatarBot() { return this.img("tomy_idle"); }
    get avatarPiensa() { return this.img("tomy_piensa"); }

    // ----------------------------------------------------------- barra
    leerColapsada() {
        try { return localStorage.getItem(LS_COLAPSADA) === "1"; } catch { return false; }
    }
    toggleColapsar() {
        this.state.colapsada = !this.state.colapsada;
        try { localStorage.setItem(LS_COLAPSADA, this.state.colapsada ? "1" : "0"); } catch { /* sin memoria, da igual */ }
    }
    // El boton de la cabecera de la barra: en escritorio repliega; en movil
    // (la barra es un panel flotante) la cierra.
    onToggleBarra() {
        if (window.innerWidth <= 820) { this.state.sidebar = false; }
        else { this.toggleColapsar(); }
    }

    // ----------------------------------------------------------- datos
    async cargar() {
        try {
            const r = await this.orm.call("foco.gpt.thread", "abrir", [false]);
            if (!r.puede) { this.state.puede = false; this.state.loading = false; return; }
            this.state.puede = true;
            this.state.nombre = r.nombre || "";
            this.state.hilos = r.hilos || [];
            this.aplicarHilo(r);
        } catch (e) {
            this.state.puede = true;
            this.state.mensajes = [this.msgError("No pude abrir el asistente: " + (e.message || e))];
        }
        this.state.loading = false;
    }

    aplicarHilo(r) {
        this.state.threadId = r.thread_id;
        this.state.titulo = r.titulo || "Conversacion";
        this.state.mensajes = (r.mensajes || []).map((m) => this.preparar(m));
        this.bajar();
    }

    async nuevo() {
        if (this.state.cargando) return;
        try {
            const r = await this.orm.call("foco.gpt.thread", "nuevo_hilo", []);
            this.state.hilos = r.hilos || this.state.hilos;
            this.state.adjuntos = [];
            this.aplicarHilo(r);
            this.state.sidebar = false;
            this.foco();
        } catch (e) {
            this.notificar(e);
        }
    }

    async abrirHilo(id) {
        if (this.state.cargando || id === this.state.threadId) { this.state.sidebar = false; return; }
        try {
            const r = await this.orm.call("foco.gpt.thread", "cargar", [id]);
            this.state.adjuntos = [];
            this.aplicarHilo(r);
            this.state.sidebar = false;
        } catch (e) {
            this.notificar(e);
        }
    }

    async borrarHilo(id, ev) {
        if (ev) ev.stopPropagation();
        if (!window.confirm("¿Borrar esta conversación? No se puede deshacer.")) return;
        try {
            const r = await this.orm.call("foco.gpt.thread", "borrar", [id]);
            this.state.hilos = r.hilos || [];
            if (id === this.state.threadId) {
                if (this.state.hilos.length) await this.abrirHilo(this.state.hilos[0].id);
                else await this.nuevo();
            }
        } catch (e) {
            this.notificar(e);
        }
    }

    // ----------------------------------------------------------- adjuntos
    pedirArchivo() { if (this.file.el) this.file.el.click(); }

    async onFile(ev) {
        await this.agregarArchivos((ev.target && ev.target.files) || []);
        if (ev.target) ev.target.value = "";
    }

    async agregarArchivos(fileList) {
        const files = Array.from(fileList || []);
        for (const f of files) {
            if (f.size > MAX_MB * 1024 * 1024) {
                this.notificar(new Error(`"${f.name}" pesa más de ${MAX_MB} MB.`));
                continue;
            }
            try {
                const data = await this.leerArchivo(f);
                this.state.adjuntos.push({
                    name: f.name, mime: f.type || "", data, size: f.size,
                    esImagen: (f.type || "").startsWith("image/"),
                });
            } catch (e) {
                this.notificar(new Error(`No pude leer "${f.name}".`));
            }
        }
    }

    // ---------- arrastrar y soltar (como ChatGPT) ----------
    traeArchivos(ev) {
        try { return Array.from((ev.dataTransfer && ev.dataTransfer.types) || []).includes("Files"); }
        catch { return false; }
    }
    onDragEnter(ev) {
        if (!this.traeArchivos(ev)) return;
        ev.preventDefault();
        this._dragN += 1;
        this.state.arrastrando = true;
    }
    onDragOver(ev) {
        if (!this.traeArchivos(ev)) return;
        ev.preventDefault();
        if (ev.dataTransfer) ev.dataTransfer.dropEffect = "copy";
    }
    onDragLeave(ev) {
        if (!this.traeArchivos(ev)) return;
        this._dragN -= 1;
        if (this._dragN <= 0) { this._dragN = 0; this.state.arrastrando = false; }
    }
    async onDrop(ev) {
        if (!this.traeArchivos(ev)) return;
        ev.preventDefault();
        this._dragN = 0;
        this.state.arrastrando = false;
        await this.agregarArchivos(ev.dataTransfer && ev.dataTransfer.files);
        this.foco();
    }

    leerArchivo(f) {
        return new Promise((resolve, reject) => {
            const fr = new FileReader();
            fr.onload = () => resolve(fr.result);   // data:...;base64,xxxx
            fr.onerror = () => reject(fr.error);
            fr.readAsDataURL(f);
        });
    }

    quitarAdjunto(i) { this.state.adjuntos.splice(i, 1); }

    // ----------------------------------------------------------- enviar
    tecla(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); this.enviar(); }
    }

    usarSugerencia(s) {
        this.state.texto = s;
        this.foco();
    }

    async enviar() {
        const texto = (this.state.texto || "").trim();
        const adjuntos = this.state.adjuntos.slice();
        if ((!texto && !adjuntos.length) || this.state.cargando) return;

        this.state.mensajes.push({
            id: "u" + Date.now(), idReal: false, role: "user", texto,
            html: this.aHtml(texto), error: false, artefactos: [], puedeExportar: false,
            adjuntos: adjuntos.map((a) => ({ name: a.name, esImagen: a.esImagen, data: a.data, id: null })),
        });
        this.state.texto = "";
        this.state.adjuntos = [];
        this.state.cargando = true;
        this.bajar();

        const carga = adjuntos.map((a) => ({ name: a.name, mime: a.mime, data: a.data }));
        try {
            const r = await this.orm.call("foco.gpt.thread", "preguntar",
                [this.state.threadId, texto, carga]);
            this.state.threadId = r.thread_id;
            if (r.titulo) this.state.titulo = r.titulo;
            if (r.hilos) this.state.hilos = r.hilos;
            this.state.mensajes.push(this.preparar(r));
        } catch (e) {
            this.state.mensajes.push(this.msgError("No pude contestar: " + (e.message || e)));
        } finally {
            this.state.cargando = false;
            this.bajar();
            this.foco();
        }
    }

    // ----------------------------------------------------------- utilidades
    preparar(m) {
        let ti = 0;
        const artefactos = (m.artefactos || []).map((a) => {
            if (a.tipo === "tabla") return { ...a, tablaIdx: ti++ };
            return a;
        });
        const tieneArte = artefactos.length > 0;
        return {
            id: m.id, idReal: typeof m.id === "number", role: m.role,
            texto: m.content || "", html: this.aHtml(m.content || ""),
            error: !!m.error, artefactos,
            // los botones de descarga solo tienen sentido en respuestas con algo que bajar
            puedeExportar: typeof m.id === "number" && m.role !== "user"
                && (!!(m.content || "").trim() || tieneArte),
            adjuntos: (m.adjuntos || []).map((a) => ({
                name: a.name, esImagen: a.es_imagen, id: a.id,
                data: a.id ? `/web/image/${a.id}` : null,
            })),
        };
    }

    // ----------------------------------------------------------- graficas y descargas
    get tinta() {
        const d = !!this.state.dark;
        return {
            ink2: d ? "#aab4c4" : "#5a6577", ink3: d ? "#7c8797" : "#8b95a6",
            linea: d ? "#2b3442" : "#e7eaf0", card: d ? "#1b2230" : "#ffffff",
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
            const el = document.getElementById(`fgpt_c_${m.id}_${i}`);
            if (!el) continue;
            try { this.graficas[`${m.id}_${i}`] = new Chart(el, this.config(a)); } catch { /* datos raros */ }
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
                     borderWidth: 2, tension: 0.3, pointRadius: 2.5, borderRadius: 4, maxBarThickness: 34 };
        });
        const options = {
            responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
            plugins: {
                legend: { display: series.length > 1 || tipo === "doughnut", position: "bottom",
                          labels: { color: t.ink2, boxWidth: 10, usePointStyle: true, font: { size: 11 } } },
            },
        };
        if (tipo !== "doughnut") {
            options.scales = {
                x: { grid: { display: false }, ticks: { color: t.ink3, font: { size: 10.5 } } },
                y: { beginAtZero: true, grid: { color: t.linea }, border: { display: false },
                     ticks: { color: t.ink3, font: { size: 10.5 }, callback: (v) => `${v}${a.unidad || ""}` } },
            };
        }
        return { type: tipo, data: { labels: a.etiquetas || [], datasets }, options };
    }

    exportar(m, fmt) {
        if (!m.idReal) return;
        const url = fmt === "word" ? `/foco/gpt/word/${m.id}` : `/foco/gpt/imprimir/${m.id}`;
        window.open(url, "_blank");
    }

    descargarExcel(m, idx) {
        if (!m.idReal) return;
        window.open(`/foco/gpt/excel/${m.id}/${idx}`, "_blank");
    }

    // Tamano legible de un archivo generado (para la tarjeta de descarga).
    pesoArchivo(bytes) {
        const b = Number(bytes) || 0;
        if (b < 1024) return `${b} B`;
        if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
        return `${(b / (1024 * 1024)).toFixed(1)} MB`;
    }

    msgError(texto) {
        return { id: "e" + Date.now(), idReal: false, role: "assistant", texto,
                 html: this.aHtml(texto), error: true, adjuntos: [], artefactos: [], puedeExportar: false };
    }

    notificar(e) {
        this.state.mensajes.push(this.msgError((e && (e.message || e)) || "Algo salió mal."));
        this.bajar();
    }

    foco() { setTimeout(() => this.input.el && this.input.el.focus(), 50); }

    bajar() {
        requestAnimationFrame(() => {
            const el = this.lista.el;
            if (el) el.scrollTop = el.scrollHeight;
        });
    }

    detectTheme() {
        const leer = (el) => {
            if (!el) return null;
            const m = getComputedStyle(el).backgroundColor.match(/[\d.]+/g);
            if (!m || m.length < 3) return null;
            if (m.length === 4 && parseFloat(m[3]) === 0) return null;
            return (0.299 * +m[0] + 0.587 * +m[1] + 0.114 * +m[2]) / 255 < 0.5;
        };
        let v = leer(document.body);
        if (v === null) v = leer(document.documentElement);
        if (v === null) v = !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
        this.state.dark = v;
    }

    // Texto plano (parrafos, guiones, **negritas**) a HTML escapado.
    aHtml(txt) {
        const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const inline = (s) => esc(s)
            .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
            .replace(/`([^`]+)`/g, "<code>$1</code>");
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
}

registry.category("actions").add("foco_gpt", FocoGpt);
