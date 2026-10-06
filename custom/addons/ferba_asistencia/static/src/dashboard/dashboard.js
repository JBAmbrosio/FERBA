/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const ESTADOS = {
    presente: { label: "Presente", color: "#16a34a" },
    corta: { label: "Jornada corta", color: "#f59e0b" },
    incompleto: { label: "Incompleto", color: "#f97316" },
    ausente: { label: "Ausente", color: "#dc2626" },
    permiso: { label: "Permiso", color: "#0ea5e9" },
    festivo: { label: "Festivo", color: "#8b5cf6" },
    descanso: { label: "Descanso", color: "#94a3b8" },
};

const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

export class FerbaAsistenciaDashboard extends Component {
    static template = "ferba_asistencia.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            cargando: true,
            generando: false,
            kpi: { corregir: 0, abiertas: 0, ausencias: 0, cumplimiento: 0, validadas: 0, checan: 0 },
            estados: [],
            abiertasLista: [],
            deptos: [],
            mesNombre: "",
            sinDatos: false,
        });
        onWillStart(() => this.cargar());
    }

    _fecha(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    get mesInicio() {
        const n = new Date();
        return this._fecha(new Date(n.getFullYear(), n.getMonth(), 1));
    }
    get hoyStr() {
        return this._fecha(new Date());
    }
    get domMes() {
        return [["fecha", ">=", this.mesInicio], ["fecha", "<=", this.hoyStr]];
    }

    async cargar() {
        this.state.cargando = true;
        const n = new Date();
        this.state.mesNombre = `${MESES[n.getMonth()]} ${n.getFullYear()}`;

        const [corregir, abiertas, validadas, checan] = await Promise.all([
            this.orm.searchCount("hr.attendance", [["necesita_revision", "=", true]]),
            this.orm.searchCount("hr.attendance", [["check_out", "=", false]]),
            this.orm.searchCount("hr.attendance", [["validada", "=", true], ["check_in", ">=", this.mesInicio]]),
            this.orm.searchCount("hr.employee", [["active", "=", true], ["ferba_controla_asistencia", "=", true]]),
        ]);

        const grupos = await this.orm.call(
            "ferba.asistencia.dia", "read_group",
            [this.domMes, ["horas_trabajadas:sum", "horas_esperadas:sum"], ["estado"]],
            { lazy: false }
        );

        let trabTot = 0, espTot = 0, ausencias = 0, maxCount = 1;
        const estados = [];
        for (const g of grupos) {
            const code = g.estado;
            const meta = ESTADOS[code] || { label: code || "—", color: "#64748b" };
            const count = g.__count || 0;
            trabTot += g.horas_trabajadas || 0;
            espTot += g.horas_esperadas || 0;
            if (code === "ausente") ausencias = count;
            maxCount = Math.max(maxCount, count);
            estados.push({ code, label: meta.label, color: meta.color, count });
        }
        estados.sort((a, b) => b.count - a.count);
        for (const e of estados) {
            e.ancho = Math.round((e.count / maxCount) * 100);
        }

        const abiertasLista = await this.orm.searchRead(
            "hr.attendance", [["check_out", "=", false]],
            ["employee_id", "check_in"], { limit: 8, order: "check_in asc" }
        );

        const gruposDep = await this.orm.call(
            "ferba.asistencia.dia", "read_group",
            [this.domMes.concat([["estado", "not in", ["descanso", "festivo"]]]),
                ["horas_trabajadas:sum", "horas_esperadas:sum"], ["department_id"]],
            { lazy: false }
        );
        const deptos = gruposDep.map((g) => {
            const esp = g.horas_esperadas || 0;
            const trab = g.horas_trabajadas || 0;
            return {
                nombre: g.department_id ? g.department_id[1] : "Sin departamento",
                trab: trab,
                esp: esp,
                pct: esp ? Math.round((trab / esp) * 100) : 0,
            };
        }).sort((a, b) => b.esp - a.esp);

        this.state.kpi = {
            corregir, abiertas, ausencias,
            cumplimiento: espTot ? Math.round((trabTot / espTot) * 100) : 0,
            validadas, checan,
        };
        this.state.estados = estados;
        this.state.abiertasLista = abiertasLista.map((a) => ({
            id: a.id,
            empleado: a.employee_id ? a.employee_id[1] : "—",
            desde: a.check_in ? a.check_in.replace("T", " ").slice(0, 16) : "",
        }));
        this.state.deptos = deptos;
        this.state.sinDatos = grupos.length === 0;
        this.state.cargando = false;
    }

    async generarMes() {
        this.state.generando = true;
        try {
            await this.orm.call("ferba.asistencia.dia", "generar_periodo",
                [this.mesInicio, this.hoyStr, false]);
            await this.cargar();
        } finally {
            this.state.generando = false;
        }
    }

    abrirCorregir() {
        this.action.doAction("ferba_asistencia.action_ferba_control_asistencia");
    }
    abrirAbiertas() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Marcas abiertas (sin salida)",
            res_model: "hr.attendance",
            views: [[false, "list"], [false, "form"]],
            domain: [["check_out", "=", false]],
        });
    }
    abrirAusencias() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Ausencias de ${this.state.mesNombre}`,
            res_model: "ferba.asistencia.dia",
            views: [[false, "list"], [false, "pivot"]],
            domain: this.domMes.concat([["estado", "=", "ausente"]]),
        });
    }
    abrirLista() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Asistencia de ${this.state.mesNombre}`,
            res_model: "ferba.asistencia.dia",
            views: [[false, "list"], [false, "pivot"]],
            domain: this.domMes,
            context: { search_default_g_estado: 1 },
        });
    }
}

registry.category("actions").add("ferba_asistencia_dashboard", FerbaAsistenciaDashboard);
