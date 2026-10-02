/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormCogMenu } from "@web/views/form/form_cog_menu/form_cog_menu";
import { ListCogMenu } from "@web/views/list/list_cog_menu";
import { STATIC_ACTIONS_GROUP_NUMBER } from "@web/search/action_menus/action_menus";

/**
 * El engrane (menu de acciones) de una cotizacion que todavia no esta aprobada
 * deja solo lo que no la saca al cliente:
 *  - en el formulario, solo «Duplicar»;
 *  - en la lista, solo las acciones seguras sobre la seleccion (exportar,
 *    duplicar, archivar, eliminar).
 * Imprimir, Compartir, Enviar un correo, Marcar como enviada y lo demas
 * aparecen al aprobarse.
 *
 * Esto es lo que se VE. Las guardas de verdad viven en el servidor: el motor de
 * reportes no genera el PDF/HTML de la cotizacion sin aprobar (imprimir desde el
 * formulario, desde la lista o por la URL directa), y enviar/confirmar/compartir
 * pasan por la aprobacion. El formulario y la lista ponen su modelo en el env
 * (useSubEnv), por eso se lee el registro o la seleccion; como el modelo vuelve a
 * pintar al cambiar, el menu se actualiza solo al aprobar.
 */
function esCotizacionSinAprobar(data) {
    return Boolean(data && data.requiere_aprobacion) && data.aprobacion_state !== "aprobada";
}

/** Formulario: el registro abierto. */
function formularioSinAprobar(model) {
    const root = model && model.root;
    if (!root || root.resModel !== "sale.order" || !root.data) {
        return false;
    }
    return esCotizacionSinAprobar(root.data);
}

/** Lista: alguna de las cotizaciones seleccionadas no esta aprobada. */
function seleccionSinAprobar(model) {
    const root = model && model.root;
    if (!root || root.resModel !== "sale.order") {
        return false;
    }
    const seleccion = root.selection || [];
    return seleccion.some((registro) => esCotizacionSinAprobar(registro.data));
}

patch(FormCogMenu.prototype, {
    get cogItems() {
        const items = super.cogItems;
        if (!formularioSinAprobar(this.env.model)) {
            return items;
        }
        return items.filter((item) => item.key === "duplicate");
    },

    async loadPrintItems() {
        if (formularioSinAprobar(this.env.model)) {
            this.state.printItems = [];
            return;
        }
        return super.loadPrintItems(...arguments);
    },
});

patch(ListCogMenu.prototype, {
    get cogItems() {
        const items = super.cogItems;
        if (!seleccionSinAprobar(this.env.model)) {
            return items;
        }
        // Solo las acciones estaticas seguras (exportar, duplicar, archivar,
        // eliminar): no sacan la cotizacion al cliente. Se van las de servidor
        // (Compartir, Enviar un correo, Marcar como enviada, etc.).
        return items.filter((item) => item.groupNumber === STATIC_ACTIONS_GROUP_NUMBER);
    },

    async loadPrintItems() {
        if (seleccionSinAprobar(this.env.model)) {
            this.state.printItems = [];
            return;
        }
        return super.loadPrintItems(...arguments);
    },
});
