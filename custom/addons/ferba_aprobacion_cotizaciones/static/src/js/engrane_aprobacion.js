/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormCogMenu } from "@web/views/form/form_cog_menu/form_cog_menu";
import { ListController } from "@web/views/list/list_controller";
import { STATIC_ACTIONS_GROUP_NUMBER } from "@web/search/action_menus/action_menus";

/**
 * El menu de acciones de una cotizacion que todavia no esta aprobada deja solo
 * lo que no la saca al cliente:
 *  - en el formulario, el engrane muestra solo «Duplicar»;
 *  - en la lista, al seleccionar cotizaciones, la barra de seleccion oculta
 *    «Imprimir» y deja en «Acciones» solo las seguras (exportar, duplicar,
 *    archivar, eliminar).
 * Imprimir, Compartir, Enviar un correo, Marcar como enviada y lo demas
 * aparecen al aprobarse.
 *
 * Esto es lo que se VE. Las guardas de verdad viven en el servidor: el motor de
 * reportes no genera el PDF/HTML de la cotizacion sin aprobar (imprimir desde el
 * formulario, desde la lista o por la URL directa /report/...), y
 * enviar/confirmar/compartir pasan por la aprobacion. El formulario y la lista
 * ponen su modelo en el env (useSubEnv), por eso se lee el registro o la
 * seleccion; como el modelo vuelve a pintar al cambiar, el menu se actualiza
 * solo al aprobar.
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

/**
 * La barra de seleccion de la lista arma «Imprimir» y «Acciones» desde
 * `actionMenuItems`. Si en la seleccion hay una cotizacion sin aprobar, se quita
 * Imprimir (print: []) y de las acciones se dejan solo las estaticas seguras
 * (exportar, duplicar, archivar, eliminar), fuera las de servidor (Compartir,
 * Enviar un correo, Marcar como enviada...). Aplica tanto a la barra de escritorio
 * (ActionMenus) como al engrane de pantalla chica (ambos leen de aqui).
 */
patch(ListController.prototype, {
    get actionMenuItems() {
        const items = super.actionMenuItems;
        const root = this.model && this.model.root;
        if (!root || root.resModel !== "sale.order") {
            return items;
        }
        const hayBloqueada = (root.selection || []).some((registro) =>
            esCotizacionSinAprobar(registro.data)
        );
        if (!hayBloqueada) {
            return items;
        }
        return {
            action: (items.action || []).filter(
                (item) => item.groupNumber === STATIC_ACTIONS_GROUP_NUMBER
            ),
            print: [],
        };
    },
});
