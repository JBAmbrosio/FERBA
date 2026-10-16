/** @odoo-module **/
/**
 * Abrir la app oficial de Odoo directo en una cotizacion (16-oct-2026).
 *
 * Foco (el celular del vendedor) deja el aviso "ir a /odoo/sales/<id>" en su
 * usuario y abre la app. Dos caminos, los dos medidos en el moto g17:
 *  - la app arranca en frio: el servidor pone el aviso en la sesion
 *    (models/foco_ir_http.py) y aqui se salta al arrancar;
 *  - la app ya estaba viva en segundo plano: Android la trae al frente SIN
 *    recargar la pagina, asi que al volver a verse se le pregunta al servidor
 *    (/foco/ir_a/tomar) si hay un aviso fresco.
 * Solo dentro de la app de Odoo (su navegador se anuncia como "OdooMobile/").
 */
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { rpc } from "@web/core/network/rpc";
import { browser } from "@web/core/browser/browser";

function saltar(url) {
    if (url && !browser.location.pathname.startsWith(url)) {
        browser.location.assign(url);
    }
}

const focoIrA = {
    start() {
        if (!browser.navigator.userAgent.includes("OdooMobile/")) {
            return;
        }
        const url = session.foco_ir_a;
        if (url) {
            delete session.foco_ir_a;
            rpc("/foco/ir_a/listo", {}).then(() => saltar(url), () => saltar(url));
        }
        let revisando = false;
        const revisar = async () => {
            if (revisando || document.visibilityState !== "visible") {
                return;
            }
            revisando = true;
            try {
                saltar(await rpc("/foco/ir_a/tomar", {}, { silent: true }));
            } catch {
                // sin red o sin sesion: nada que hacer
            } finally {
                revisando = false;
            }
        };
        document.addEventListener("visibilitychange", revisar);
        browser.addEventListener("focus", revisar);
    },
};

registry.category("services").add("foco_ir_a", focoIrA);
