/** @odoo-module **/

/**
 * "En que se fue el tiempo."
 *
 * La pantalla anterior abria en TABLA DINAMICA -un cruce, la vista mas
 * abstracta que existe- con empleado por categoria y tres medidas, y detras una
 * lista de doce columnas con CINCO medidas de horas. Tenia todo el dato y
 * ninguna respuesta: para saber en que trabajo alguien habia que construir la
 * consulta uno mismo.
 *
 * Esta contesta una sola pregunta y deja el resto a un clic. Lo que se quita de
 * la vista no se pierde: el boton "Ver tabla completa" abre exactamente la
 * pantalla de antes, con el mismo periodo y la misma persona.
 */

import { Component, useState, onWillStart, onMounted, useRef, useExternalListener } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const MESES = ["ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic"];

export class FocoUso extends Component {
    static template = "foco_monitor.Uso";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        // Se puede llegar aqui desde la ficha de un dia en Jornada. Si esta
        // pantalla ignorara el contexto, ese enlace diria "ver el detalle" y
        // abriria la semana entera de toda la empresa: el usuario tendria que
        // volver a encontrar a mano la persona y el dia que ya tenia en la
        // mano, que es peor que no ofrecer el enlace.
        // Se ancla en la SEMANA de ese dia, no en el dia: esta pantalla solo
        // sabe de semanas y meses, y meter un tercer modo a medias -sin boton
        // en el conmutador y sin paso propio en las flechas- seria peor que
        // llegar a la semana correcta con la persona ya elegida.
        const ctx = (this.props.action && this.props.action.context) || {};
        const dia = ctx.foco_desde || null;
        this.state = useState({
            loading: true, dark: false,
            vista: "semana",              // semana | mes
            ancla: dia || this.ymd(new Date()),
            empleados: [], empleadoId: ctx.foco_employee_id || null,  // null = todos
            datos: null,
            abierta: null,                // fila desplegada
        });
        onWillStart(() => this.load());
        onMounted(() => this.detectTheme());
        useExternalListener(window, "keydown", (ev) => {
            if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
            if (ev.key === "ArrowLeft") this.mover(-1);
            if (ev.key === "ArrowRight") this.mover(1);
        });
    }

    detectTheme() {
        let el = this.root.el && this.root.el.parentElement;
        for (let i = 0; el && i < 12; i++, el = el.parentElement) {
            const m = getComputedStyle(el).backgroundColor.match(/[\d.]+/g);
            if (m && m.length >= 3 && !(m.length === 4 && parseFloat(m[3]) === 0)) {
                const lum = (0.299 * +m[0] + 0.587 * +m[1] + 0.114 * +m[2]) / 255;
                this.state.dark = lum < 0.5;
                return;
            }
        }
        this.state.dark = false;
    }

    ymd(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    parse(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
    lunes(d) { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }

    get rango() {
        const a = this.parse(this.state.ancla);
        if (this.state.vista === "mes") {
            return [this.ymd(new Date(a.getFullYear(), a.getMonth(), 1)),
                    this.ymd(new Date(a.getFullYear(), a.getMonth() + 1, 0))];
        }
        const ini = this.lunes(a);
        const fin = new Date(ini); fin.setDate(ini.getDate() + 6);
        return [this.ymd(ini), this.ymd(fin)];
    }

    get periodoLabel() {
        const [a, b] = this.rango;
        const da = this.parse(a), db = this.parse(b);
        if (this.state.vista === "mes") {
            return `${MESES[da.getMonth()]} ${da.getFullYear()}`;
        }
        return da.getMonth() === db.getMonth()
            ? `${da.getDate()}–${db.getDate()} ${MESES[da.getMonth()]}`
            : `${da.getDate()} ${MESES[da.getMonth()]} – ${db.getDate()} ${MESES[db.getMonth()]}`;
    }

    async load() {
        this.state.loading = true;
        if (!this.state.empleados.length) {
            const pcs = await this.orm.searchRead(
                "foco.computer", [["employee_id", "!=", false]], ["employee_id"]);
            const vistos = {};
            for (const p of pcs) vistos[p.employee_id[0]] = p.employee_id[1];
            this.state.empleados = Object.entries(vistos)
                .map(([id, name]) => ({ id: +id, name }))
                .sort((a, b) => a.name.localeCompare(b.name));
        }
        const [desde, hasta] = this.rango;
        this.state.datos = await this.orm.call("foco.usage", "desglose",
            [this.state.empleadoId ? [this.state.empleadoId] : [], desde, hasta]);
        this.state.abierta = null;
        this.state.loading = false;
    }

    setVista(v) { if (v !== this.state.vista) { this.state.vista = v; this.load(); } }
    setEmpleado(ev) {
        this.state.empleadoId = ev.target.value ? +ev.target.value : null;
        this.load();
    }
    mover(paso) {
        const d = this.parse(this.state.ancla);
        if (this.state.vista === "mes") d.setMonth(d.getMonth() + paso, 1);
        else d.setDate(d.getDate() + paso * 7);
        this.state.ancla = this.ymd(d);
        this.load();
    }
    hoy() { this.state.ancla = this.ymd(new Date()); this.load(); }

    abrir(fila) {
        if (!fila.detalle.length) return;
        this.state.abierta = this.state.abierta === fila.id ? null : fila.id;
    }

    get quien() {
        const e = this.state.empleados.find((x) => x.id === this.state.empleadoId);
        return e ? e.name : "Toda la empresa";
    }

    /** El ancho de la barra, en % del renglon mas largo.
     *
     *  Relativo al MAYOR y no al total: con quince aplicaciones, un porcentaje
     *  del total deja todas las barras en un hilo de dos pixeles y la
     *  comparacion -que es para lo que existe la barra- se pierde. El numero de
     *  al lado sigue siendo el porcentaje real del periodo. */
    ancho(horas) {
        const filas = (this.state.datos && this.state.datos.filas) || [];
        const top = filas.length ? Math.max(...filas.map((f) => f.horas)) : 0;
        return top > 0 ? Math.max(1.5, (horas / top) * 100) : 0;
    }

    /** El mismo ancho, expresado como recorte desde la derecha.
     *
     *  La barra se dibuja entera y se recorta, en vez de animar el ancho:
     *  animar `width` recalcula el layout en cada fotograma, y `clip-path` va
     *  en el compositor conservando el radio. */
    recorte(horas) {
        const resto = (100 - this.ancho(horas)).toFixed(2);
        return "inset(0 " + resto + "% 0 0)";
    }

    /** Un solo tono, mas o menos denso segun cuanto aporta esa categoria.
     *
     *  No se usa la paleta de colores de Odoo -indices arbitrarios- ni un color
     *  por categoria: el peso YA es el dato, asi que la densidad se deriva de
     *  el y no puede desviarse del numero.
     *
     *  Lo SIN CLASIFICAR no tiene peso todavia, asi que la barra va en la
     *  tinta mas tenue. En la primera version iba en ambar y el resultado fue
     *  que siete de dieciseis renglones eran ambar: dejaba de ser una senal y
     *  pasaba a ser el fondo. El ambar se queda donde manda una accion -la
     *  banda de arriba y el punto de categoria- y no invade la cantidad. */
    tinta(fila) {
        if (fila.sistema) return "sistema";
        if (fila.sin_clasificar) return "sin";
        if (fila.peso >= 0.8) return "alta";
        if (fila.peso >= 0.4) return "media";
        return "baja";
    }

    /** El punto SI marca lo que falta clasificar: es el canal de estado. */
    punto(fila) {
        return fila.sin_clasificar ? "pendiente" : this.tinta(fila);
    }

    clasificarApp(app_id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Clasificar aplicación",
            res_model: "foco.app",
            res_id: app_id,
            views: [[false, "form"]],
            target: "new",
        });
    }

    /** Un pendiente puede ser una app o un sitio; se abre el suyo. */
    resolver(p) {
        return p.tipo === "app" ? this.clasificarApp(p.id) : this.clasificar(p.id);
    }

    dur(h) {
        const m = Math.round((h || 0) * 60);
        if (!m) return "0m";
        return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
    }
    pct(v) { return `${(v || 0).toFixed(v < 10 ? 1 : 0)}%`; }

    /** La tabla de siempre, con el mismo periodo y la misma persona.
     *
     *  Simplificar no puede significar esconder: lo que aqui se resume tiene
     *  que poder abrirse entero, y llegar con el contexto puesto, no en blanco. */
    verTabla() {
        const [desde, hasta] = this.rango;
        const dominio = [["date", ">=", desde], ["date", "<=", hasta]];
        if (this.state.empleadoId) dominio.push(["employee_id", "=", this.state.empleadoId]);
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Detalle · ${this.periodoLabel}`,
            res_model: "foco.usage",
            views: [[false, "list"], [false, "pivot"], [false, "graph"]],
            domain: dominio,
        });
    }

    clasificar(site_id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Clasificar sitio",
            res_model: "foco.site",
            res_id: site_id,
            views: [[false, "form"]],
            target: "new",
        });
    }
}

registry.category("actions").add("foco_uso", FocoUso);
