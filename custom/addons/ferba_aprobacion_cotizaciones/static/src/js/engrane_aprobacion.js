/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormCogMenu } from "@web/views/form/form_cog_menu/form_cog_menu";

/**
 * El engrane (menu de acciones) de una cotizacion que todavia no esta aprobada
 * solo ofrece «Duplicar». Imprimir, Eliminar, Compartir, Generar un enlace de
 * pago, Enviar un correo, Marcar como enviada, Solicitar firma y lo demas
 * aparecen en cuanto se aprueba (19.0.1.4.0).
 *
 * Es lo que se ve: las compuertas de enviar y confirmar viven en Python
 * (action_quotation_send / action_confirm). El formulario pone su modelo en el
 * env (FormController: useSubEnv({ model })), asi se lee el registro abierto; y
 * como el modelo vuelve a pintar todo al cambiar, el menu se actualiza solo al
 * aprobar.
 */
function cotizacionSinAprobar(model) {
    const root = model && model.root;
    if (!root || root.resModel !== "sale.order") {
        return false;
    }
    const data = root.data || {};
    return Boolean(data.requiere_aprobacion) && data.aprobacion_state !== "aprobada";
}

patch(FormCogMenu.prototype, {
    get cogItems() {
        const items = super.cogItems;
        if (!cotizacionSinAprobar(this.env.model)) {
            return items;
        }
        return items.filter((item) => item.key === "duplicate");
    },

    async loadPrintItems() {
        if (cotizacionSinAprobar(this.env.model)) {
            this.state.printItems = [];
            return;
        }
        return super.loadPrintItems(...arguments);
    },
});
