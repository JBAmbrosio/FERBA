/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount, useRef, useExternalListener, useEffect } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";

// Chart.js NO viaja en este modulo: Odoo ya lo trae (4.4.5) y lo publica en el
// bundle `web.chartjs_lib`, que es el mismo que carga su vista de graficos. Se
// pide bajo demanda, no en el manifiesto, porque el resto de las pantallas de
// Foco no dibuja graficas y no tienen por que pagar la descarga.
//
// Un CDN estaba descartado de entrada: este modulo corre en Odoo.sh, y una
// dependencia externa convierte «se cayo la red de un tercero» en «el tablero
// no abre».
let ChartJS = null;
async function traerChart() {
    if (ChartJS) return ChartJS;
    await loadBundle("web.chartjs_lib");
    ChartJS = window.Chart;
    return ChartJS;
}

const CAT_KEY = {
    "Productiva": "productiva", "Navegador": "navegador", "Neutral": "neutral",
    "Distraccion": "distraccion", "Sistema": "sistema",
};
const CAT_LABEL = {
    productiva: "Productiva", navegador: "Navegador", neutral: "Neutral",
    distraccion: "Distracción", sistema: "Sistema", sin: "Sin clasificar",
};
const CAT_ORDER = ["productiva", "navegador", "neutral", "distraccion", "sistema", "sin"];

// Cada cuanto se refresca solo. El agente empuja cada 5 min en produccion, asi
// que refrescar mas seguido no traeria dato nuevo, solo carga.
const REFRESCO_MS = 10 * 60 * 1000;

