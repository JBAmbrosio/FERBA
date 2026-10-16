/** @odoo-module **/
/**
 * Abrir la app oficial de Odoo directo en una cotizacion (16-oct-2026).
 *
 * Foco (el celular del vendedor) deja el aviso "ir a /odoo/sales/<id>" en su
 * usuario y abre la app; el servidor solo lo pone en la sesion cuando la
 * pantalla arranca DENTRO de la app de Odoo y el aviso es fresco
 * (models/foco_ir_http.py). Aqui se borra el aviso y se salta.
 */
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { rpc } from "@web/core/network/rpc";
import { browser } from "@web/core/browser/browser";

const focoIrA = {
    start() {
        const url = session.foco_ir_a;
        if (!url) {
            return;
        }
        delete session.foco_ir_a;
        const saltar = () => {
            if (!browser.location.pathname.startsWith(url)) {
                browser.location.assign(url);
            }
        };
        rpc("/foco/ir_a/listo", {}).then(saltar, saltar);
    },
};

registry.category("services").add("foco_ir_a", focoIrA);
