/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount, useEffect, markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

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
        this.lista = useRef("lista");
        this.input = useRef("input");
        this.file = useRef("file");
        this.state = useState({
            loading: true, dark: true, puede: null, nombre: "",
            threadId: null, titulo: "Conversacion", mensajes: [], hilos: [],
            texto: "", cargando: false, adjuntos: [],
            sidebar: false, colapsada: this.leerColapsada(),
        });
        onWillStart(() => this.cargar());
        onMounted(() => {
            this.detectTheme();
            this.observador = new MutationObserver(() => this.detectTheme());
            this.observador.observe(document.body,
                { attributes: true, attributeFilter: ["class", "style", "data-color-scheme"] });
        });
        useEffect(
            () => { this.bajar(); },
            () => [this.state.mensajes.length, this.state.cargando]
        );
        onWillUnmount(() => this.observador && this.observador.disconnect());
    }

    get sugerencias() { return SUGERENCIAS; }

    img(nombre) { return `${BASE_IMG}/${nombre}.png`; }

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
        const files = Array.from((ev.target && ev.target.files) || []);
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
        if (ev.target) ev.target.value = "";
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
            html: this.aHtml(texto), error: false,
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
        return {
            id: m.id, idReal: typeof m.id === "number", role: m.role,
            texto: m.content || "", html: this.aHtml(m.content || ""),
            error: !!m.error, adjuntos: (m.adjuntos || []).map((a) => ({
                name: a.name, esImagen: a.es_imagen, id: a.id,
                data: a.id ? `/web/image/${a.id}` : null,
            })),
        };
    }

    msgError(texto) {
        return { id: "e" + Date.now(), idReal: false, role: "assistant", texto,
                 html: this.aHtml(texto), error: true, adjuntos: [] };
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