export class FocoDashboard extends Component {
    static template = "foco_monitor.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        this.state = useState({
            loading: true, days: 7, dark: false,
            team: {}, employees: [], composition: [], distractions: [],
            sites: [], pendingSites: [],
            attention: 0, sinSenal: 0, cobertura: {},
            detail: null, open: false,
            refrescando: false, ultimo: null,
            // analitica
            serie: [], porPersona: [], jornada: [],
            expEmpleado: "", expGrano: "dia",
            // Que hizo cada quien, desplegado debajo de su renglon. El arbol
            // se pide al abrir y se guarda por persona Y periodo: cambiar de
            // «7 dias» a «30» es otra pregunta, no la misma con cache.
            rango: null, abiertos: {}, arboles: {},
        });
        // Un lienzo por grafica. Se guardan las instancias para destruirlas:
        // Chart.js deja listeners vivos, y redibujar sin destruir acumula una
        // grafica encima de otra en cada refresco -se nota porque el tooltip
        // empieza a mostrar series viejas-.
        this.cTendencia = useRef("cTendencia");
        this.cDonut = useRef("cDonut");
        this.cJornada = useRef("cJornada");
        this.graficas = {};
        onWillStart(() => this.load());
        onMounted(() => {
            this.detectTheme();
            this.temporizador = setInterval(() => this.refrescoAutomatico(), REFRESCO_MS);
            // Odoo cambia de claro a oscuro SIN recargar la pagina: repinta el
            // tema en el <body>. Se vigila ese atributo para redibujar las
            // graficas con la tinta correcta al instante; si no, la letra de
            // los lienzos -que la pinta Chart.js, no el CSS- se queda con el
            // color del tema anterior hasta el proximo refresco, y de ahi el
            // reporte de "letras negras" sobre fondo oscuro. El resto de la
            // pantalla es HTML y ya cambia solo con las variables CSS.
            this.observadorTema = new MutationObserver(() => {
                const antes = this.state.dark;
                this.detectTheme();
                if (this.state.dark !== antes) this.dibujar();
            });
            this.observadorTema.observe(document.body,
                { attributes: true, attributeFilter: ["class", "style", "data-color-scheme"] });
        });
        onWillUnmount(() => {
            clearInterval(this.temporizador);
            if (this.observadorTema) this.observadorTema.disconnect();
            this.tirarGraficas();
        });
        // Se redibuja cuando cambia el dato, no en cada render: el tablero
        // repinta al abrir la ficha de un empleado y volver a construir tres
        // graficas por eso seria trabajo tirado.
        useEffect(
            () => { this.dibujar(); },
            () => [this.state.serie, this.state.porPersona, this.state.reparto,
                   this.state.jornada, this.state.dark, this.state.days]
        );
        useExternalListener(window, "keydown", (ev) => {
            if (ev.key === "Escape" && this.state.detail) this.closeDetail();
        });
    }

    // ------------------------------------------------------------- graficas
    //
    // Tres graficas, cada una con UNA pregunta. La regla que gobierna las tres
    // es la misma del resto de Foco: el ambar se reserva para lo unico
    // accionable -el tiempo sin clasificar-, asi que aqui no se usa de adorno
    // ni para «ojo, bajo». Si el ambar significara dos cosas, dejaria de
    // significar la que importa.

    get tinta() {
        const d = this.state.dark;
        return {
            ink: d ? "#e9edf4" : "#0f1520",
            ink2: d ? "#aab4c4" : "#5a6577",
            ink3: d ? "#7c8797" : "#8b95a6",
            linea: d ? "#2b3442" : "#e7eaf0",
            accent: d ? "#6b9bff" : "#2f6fed",
            fuerte: d ? "rgba(233,237,244,.86)" : "rgba(15,21,32,.82)",
            flojo: d ? "rgba(233,237,244,.20)" : "rgba(15,21,32,.17)",
            ambar: "#f59e0b",
            card: d ? "#171c27" : "#ffffff",
            // Categorias cualitativas de la dona. Aqui el color SI distingue
            // -son clases distintas, no una escala de cantidad-, y el ambar
            // sigue reservado para lo unico accionable: lo sin clasificar.
            prod: d ? "#34d399" : "#10b981",
            dist: d ? "#f87171" : "#ef4444",
            otro: d ? "#64748b" : "#94a3b8",
        };
    }

    /** Etiqueta corta de un día: «lun 14». El eje no necesita el año. */
    ejeDia(iso) {
        const [y, m, d] = iso.split("-").map(Number);
        const f = new Date(y, m - 1, d);
        const dias = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
        return `${dias[f.getDay()]} ${f.getDate()}`;
    }

    tirarGraficas() {
        for (const k of Object.keys(this.graficas)) {
            try { this.graficas[k].destroy(); } catch { /* ya no existe */ }
            delete this.graficas[k];
        }
    }

    /** «Hoy» no tiene tendencia que dibujar -un solo dia es un punto suelto-,
     *  asi que el panel ancho pasa a comparar a las PERSONAS del dia: una
     *  barra horizontal por cada quien, apilada en las MISMAS cuatro cubetas
     *  que la dona para que el color diga lo mismo en ambas graficas. Van
     *  ordenadas por tiempo activo, las mas largas arriba, que es como se lee
     *  "quien esta cargando hoy" de un vistazo. */
    dibujarPorPersona(Chart, t) {
        const p = [...(this.state.porPersona || [])]
            .filter((e) => e.activo > 0.008)
            .sort((a, b) => b.activo - a.activo)
            .slice(0, 12);
        if (!p.length) return;
        const corta = (n) => { n = n || "—"; return n.length > 20 ? n.slice(0, 19) + "…" : n; };
        const cubeta = (label, key, col) => ({
            label, data: p.map((e) => e[key] || 0),
            backgroundColor: col, borderColor: t.card, borderWidth: 1,
            borderRadius: 2, borderSkipped: false, maxBarThickness: 22,
        });
        this.graficas.tendencia = new Chart(this.cTendencia.el, {
            type: "bar",
            data: {
                labels: p.map((e) => corta(e.nombre)),
                datasets: [
                    cubeta("Productivo", "productivo", t.prod),
                    cubeta("Otro", "otro", t.otro),
                    cubeta("Distracción", "distraccion", t.dist),
                    cubeta("Sin clasificar", "sin_clasificar", t.ambar),
                ],
            },
            options: {
                indexAxis: "y",
                responsive: true, maintainAspectRatio: false,
                animation: { duration: 420, easing: "easeOutQuart" },
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { display: true, position: "bottom",
                              labels: { color: t.ink2, boxWidth: 10, boxHeight: 10,
                                        usePointStyle: true, pointStyle: "circle",
                                        font: { size: 11 }, padding: 14 } },
                    tooltip: {
                        backgroundColor: t.card, titleColor: t.ink, bodyColor: t.ink2,
                        borderColor: t.linea, borderWidth: 1, padding: 10, cornerRadius: 8,
                        callbacks: {
                            label: (c) => ` ${c.dataset.label}: ${this.fmt(c.parsed.x)}`,
                        },
                    },
                },
                scales: {
                    x: { stacked: true, grid: { color: t.linea, drawTicks: false },
                         border: { display: false },
                         ticks: { color: t.ink3, font: { size: 10.5 },
                                  callback: (v) => `${v}h` } },
                    y: { stacked: true, grid: { display: false },
                         border: { color: t.linea },
                         ticks: { color: t.ink2, font: { size: 11.5 } } },
                },
            },
        });
    }

    async dibujar() {
        if (!this.cTendencia.el && !this.cDonut.el && !this.cJornada.el) return;
        let Chart;
        try {
            Chart = await traerChart();
        } catch {
            // Sin libreria el tablero sigue sirviendo: los numeros y la tabla
            // no dependen de ella. Se calla en vez de tirar la pantalla.
            return;
        }
        if (!Chart) return;
        this.tirarGraficas();
        const t = this.tinta;

        // Base comun. Sin animacion en el eje de valores al refrescar: el
        // tablero se repinta cada diez minutos y ver tres graficas creciendo
        // desde cero cada vez es ruido, no informacion.
        const base = {
            responsive: true, maintainAspectRatio: false,
            animation: { duration: 420, easing: "easeOutQuart" },
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: t.card, titleColor: t.ink, bodyColor: t.ink2,
                    borderColor: t.linea, borderWidth: 1, padding: 10,
                    cornerRadius: 8, displayColors: true, boxPadding: 4,
                    titleFont: { weight: "600", size: 12 },
                    bodyFont: { size: 12 },
                },
            },
            scales: {
                x: { grid: { display: false }, border: { color: t.linea },
                     ticks: { color: t.ink3, font: { size: 10.5 }, maxRotation: 0,
                              autoSkipPadding: 12 } },
                y: { grid: { color: t.linea, drawTicks: false },
                     border: { display: false },
                     ticks: { color: t.ink3, font: { size: 10.5 }, padding: 8 } },
            },
        };

        // 1) EL PANEL ANCHO. Cambia de pregunta segun el periodo. En «Hoy» un
        //    solo dia no dibuja tendencia -seria un punto suelto-, asi que
        //    compara a las PERSONAS del dia (info de todos los equipos, hoy).
        //    En 7 o 30 dias vuelve a ser la TENDENCIA como area: la pregunta de
        //    todos los dias -vamos mejor o peor-. El area con gradiente le da
        //    peso; la linea gruesa y el punto final con halo marcan donde
        //    estamos hoy.
        if (this.cTendencia.el && this.state.days === 1) {
            this.dibujarPorPersona(Chart, t);
        } else if (this.cTendencia.el && this.state.serie.length) {
            const s = this.state.serie;
            const ultimoConDato = (() => {
                for (let i = s.length - 1; i >= 0; i--) if (s[i].activo) return i;
                return -1;
            })();
            this.graficas.tendencia = new Chart(this.cTendencia.el, {
                type: "line",
                data: {
                    labels: s.map((x) => this.ejeDia(x.date)),
                    datasets: [{
                        label: "Índice",
                        data: s.map((x) => x.indice),
                        borderColor: t.accent, borderWidth: 2.5,
                        backgroundColor: (ctx) => {
                            const { ctx: c, chartArea: a } = ctx.chart;
                            if (!a) return "transparent";
                            const g = c.createLinearGradient(0, a.top, 0, a.bottom);
                            g.addColorStop(0, this.state.dark
                                ? "rgba(107,155,255,.42)" : "rgba(47,111,237,.30)");
                            g.addColorStop(0.55, this.state.dark
                                ? "rgba(107,155,255,.12)" : "rgba(47,111,237,.09)");
                            g.addColorStop(1, "rgba(47,111,237,0)");
                            return g;
                        },
                        fill: true, tension: 0.35, spanGaps: false,
                        // Todos los dias con dato llevan punto; el ULTIMO, mas
                        // grande y con halo, para leer "hoy" de un vistazo.
                        pointRadius: (c) => c.raw === null ? 0
                            : (c.dataIndex === ultimoConDato ? 5 : 2.5),
                        pointBackgroundColor: t.accent,
                        pointBorderColor: t.card,
                        pointBorderWidth: (c) => c.dataIndex === ultimoConDato ? 3 : 0,
                        pointHoverRadius: 6,
                        pointHoverBorderColor: t.card, pointHoverBorderWidth: 2,
                    }],
                },
                options: {
                    ...base,
                    layout: { padding: { top: 8, right: 4 } },
                    scales: {
                        ...base.scales,
                        y: { ...base.scales.y, min: 0, max: 100,
                             ticks: { ...base.scales.y.ticks, stepSize: 25,
                                      callback: (v) => `${v}%` } },
                    },
                    plugins: {
                        ...base.plugins,
                        tooltip: {
                            ...base.plugins.tooltip,
                            callbacks: {
                                label: (c) => {
                                    const f = this.state.serie[c.dataIndex];
                                    if (!f.activo) return "Sin dato ese día";
                                    return `Índice ${f.indice}% · ${this.fmt(f.productivo)} de ${this.fmt(f.activo)}`;
                                },
                            },
                        },
                    },
                },
            });
        }

        // 2) EL REPARTO DEL TIEMPO, como DONA con el indice al centro. Es la
        //    grafica estrella: de un vistazo dice de que esta hecho el tiempo
        //    del equipo, y el hueco central lleva el numero que resume todo. La
        //    comparacion persona-a-persona vive en la tabla de abajo, asi que
        //    la dona puede quedarse con el total sin perder nada.
        if (this.cDonut.el) {
            const r = this.state.reparto || {};
            const partes = [
                { et: "Productivo", v: r.productivo || 0, col: t.prod },
                { et: "Otro (neutral)", v: r.otro || 0, col: t.otro },
                { et: "Distracción", v: r.distraccion || 0, col: t.dist },
                { et: "Sin clasificar", v: r.sin_clasificar || 0, col: t.ambar },
            ].filter((x) => x.v > 0.008);
            const totalHoras = partes.reduce((a, x) => a + x.v, 0);
            const centro = { pct: r.indice || 0, has: totalHoras > 0 };
            // Plugin propio: el numero grande y su etiqueta en el hueco. Chart.js
            // no dibuja texto central; se hace a mano en afterDraw.
            const centroPlugin = {
                id: "centroDona",
                afterDraw: (chart) => {
                    const { ctx } = chart;
                    const meta = chart.getDatasetMeta(0);
                    const arc = meta.data[0];
                    if (!arc) return;
                    const cx = arc.x, cy = arc.y;
                    ctx.save();
                    ctx.textAlign = "center"; ctx.textBaseline = "middle";
                    if (centro.has) {
                        ctx.fillStyle = t.ink;
                        ctx.font = "700 30px system-ui, sans-serif";
                        ctx.fillText(`${centro.pct}%`, cx, cy - 8);
                        ctx.fillStyle = t.ink3;
                        ctx.font = "600 11px system-ui, sans-serif";
                        ctx.fillText("PRODUCTIVO", cx, cy + 16);
                    } else {
                        ctx.fillStyle = t.ink3;
                        ctx.font = "500 13px system-ui, sans-serif";
                        ctx.fillText("Sin datos", cx, cy);
                    }
                    ctx.restore();
                },
            };
            this.graficas.donut = new Chart(this.cDonut.el, {
                type: "doughnut",
                data: {
                    labels: partes.map((x) => x.et),
                    datasets: [{
                        data: partes.map((x) => x.v),
                        backgroundColor: partes.map((x) => x.col),
                        borderColor: t.card, borderWidth: 3,
                        hoverOffset: 6, hoverBorderColor: t.card,
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    cutout: "68%",
                    animation: { duration: 480, easing: "easeOutQuart" },
                    plugins: {
                        legend: { display: true, position: "right",
                                  labels: { color: t.ink2, boxWidth: 10, boxHeight: 10,
                                            usePointStyle: true, pointStyle: "circle",
                                            font: { size: 11.5 }, padding: 12,
                                            generateLabels: (ch) => {
                                                const ds = ch.data.datasets[0];
                                                return ch.data.labels.map((l, i) => ({
                                                    text: `${l}  ${this.fmt(ds.data[i])}`,
                                                    fillStyle: ds.backgroundColor[i],
                                                    strokeStyle: ds.backgroundColor[i],
                                                    pointStyle: "circle",
                                                    // fontColor EXPLICITO por item: cuando se da
                                                    // un `generateLabels` propio, Chart.js pinta
                                                    // CADA etiqueta con el `fontColor` del item y
                                                    // NO con `labels.color`; sin esto cae en su gris
                                                    // por defecto (~#666), que sobre el fondo oscuro
                                                    // se ve casi negro -era esta leyenda, y solo
                                                    // esta, la de las "letras negras"-.
                                                    fontColor: t.ink2,
                                                    index: i,
                                                }));
                                            } } },
                        tooltip: {
                            backgroundColor: t.card, titleColor: t.ink, bodyColor: t.ink2,
                            borderColor: t.linea, borderWidth: 1, padding: 10, cornerRadius: 8,
                            callbacks: {
                                label: (c) => {
                                    const pct = totalHoras ? Math.round(c.parsed / totalHoras * 100) : 0;
                                    return ` ${this.fmt(c.parsed)} · ${pct}%`;
                                },
                            },
                        },
                    },
                },
                plugins: [centroPlugin],
            });
        }

        // 3) DENTRO O FUERA DEL HORARIO, en COLUMNAS. No dice cuanto se trabajo:
        //    dice si cayo donde debia. La columna del dia con trabajo fuera de
        //    jornada se ve crecer por encima en otro color, y esa es justo la
        //    que abre la conversacion.
        if (this.cJornada.el && this.state.jornada.length) {
            const j = this.state.jornada;
            const grad = (hex1, hex2) => (ctx) => {
                const { ctx: c, chartArea: a } = ctx.chart;
                if (!a) return hex1;
                const g = c.createLinearGradient(0, a.bottom, 0, a.top);
                g.addColorStop(0, hex2); g.addColorStop(1, hex1);
                return g;
            };
            this.graficas.jornada = new Chart(this.cJornada.el, {
                type: "bar",
                data: {
                    labels: j.map((x) => this.ejeDia(x.date)),
                    datasets: [
                        { label: "Dentro de jornada", data: j.map((x) => x.dentro),
                          backgroundColor: t.fuerte, borderRadius: 5, borderSkipped: false,
                          maxBarThickness: 34 },
                        { label: "Fuera de jornada", data: j.map((x) => x.fuera),
                          backgroundColor: t.accent, borderRadius: 5, borderSkipped: false,
                          maxBarThickness: 34 },
                    ],
                },
                options: {
                    ...base,
                    plugins: {
                        ...base.plugins,
                        legend: { display: true, position: "bottom",
                                  labels: { color: t.ink2, boxWidth: 10, boxHeight: 10,
                                            usePointStyle: true, pointStyle: "circle",
                                            font: { size: 11 }, padding: 14 } },
                        tooltip: {
                            ...base.plugins.tooltip,
                            callbacks: { label: (c) => ` ${c.dataset.label}: ${this.fmt(c.parsed.y)}` },
                        },
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false },
                             border: { color: t.linea },
                             ticks: { color: t.ink3, font: { size: 10.5 }, maxRotation: 0 } },
                        y: { stacked: true, grid: { color: t.linea, drawTicks: false },
                             border: { display: false },
                             ticks: { color: t.ink3, font: { size: 10.5 },
                                      callback: (v) => `${v}h` } },
                    },
                },
            });
        }
    }

    // ------------------------------------------------------------ exportar
    //
    // Se arma la URL y se deja que el navegador descargue. No se construye el
    // archivo aqui: el nombre lo pone el servidor -con la persona y las fechas
    // dentro-, y el periodo no lo limita la memoria de la pestana.
    get urlExport() {
        const today = new Date();
        const ini = new Date(today);
        ini.setDate(today.getDate() - (this.state.days - 1));
        const p = new URLSearchParams({
            desde: this.ymd(ini), hasta: this.ymd(today),
            grano: this.state.expGrano,
        });
        if (this.state.expEmpleado) p.set("empleado", this.state.expEmpleado);
        return `/foco/reporte.csv?${p.toString()}`;
    }

    exportar() { window.location = this.urlExport; }

    detectTheme() {
        // El tema se lee del DOCUMENTO, no de un ancestro cercano del
        // componente. La version anterior subia por los padres buscando el
        // primer fondo no transparente; en modo oscuro esos padres suelen ser
        // transparentes hasta el body, asi que no encontraba nada y caia en
        // "claro" -y las graficas salian con tinta NEGRA sobre el fondo oscuro
        // de Odoo, ilegibles-. `body` y `html` SI los pinta Odoo segun el tema
        // activo, y existen aunque el componente aun no este montado.
        const oscuro = (el) => {
            if (!el) return null;
            const m = getComputedStyle(el).backgroundColor.match(/[\d.]+/g);
            if (!m || m.length < 3) return null;
            if (m.length === 4 && parseFloat(m[3]) === 0) return null;  // transparente
            return (0.299 * +m[0] + 0.587 * +m[1] + 0.114 * +m[2]) / 255 < 0.5;
        };
        let v = oscuro(document.body);
        if (v === null) v = oscuro(document.documentElement);
        if (v === null) {
            v = !!(window.matchMedia
                   && window.matchMedia("(prefers-color-scheme: dark)").matches);
        }
        this.state.dark = v;
    }

    // Fecha LOCAL, no UTC.
    //
    // Estaba con `toISOString()`, que convierte a UTC antes de recortar. En
    // Mexico -UTC-6- a partir de las 18:00 devolvia el dia SIGUIENTE, asi que
    // «Hoy» pedia el dia que todavia no empieza y el tablero se quedaba en
    // blanco cada tarde. Las otras dos pantallas ya lo hacian asi; esta se
    // habia quedado atras.
    ymd(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }

    async load() {
        // `loading` solo la PRIMERA vez. En un refresco (manual o automatico)
        // NO se pone: encenderla desmonta el bloque t-else -que contiene las
        // graficas- y al remontarlo los <canvas> son elementos nuevos, con lo
        // que las instancias de Chart.js quedan huerfanas y el tablero se queda
        // en blanco hasta el siguiente redibujo. El dato viejo se queda a la
        // vista mientras llega el nuevo, que es mejor que un parpadeo en vacio.
        // El giro del boton (`refrescando`) ya avisa que se esta actualizando.
        if (!this.state.ultimo) {
            this.state.loading = true;
        }
        // Re-detecta el tema en cada carga: si cambio de claro a oscuro entre
        // dos refrescos, las graficas se redibujan con la tinta correcta sin
        // recargar la pagina. Cuesta una lectura de estilo, nada.
        this.detectTheme();
        const days = this.state.days;
        const today = new Date();
        const curStart = new Date(today); curStart.setDate(today.getDate() - (days - 1));
        const prevEnd = new Date(curStart); prevEnd.setDate(curStart.getDate() - 1);
        const prevStart = new Date(prevEnd); prevStart.setDate(prevEnd.getDate() - (days - 1));
        this.state.rango = [this.ymd(curStart), this.ymd(today)];

        const fields = ["employee_id", "app_id", "site_id", "category_id", "fg_active",
            "fg_idle", "background", "active_hours", "productive_hours", "call_hours",
            "injected_hours"];
        const [cur, prev, comps, resumen, salud, pendientes, cobertura] = await Promise.all([
            this.orm.searchRead("foco.usage", [["date", ">=", this.ymd(curStart)]], fields),
            this.orm.searchRead("foco.usage",
                [["date", ">=", this.ymd(prevStart)], ["date", "<=", this.ymd(prevEnd)]], fields),
            this.orm.searchRead("foco.computer", [], ["employee_id", "last_seen", "name"]),
            // jornada esperada y ausencias: sin esto el numero castiga a quien
            // tuvo una cita medica igual que a quien no trabajo
            this.orm.call("foco.absence", "dashboard_summary",
                [this.ymd(curStart), this.ymd(today)]),
            // salud del agente: un agente caido muestra 0h igual que alguien
            // que no hizo nada. Sin esto se leen datos rotos como flojera.
            this.orm.call("foco.computer", "health_summary", []),
            // Cola de clasificacion por IMPACTO: un catalogo que se descubre
            // solo se pudre si nadie lo clasifica, y nadie clasifica 400
            // sitios. Casi siempre un punado explica la mayor parte del tiempo.
            this.orm.call("foco.usage", "sitios_por_clasificar", [days, 6]),
            // Cobertura del dato: dias laborables del calendario de cada quien
            // contra dias en los que su equipo reporto algo. Sin esto se
            // comparan cosas que no son comparables: un 62% sobre 22 dias
            // medidos y un 62% sobre 9 se ven identicos en la tabla.
            this.orm.call("foco.usage", "cobertura",
                [this.ymd(curStart), this.ymd(today)]),
        ]);

        // La analitica se pide agregada, no cruda: el servidor devuelve
        // decenas de filas donde antes viajaban cientos de miles.
        const [analitica, jornada] = await Promise.all([
            this.orm.call("foco.usage", "analitica",
                [this.ymd(curStart), this.ymd(today)]),
            this.orm.call("foco.workday", "jornada_serie",
                [this.ymd(curStart), this.ymd(today)]),
        ]);
        this.state.serie = analitica.dias || [];
        this.state.porPersona = analitica.empleados || [];
        this.state.jornada = jornada || [];
        // Reparto GLOBAL del tiempo activo del equipo, para la dona. El "otro"
        // -tiempo activo que no es productivo, ni distraccion, ni sin
        // clasificar: lo neutral y el navegador- se deriva aqui para que los
        // cuatro segmentos sumen exactamente el activo.
        const rp = analitica.reparto || {};
        const tot = analitica.total || {};
        const otro = Math.max((tot.activo || 0) - (rp.productivo || 0)
            - (rp.distraccion || 0) - (rp.sin_clasificar || 0), 0);
        this.state.reparto = {
            productivo: rp.productivo || 0,
            distraccion: rp.distraccion || 0,
            sin_clasificar: rp.sin_clasificar || 0,
            otro,
            activo: tot.activo || 0,
            indice: tot.indice || 0,
        };

        const seen = {}, pcName = {};
        for (const c of comps) {
            if (!c.employee_id) continue;
            const id = c.employee_id[0];
            const t = c.last_seen ? new Date(c.last_seen.replace(" ", "T") + "Z").getTime() : 0;
            if (!seen[id] || t > seen[id]) { seen[id] = t; pcName[id] = c.name; }
        }

        const emp = {}, comp = {}, distr = {}, sites = {};
        let tActive = 0, tProd = 0, tDistr = 0, tIdle = 0;
        for (const r of cur) {
            const catName = r.category_id ? r.category_id[1] : "Sin clasificar";
            const ck = CAT_KEY[catName] || "sin";
            const siteName = r.site_id ? r.site_id[1] : "";
            tActive += r.active_hours; tProd += r.productive_hours;
            tIdle += r.fg_idle || 0;
            if (catName === "Distraccion") tDistr += r.active_hours;

            (comp[ck] = comp[ck] || { key: ck, hours: 0 }).hours += r.active_hours;
            if (catName === "Distraccion") {
                // Por SITIO cuando lo hay: "Chrome 3h" no le sirve a nadie,
                // "youtube.com 3h" si.
                const dn = siteName || (r.app_id ? r.app_id[1] : "?");
                (distr[dn] = distr[dn] || { name: dn, hours: 0 }).hours += r.fg_active;
            }
            if (siteName) {
                const s = sites[siteName] = sites[siteName] ||
                    { name: siteName, hours: 0, catKey: ck };
                s.hours += r.fg_active;
                if (ck !== "sin") s.catKey = ck;
            }

            const eid = r.employee_id ? r.employee_id[0] : 0;
            const en = r.employee_id ? r.employee_id[1] : "Sin empleado";
            const e = emp[eid] = emp[eid] || { id: eid, name: en, active: 0, prod: 0, distr: 0, call: 0, injected: 0, idle: 0, apps: {}, sites: {}, comp: {}, distrMap: {} };
            e.active += r.active_hours; e.prod += r.productive_hours;
            e.call += r.call_hours || 0;
            e.injected += r.injected_hours || 0;
            e.idle += r.fg_idle || 0;
            if (catName === "Distraccion") {
                e.distr += r.active_hours;
                // La distraccion con NOMBRE, por sitio cuando lo hay: en el
                // renglon cerrado «youtube.com 32m» dice mas que «Distraccion».
                const dn = siteName || (r.app_id ? r.app_id[1] : "?");
                (e.distrMap[dn] = e.distrMap[dn] || { name: dn, hours: 0 }).hours += r.fg_active;
            }
            if (r.app_id) {
                const an = r.app_id[1];
                const a = e.apps[an] = e.apps[an] ||
                    { name: this.appLabel(an), full: an, hours: 0, catKey: ck };
                a.hours += r.fg_active;
            }
            if (siteName) {
                const s = e.sites[siteName] = e.sites[siteName] ||
                    { name: this.appLabel(siteName), full: siteName, hours: 0, catKey: ck };
                s.hours += r.fg_active;
                if (ck !== "sin") s.catKey = ck;
            }
            (e.comp[ck] = e.comp[ck] || { key: ck, hours: 0 }).hours += r.active_hours;
        }

        let pActive = 0, pProd = 0;
        for (const r of prev) { pActive += r.active_hours; pProd += r.productive_hours; }

        const empIds = Object.keys(emp).map(Number).filter(Boolean);
        const depts = {};
        if (empIds.length) {
            const es = await this.orm.read("hr.employee", empIds, ["department_id"]);
            for (const e of es) depts[e.id] = e.department_id ? e.department_id[1] : "";
        }

        const idx = tActive > 0 ? (tProd / tActive) * 100 : 0;
        const pIdx = pActive > 0 ? (pProd / pActive) * 100 : 0;

        const employees = Object.values(emp).map((e) => {
            const index = e.active > 0 ? Math.round((e.prod / e.active) * 100) : 0;
            const appList = Object.values(e.apps).sort((a, b) => b.hours - a.hours);
            const appMax = Math.max(1, ...appList.map((a) => a.hours));
            appList.forEach((a) => { a.pct = Math.round(a.hours / appMax * 100); a.catLabel = CAT_LABEL[a.catKey]; });
            const siteList = Object.values(e.sites).sort((a, b) => b.hours - a.hours).slice(0, 12);
            const siteMax = Math.max(1, ...siteList.map((s) => s.hours));
            siteList.forEach((s) => { s.pct = Math.round(s.hours / siteMax * 100); s.catLabel = CAT_LABEL[s.catKey]; });
            const cTot = Object.values(e.comp).reduce((s, c) => s + c.hours, 0) || 1;
            const composition = Object.values(e.comp)
                .sort((a, b) => CAT_ORDER.indexOf(a.key) - CAT_ORDER.indexOf(b.key))
                .map((c) => ({ key: c.key, label: CAT_LABEL[c.key], hours: c.hours, pct: Math.round(c.hours / cTot * 100) }));
            const ls = seen[e.id] || 0;
            const sal = (salud || {})[String(e.id)] || { health: "ok", minutes: 0 };
            const sinSenal = sal.health === "stale" || sal.health === "never";
            const cb = (cobertura || {})[String(e.id)] || null;
            const res = (resumen || {})[String(e.id)] || {};
            const expected = res.expected || 0;
            const justified = res.justified || 0;
            // Lo unico que amerita conversacion: ni medido, ni justificado.
            const unexplained = Math.max(0, expected - e.active - justified);
            const dList = Object.values(e.distrMap).sort((a, b) => b.hours - a.hours);
            const topDistr = dList.length && dList[0].hours > 0.008
                ? { name: this.appLabel(dList[0].name), hours: dList[0].hours } : null;
            return {
                topDistr, hasTopDistr: !!topDistr,
                // Que tan completo esta el dato de esta persona. No lleva
                // umbral: "incompleto" es una igualdad exacta -faltan dias-,
                // no un corte elegido.
                cobDias: cb ? cb.dias_con_dato : 0,
                cobEsperados: cb ? cb.dias_esperados : 0,
                cobPct: cb ? cb.pct : 0,
                cobFuente: cb ? cb.fuente : "",
                cobParcial: !!cb && cb.dias_esperados > 0 && cb.dias_con_dato < cb.dias_esperados,
                expected, justified, unexplained,
                pendingAbs: res.pending || 0,
                hasExpected: expected > 0,
                // Presencia REAL, no un punto verde o gris. Los estados
                // explicados (apagado, suspendido) son normalidad y van en
                // tinta secundaria; solo "sin senal" toma color, porque es el
                // unico sobre el que hay que hacer algo.
                presencia: sal.presence || (sinSenal ? "sin_senal" : "activo"),
                presenciaTxt: sal.presence_label || "",
                health: sal.health, sinSenal,
                sinSenalLabel: sal.health === "never" ? "nunca reportó"
                    : this.sinceMin(sal.minutes),
                // Anomalias de integridad: EVIDENCIA para revisar con la
                // persona, nunca un veredicto del sistema.
                injected: e.injected, hasInjected: e.injected > 0.008,
                integrityAlert: sal.integrity || "",
                hasAnomaly: e.injected > 0.008 || !!sal.integrity,
                call: e.call, hasCall: e.call > 0.008,
                // Frente a la pantalla sin teclear. NO se resta de nada: se
                // traslapa con las ausencias justificadas (quien se va al
                // medico deja la ventana enfocada). Es observacion, no cuenta.
                idle: e.idle, hasIdle: e.idle > 0.008,
                id: e.id, name: e.name, active: e.active, prod: e.prod, distr: e.distr, index,
                dept: depts[e.id] || "", pc: pcName[e.id] || "",
                topApp: appList.length ? appList[0].name : "—",
                appList, siteList, composition,
                initials: this.initials(e.name), hue: this.hue(e.name),
                meter: index >= 70 ? "good" : index >= 40 ? "mid" : "low",
                online: !!ls && (Date.now() - ls) < 10 * 60 * 1000,
                since: this.since(ls),
                // Nunca acusar de baja productividad a quien el agente dejo de
                // reportar: ese numero bajo es dato FALTANTE, no flojera.
                attention: !sinSenal && e.active > 0.25 && index < 40,
            };
        }).sort((a, b) => b.index - a.index || b.active - a.active);

        const compTotal = Object.values(comp).reduce((s, c) => s + c.hours, 0) || 1;
        const composition = Object.values(comp)
            .sort((a, b) => CAT_ORDER.indexOf(a.key) - CAT_ORDER.indexOf(b.key))
            .map((c) => ({ key: c.key, label: CAT_LABEL[c.key], hours: c.hours, pct: Math.round(c.hours / compTotal * 100) }));

        const distMax = Math.max(1, ...Object.values(distr).map((d) => d.hours));
        const distractions = Object.values(distr).sort((a, b) => b.hours - a.hours).slice(0, 6)
            .map((d) => ({ name: this.appLabel(d.name), hours: d.hours, pct: Math.round(d.hours / distMax * 100) }));

        const siteAll = Object.values(sites).sort((a, b) => b.hours - a.hours);
        const siteMax = Math.max(1, ...siteAll.map((s) => s.hours));
        const topSites = siteAll.slice(0, 8).map((s) => ({
            name: this.appLabel(s.name), hours: s.hours, catKey: s.catKey,
            catLabel: CAT_LABEL[s.catKey], pct: Math.round(s.hours / siteMax * 100),
        }));
        const pend = pendientes || [];
        const pendHours = pend.reduce((sum, p) => sum + (p.hours || 0), 0);

        // Cobertura del equipo, en DIAS. Se suma sobre TODAS las personas
        // monitoreadas, no solo sobre las que aparecen en la tabla: quien no
        // reporto nada no genera renglones y hoy seria invisible, que es
        // justamente el hueco que esto viene a tapar.
        const conNombre = {};
        for (const e of employees) conNombre[String(e.id)] = e.name;
        for (const c of comps) {
            if (c.employee_id) conNombre[String(c.employee_id[0])] = c.employee_id[1];
        }
        let cobDias = 0, cobEsp = 0, cobParciales = 0;
        const sinDato = [];
        for (const [eid, c] of Object.entries(cobertura || {})) {
            cobDias += c.dias_con_dato; cobEsp += c.dias_esperados;
            if (c.dias_esperados > 0 && c.dias_con_dato < c.dias_esperados) cobParciales++;
            if (c.dias_esperados > 0 && c.dias_con_dato === 0) {
                sinDato.push(conNombre[eid] || ("empleado " + eid));
            }
        }

        this.state.team = {
            cobDias, cobEsp, cobParciales,
            cobPct: cobEsp > 0 ? Math.round(cobDias / cobEsp * 100) : 0,
            cobMedible: cobEsp > 0,
            cobSinDato: sinDato.length,
            cobSinDatoNombres: sinDato.slice(0, 8).join(", "),
            index: Math.round(idx), indexDelta: Math.round(idx - pIdx),
            active: tActive, prod: tProd, prodDelta: tProd - pProd,
            distr: tDistr, distrPct: tActive > 0 ? Math.round(tDistr / tActive * 100) : 0,
            idle: tIdle, hasIdle: tIdle > 0.008,
            siteHours: siteAll.reduce((sum, s) => sum + s.hours, 0),
            pendCount: pend.length, pendHours,
            monitored: employees.length, online: employees.filter((e) => e.online).length,
            meter: idx >= 70 ? "good" : idx >= 40 ? "mid" : "low",
        };
        this.state.employees = employees;
        this.state.composition = composition;
        this.state.distractions = distractions;
        this.state.sites = topSites;
        this.state.pendingSites = pend.map((p) => ({ ...p, name: this.appLabel(p.host) }));
        this.state.cobertura = cobertura || {};
        this.state.attention = employees.filter((e) => e.attention).length;
        this.state.sinSenal = employees.filter((e) => e.sinSenal).length;
        this.state.ultimo = new Date();
        this.state.loading = false;
        // Los paneles que estan abiertos se refrescan con el resto del
        // tablero, en sitio: el dato viejo se queda a la vista hasta que llega
        // el nuevo, sin pasar otra vez por el esqueleto.
        for (const e of employees) {
            if (this.state.abiertos[e.id]) this.cargarArbol(e, true);
        }
    }

    // ------------------------------------------------- que hizo cada quien
    //
    // El arbol vive DEBAJO del renglon, no en un cajon: la pregunta mas
    // frecuente del administrador -"en que se le fue el dia"- no necesita
    // interrumpir la tabla para contestarse. La ficha completa (jornada,
    // anomalias) sigue a un clic, desde el propio panel.

    claveArbol(e) { return `${e.id}|${this.state.days}`; }

    arbolDe(e) { return e ? (this.state.arboles[this.claveArbol(e)] || null) : null; }

    async cargarArbol(e, force = false) {
        const k = this.claveArbol(e);
        if (this.state.arboles[k] && !force) return;
        if (!this.state.arboles[k]) this.state.arboles[k] = { cargando: true };
        const [desde, hasta] = this.state.rango;
        try {
            const arbol = await this.orm.call("foco.usage", "arbol_actividad", [e.id, desde, hasta]);
            this.state.arboles[k] = this.prepararArbol(arbol);
        } catch {
            // Un panel que no carga se dice; el resto del tablero sigue.
            this.state.arboles[k] = { error: true };
        }
    }

    /** Lo que el servidor no decide: color, ancho de barra y etiquetas.
     *
     *  El ancho es relativo al MAYOR de sus hermanos, no al total: con quince
     *  aplicaciones, un porcentaje del total deja todas las barras en un hilo
     *  y la comparacion -para lo que existe la barra- se pierde. El porcentaje
     *  real del periodo va en el numero de al lado. */
    prepararArbol(arbol) {
        const nivel = (nodos, prof) => {
            const top = Math.max(0, ...nodos.map((n) => n.horas));
            for (const n of nodos) {
                n.catKey = this.catKeyDe(n);
                n.catLabel = CAT_LABEL[n.catKey] || n.categoria || "";
                n.w = top > 0 ? Math.max(1.5, n.horas / top * 100) : 0;
                n.abierto = false;
                n.prof = prof;
                n.kids = this.etiquetaHijos(n);
                n.abrible = !!(n.hijos && n.hijos.length) || !!n.cola;
                if (n.hijos && n.hijos.length) nivel(n.hijos, prof + 1);
            }
        };
        nivel(arbol.apps || [], 1);
        nivel(arbol.sistema || [], 1);
        arbol.sistemaAbierto = false;
        return arbol;
    }

    /** La MISMA regla con la que se conto el tiempo (`_compute_category_id`):
     *  la categoria heredada decide el color; «sin clasificar» es el estado
     *  del sitio o la app, y va en la accion, no en el color. */
    catKeyDe(n) {
        if (n.sistema) return "sistema";
        if (!n.categoria) return "sin";
        const k = CAT_KEY[n.categoria];
        if (k) return k;
        if (n.peso >= 0.8) return "productiva";
        if (n.peso <= 0) return "distraccion";
        return "neutral";
    }

    etiquetaHijos(n) {
        const m = (n.hijos || []).length + (n.cola ? n.cola.n : 0);
        if (!m) return "";
        let que;
        if (n.tipo === "app") {
            que = (n.hijos.length && n.hijos[0].tipo === "archivo") || (!n.hijos.length)
                ? "archivo" : "sitio";
        } else {
            que = "página";
        }
        return `${m} ${que}${m === 1 ? "" : "s"}`;
    }

    toggleFila(e) {
        const abre = !this.state.abiertos[e.id];
        this.state.abiertos[e.id] = abre;
        if (abre) this.cargarArbol(e);
    }

    /** Enter o espacio sobre el renglon lo despliega; sobre un boton de
     *  adentro, no: ese ya tiene su propia accion. */
    teclaFila(ev, e) {
        if (ev.target !== ev.currentTarget) return;
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.toggleFila(e); }
    }

    toggleNodo(n) { if (n.abrible) n.abierto = !n.abierto; }

    teclaNodo(ev, n) {
        if (ev.target !== ev.currentTarget) return;
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.toggleNodo(n); }
    }

    toggleSistema(arbol) { arbol.sistemaAbierto = !arbol.sistemaAbierto; }

    teclaSistema(ev, arbol) {
        if (ev.target !== ev.currentTarget) return;
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.toggleSistema(arbol); }
    }

    /** La misma persona y la misma semana, en la pantalla que ya existe para
     *  esto. Simplificar aqui no puede significar esconder: lo que se resume
     *  tiene que poder abrirse entero, con el contexto puesto. */
    abrirUso(e) {
        this.action.doAction("foco_monitor.foco_uso_client", {
            additionalContext: {
                foco_employee_id: e.id,
                foco_desde: this.state.rango ? this.state.rango[1] : undefined,
            },
        });
    }

    /** Del dato al acto, sin salir del panel: lo sin clasificar se clasifica
     *  ahi mismo. Es lo unico que mueve el indice. */
    clasificarSitio(site_id) {
        if (!site_id) return;
        this.action.doAction({
            type: "ir.actions.act_window", name: "Clasificar sitio",
            res_model: "foco.site", res_id: site_id,
            views: [[false, "form"]], target: "new",
        });
    }
    clasificarApp(app_id) {
        if (!app_id) return;
        this.action.doAction({
            type: "ir.actions.act_window", name: "Clasificar aplicación",
            res_model: "foco.app", res_id: app_id,
            views: [[false, "form"]], target: "new",
        });
    }

    /** Refresco de fondo. NO interrumpe si hay una ficha abierta ni si la
     *  pestana no se esta viendo: recargar debajo de las manos del usuario es
     *  peor que mostrar un dato de hace un minuto. */
    async refrescoAutomatico() {
        if (this.state.detail || this.state.refrescando || document.hidden) return;
        await this.refrescar();
    }

    async refrescar() {
        if (this.state.refrescando) return;
        this.state.refrescando = true;
        try {
            await this.load();
        } finally {
            this.state.refrescando = false;
        }
    }

    get horaUltimo() {
        if (!this.state.ultimo) return "";
        const d = this.state.ultimo;
        const hh = String(d.getHours()).padStart(2, "0");
        const mm = String(d.getMinutes()).padStart(2, "0");
        return `${hh}:${mm}`;
    }

    openDetail(e) {
        this.state.detail = e;
        this.state.open = false;
        // La ficha muestra el mismo arbol que el renglon: una sola verdad.
        this.cargarArbol(e);

        requestAnimationFrame(() => requestAnimationFrame(() => { this.state.open = true; }));
    }
    closeDetail() {
        this.state.open = false;
        setTimeout(() => { this.state.detail = null; }, 300);
    }

    /** Saltar al tablero del movil sin pasar por el menu: es el mismo panel de
     *  monitoreo, otra fuente. Se cambia la accion en el mismo sitio, que es
     *  como el usuario piensa el switch -"ver los telefonos"-, no como una
     *  navegacion nueva. */
    verMovil() {
        this.action.doAction("foco_monitor.foco_movil_client");
    }

    /** Del dato al acto: ver un sitio sin clasificar y poder clasificarlo ahi
     *  mismo es lo que mantiene vivo el catalogo. */
    abrirSitios() {
        this.action.doAction("foco_monitor.foco_site_action", {
            additionalContext: { search_default_sin_clasificar: 1 },
        });
    }

    appLabel(s) { s = s || ""; return s.length > 46 ? s.slice(0, 44) + "…" : s; }
    initials(n) { return (n || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase(); }
    hue(n) { let h = 0; for (const ch of (n || "")) h = (h * 31 + ch.charCodeAt(0)) % 360; return h; }
    fmt(h) { const m = Math.round((h || 0) * 60); return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`; }
    fmtDelta(h) {
        const m = Math.round(Math.abs(h || 0) * 60);
        const body = m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
        return `${(h || 0) >= 0 ? "+" : "−"}${body}`;
    }
    sinceMin(m) {
        if (!m) return "recién";
        if (m < 60) return `hace ${m} min`;
        if (m < 1440) return `hace ${Math.round(m / 60)} h`;
        return `hace ${Math.round(m / 1440)} d`;
    }
    since(ts) {
        if (!ts) return "sin señal";
        const s = (Date.now() - ts) / 1000;
        if (s < 600) return "en línea";
        if (s < 3600) return `hace ${Math.round(s / 60)} min`;
        if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
        return `hace ${Math.round(s / 86400)} d`;
    }
    setDays(n) { if (n === this.state.days) return; this.state.days = n; this.load(); }
}

registry.category("actions").add("foco_dashboard", FocoDashboard);
