/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

// El lanzador de Tommy GPT vive en la barra de arriba de Odoo (systray), no como
// una aplicacion aparte. Al hacer clic abre la pantalla del asistente. Solo se
// registra si la persona tiene acceso: el servidor lo dice en session_info
// (session.foco_gpt_puede), asi el tomate solo le aparece a quien puede usarlo.
export class FocoGptSystray extends Component {
    static template = "foco_monitor.FocoGptSystrayItem";
    static props = {};

    setup() {
        this.action = useService("action");
    }

    abrir() {
        this.action.doAction("foco_monitor.foco_gpt_action");
    }
}

if (session.foco_gpt_puede) {
    registry.category("systray").add(
        "foco_gpt", { Component: FocoGptSystray }, { sequence: 20 });
}
