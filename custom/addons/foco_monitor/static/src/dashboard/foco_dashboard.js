/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, useRef, useExternalListener } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const CAT_KEY = {
    "Productiva": "productiva", "Navegador": "navegador", "Neutral": "neutral",
    "Distraccion": "distraccion", "Sistema": "sistema",
};
const CAT_LABEL = {
    productiva: "Productiva", navegador: "Navegador", neutral: "Neutral",
    distraccion: "Distracción", sistema: "Sistema", sin: "Sin clasificar",
};
const CAT_ORDER = ["productiva", "navegador", "neutral", "distraccion", "sistema", "sin"];

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
            attention: 0, sinSenal: 0,
            detail: null, open: false,
        });
        onWillStart(() => this.load());
        onMounted(() => this.detectTheme());
        useExternalListener(window, "keydown", (ev) => {
            if (ev.key === "Escape" && this.state.detail) this.closeDetail();
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

    ymd(d) { return d.toISOString().slice(0, 10); }

    async load() {
        this.state.loading = true;
        const days = this.state.days;
        const today = new Date();
        const curStart = new Date(today); curStart.setDate(today.getDate() - (days - 1));
        const prevEnd = new Date(curStart); prevEnd.setDate(curStart.getDate() - 1);
        const prevStart = new Date(prevEnd); prevStart.setDate(prevEnd.getDate() - (days - 1));

        const fields = ["employee_id", "app_id", "site_id", "category_id", "fg_active",
            "fg_idle", "background", "active_hours", "productive_hours", "call_hours",
            "injected_hours"];
        const [cur, prev, comps, resumen, salud, pendientes] = await Promise.all([
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
        ]);

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
            const e = emp[eid] = emp[eid] || { id: eid, name: en, active: 0, prod: 0, distr: 0, call: 0, injected: 0, idle: 0, apps: {}, sites: {}, comp: {} };
            e.active += r.active_hours; e.prod += r.productive_hours;
            e.call += r.call_hours || 0;
            e.injected += r.injected_hours || 0;
            e.idle += r.fg_idle || 0;
            if (catName === "Distraccion") e.distr += r.active_hours;
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
            const res = (resumen || {})[String(e.id)] || {};
            const expected = res.expected || 0;
            const justified = res.justified || 0;
            // Lo unico que amerita conversacion: ni medido, ni justificado.
            const unexplained = Math.max(0, expected - e.active - justified);
            return {
                expected, justified, unexplained,
                pendingAbs: res.pending || 0,
                hasExpected: expected > 0,
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

        this.state.team = {
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
        this.state.attention = employees.filter((e) => e.attention).length;
        this.state.sinSenal = employees.filter((e) => e.sinSenal).length;
        this.state.loading = false;
    }

    openDetail(e) {
        this.state.detail = e;
        this.state.open = false;

        requestAnimationFrame(() => requestAnimationFrame(() => { this.state.open = true; }));
    }
    closeDetail() {
        this.state.open = false;
        setTimeout(() => { this.state.detail = null; }, 300);
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
