/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

// La Play Store administrada de la empresa, DENTRO de Odoo.
//
// Es el iframe oficial de Google (managed Google Play): se busca en la Play
// Store de verdad y, al elegir una app, Google avisa por `onproductselect`.
// Odoo decide que hacer con lo elegido segun de donde se abrio la pantalla:
// una solicitud (se aprueba para esa persona), un perfil (para todo el
// perfil), un telefono (se le instala) o nada (entra al catalogo).
//
// El iframe solo se abre con un token de un solo uso que pide el servidor;
// sin la empresa registrada en Google, la pantalla dice que falta.
const API_GOOGLE = "https://apis.google.com/js/api.js";

export class FocoPlay extends Component {
    static template = "foco_monitor.Play";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notif = useService("notification");
        this.marco = useRef("marco");
        const ctx = (this.props.action && this.props.action.context) || {};
        this.ctx = {
            request_id: ctx.foco_request_id || false,
            policy_id: ctx.foco_policy_id || false,
            device_id: ctx.foco_device_id || false,
            install_type: ctx.foco_install_type || false,
        };
        this.state = useState({
            cargando: true, error: "", estado: "", destino: "", elegidas: [], ocupado: false,
        });
        this.vivo = true;
        onMounted(() => this.abrir());
        onWillUnmount(() => { this.vivo = false; });
    }

    async abrir() {
        let r;
        try {
            r = await this.orm.call("foco.mobile.app", "play_ficha", [this.ctx]);
        } catch (e) {
            this.fallar((e.data && e.data.message) || e.message || "No se pudo abrir la Play Store.");
            return;
        }
        if (!this.vivo) return;
        if (r.error) {
            this.state.estado = r.estado || "";
            this.fallar(r.error);
            return;
        }
        this.state.destino = r.destino || "";
        try {
            await loadJS(API_GOOGLE);
        } catch {
            this.fallar("No se pudo cargar el componente de Google (apis.google.com). Revisa la conexión del navegador.");
            return;
        }
        if (!this.vivo || !window.gapi) return;
        window.gapi.load("gapi.iframes", () => {
            if (!this.vivo || !this.marco.el) return;
            const iframe = window.gapi.iframes.getContext().openChild({
                url: r.url,
                where: this.marco.el,
                attributes: { style: "width: 100%; height: 100%; border: 0;", scrolling: "yes" },
            });
            iframe.register("onproductselect", (ev) => this.elegir(ev),
                window.gapi.iframes.CROSS_ORIGIN_IFRAMES_FILTER);
            this.state.cargando = false;
        });
    }

    fallar(msg) {
        this.state.error = msg;
        this.state.cargando = false;
    }

    async elegir(ev) {
        if (!ev || !ev.packageName) return;
        if (ev.action && ev.action !== "selected") return;
        if (this.state.ocupado) return;
        this.state.ocupado = true;
        let r;
        try {
            r = await this.orm.call("foco.mobile.app", "aprobar_desde_play", [ev.packageName, this.ctx]);
        } catch (e) {
            r = { ok: false, mensaje: (e.data && e.data.message) || "No se pudo aprobar." };
        } finally {
            this.state.ocupado = false;
        }
        this.notif.add(r.mensaje, { type: r.ok ? "success" : "warning" });
        if (!r.ok) return;
        this.state.elegidas.unshift({ paquete: ev.packageName, mensaje: r.mensaje });
        // Desde una solicitud se elige UNA app: de vuelta a la solicitud, ya
        // aprobada, para que se vea el resultado.
        if (this.ctx.request_id) {
            this.action.doAction({
                type: "ir.actions.act_window", res_model: "foco.mobile.app.request",
                res_id: this.ctx.request_id, views: [[false, "form"]], target: "current",
            });
        }
    }

    abrirAjustes() {
        this.action.doAction("foco_monitor.foco_settings_movil_action");
    }
}

registry.category("actions").add("foco_play", FocoPlay);
