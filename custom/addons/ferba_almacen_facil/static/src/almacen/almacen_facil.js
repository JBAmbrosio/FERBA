/** @odoo-module **/

import { Component, useState, useRef, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const MODEL = "ferba.almacen.facil";

export class AlmacenFacil extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            screen: "inicio",
            modo: "recibir",
            loading: true,
            resumen: { usuario: "", recepciones: 0, entregas: 0 },
            lista: [],
            detalle: null,
            busqueda: "",
            resultados: null,
            enviando: false,
        });
        this.firmaRef = useRef("firma");
        this._dibujando = false;
        this._firmado = false;
        onWillStart(() => this.cargarInicio());
    }

    // ---------------------------------------------------------------- inicio
    async cargarInicio() {
        this.state.loading = true;
        this.state.screen = "inicio";
        try {
            this.state.resumen = await this.orm.call(MODEL, "resumen", []);
        } catch (e) { this._err(e); }
        this.state.loading = false;
    }

    // ---------------------------------------------------------------- lista
    async abrirLista(modo) {
        this.state.modo = modo || "recibir";
        this.state.screen = "lista";
        this.state.loading = true;
        try {
            const metodo = this.state.modo === "entregar" ? "lista_entregas" : "lista_recepciones";
            this.state.lista = await this.orm.call(MODEL, metodo, []);
        } catch (e) { this._err(e); }
        this.state.loading = false;
    }

    // ---------------------------------------------------------------- detalle
    async abrirDetalle(id) {
        this.state.screen = "detalle";
        this.state.loading = true;
        this._firmado = false;
        try {
            this.state.detalle = await this.orm.call(MODEL, "detalle", [id]);
        } catch (e) { this._err(e); }
        this.state.loading = false;
    }

    volver() {
        if (this.state.screen === "detalle") { this.abrirLista(this.state.modo); }
        else { this.cargarInicio(); }
    }

    // ---------------------------------------------------------- recibir/entregar
    async procesar() {
        if (this.state.enviando || !this.state.detalle) { return; }
        this.state.enviando = true;
        let firma = null;
        const c = this.firmaRef.el;
        if (c && this._firmado) { firma = c.toDataURL("image/png"); }
        try {
            const metodo = this.state.modo === "entregar" ? "entregar" : "recibir";
            const r = await this.orm.call(MODEL, metodo, [this.state.detalle.id, firma]);
            if (r && r.ok) {
                this.notification.add("✅ " + (r.msg || "Listo"), { type: "success" });
                await this.abrirLista(this.state.modo);
                this.orm.call(MODEL, "resumen", []).then((s) => { this.state.resumen = s; });
            } else {
                this.notification.add("⚠️ " + ((r && r.msg) || "No se pudo completar"), { type: "danger", sticky: true });
            }
        } catch (e) { this._err(e); }
        this.state.enviando = false;
    }

    // ---------------------------------------------------------------- buscar
    irBuscar() {
        this.state.screen = "buscar";
        this.state.resultados = null;
        this.state.busqueda = "";
    }

    async buscar(ev) {
        const texto = ev.target.value;
        this.state.busqueda = texto;
        if (!texto || texto.trim().length < 2) { this.state.resultados = null; return; }
        try {
            this.state.resultados = await this.orm.call(MODEL, "buscar", [texto]);
        } catch (e) { this._err(e); }
    }

    // ---------------------------------------------------------------- firma
    _pos(ev) {
        const c = this.firmaRef.el;
        const r = c.getBoundingClientRect();
        return { x: (ev.clientX - r.left) * (c.width / r.width), y: (ev.clientY - r.top) * (c.height / r.height) };
    }
    firmaInicio(ev) {
        ev.preventDefault();
        this._dibujando = true;
        const ctx = this.firmaRef.el.getContext("2d");
        const p = this._pos(ev);
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
    }
    firmaMueve(ev) {
        if (!this._dibujando) { return; }
        ev.preventDefault();
        const ctx = this.firmaRef.el.getContext("2d");
        const p = this._pos(ev);
        ctx.lineWidth = 2.5;
        ctx.lineCap = "round";
        ctx.strokeStyle = "#111827";
        ctx.lineTo(p.x, p.y);
        ctx.stroke();
        this._firmado = true;
    }
    firmaFin() { this._dibujando = false; }
    firmaLimpia() {
        const c = this.firmaRef.el;
        if (c) { c.getContext("2d").clearRect(0, 0, c.width, c.height); this._firmado = false; }
    }

    // ---------------------------------------------------------------- helpers
    color(dias) { return dias >= 5 ? "rojo" : (dias >= 2 ? "ambar" : "verde"); }
    textoDias(dias) {
        if (dias <= 0) { return "hoy"; }
        if (dias === 1) { return "ayer"; }
        return "hace " + dias + " días";
    }
    _err(e) { this.notification.add((e && e.message) || String(e), { type: "danger", sticky: true }); }
}

AlmacenFacil.template = "ferba_almacen_facil.App";
registry.category("actions").add("ferba_almacen_facil", AlmacenFacil);
