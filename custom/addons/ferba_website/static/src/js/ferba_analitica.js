/**
 * Capa 1 — señales de interés del sitio de Ferba.
 *
 * Qué NO hace, a propósito:
 *  - no mide scroll, rebote ni duración media: con unos cientos de visitas
 *    al mes eso es ruido, y ninguna de esas cifras justifica una llamada;
 *  - no pone cookies propias (la atribución vive en sessionStorage, que se
 *    borra al cerrar la pestaña);
 *  - no depende de ningún proveedor: las señales siempre llegan a Odoo, y
 *    de paso se replican a Plausible, Umami o Google Analytics SI el sitio
 *    ya los tiene cargados. Si no hay ninguno, no pasa nada.
 *
 * Qué señales existe lo decide el MARCADO, no este archivo: cualquier
 * elemento con data-ferba-senal="nombre" o "nombre:detalle" emite al hacer
 * clic, y si además lleva data-ferba-ver="ms" emite cuando lleva ese rato
 * a la vista. Añadir una señal nueva es añadir un atributo (y su nombre a
 * la lista cerrada del modelo).
 */
(function () {
    "use strict";

    if (!document.querySelector(".o_ferba_page")) {
        return;
    }

    // Mientras el aviso de privacidad siga sin escribirse no hay puerta de
    // consentimiento, así que al menos se honra lo que el visitante YA pidió
    // explícitamente en su navegador. Si dice que no lo sigan, no se emite
    // nada: ni a Odoo ni a los terceros de abajo.
    if (
        navigator.doNotTrack === "1" ||
        window.doNotTrack === "1" ||
        navigator.msDoNotTrack === "1" ||
        navigator.globalPrivacyControl === true
    ) {
        return;
    }

    const RUTA = "/ferba/evento";
    const CLAVE = "ferba_atribucion";
    // Mismo límite que LARGO_MAX en controllers/analitica.py. El servidor
    // recorta igualmente; esto evita mandar lo que se va a tirar y, sobre
    // todo, evita que un enlace entrante preparado infle sin tope los campos
    // ocultos que viajan DENTRO del correo del formulario de contacto.
    const LARGO_MAX = 128;
    const emitidas = new Set();

    function recortar(texto) {
        return String(texto === null || texto === undefined ? "" : texto).slice(0, LARGO_MAX);
    }

    // ------------------------------------------------------------------
    //  Atribución: de dónde venía. Se resuelve una vez y sobrevive a la
    //  navegación interna, porque el visitante casi nunca deja la señal en
    //  la misma página en la que aterrizó.
    // ------------------------------------------------------------------
    function atribucion() {
        let guardada = null;
        try {
            guardada = JSON.parse(sessionStorage.getItem(CLAVE) || "null");
        } catch (e) {
            guardada = null;
        }
        if (guardada) {
            // Se recorta también al releer: puede venir de una pestaña que
            // guardó el valor antes de que existiera el límite.
            return {
                origen: recortar(guardada.origen),
                campana: recortar(guardada.campana),
                entrada: recortar(guardada.entrada),
            };
        }

        const p = new URLSearchParams(window.location.search);
        const fuente = p.get("utm_source");
        const medio = p.get("utm_medium");

        let origen = "directo";
        if (fuente) {
            origen = medio ? fuente + " / " + medio : fuente;
        } else if (document.referrer) {
            try {
                const host = new URL(document.referrer).hostname;
                // Navegar dentro del propio sitio no es un origen nuevo.
                if (host && host !== window.location.hostname) {
                    origen = host.replace(/^www\./, "");
                }
            } catch (e) {
                // referente ilegible: se queda en "directo"
            }
        }

        const dato = {
            origen: recortar(origen),
            campana: recortar(p.get("utm_campaign")),
            entrada: recortar(window.location.pathname),
        };
        try {
            sessionStorage.setItem(CLAVE, JSON.stringify(dato));
        } catch (e) {
            // navegación privada o almacenamiento bloqueado: se sigue sin
            // persistir; la atribución valdrá solo para esta página.
        }
        return dato;
    }

    // ------------------------------------------------------------------
    //  Emisión
    // ------------------------------------------------------------------
    function emitir(nombre, detalle) {
        const llave = nombre + "|" + (detalle || "");
        if (emitidas.has(llave)) {
            return;
        }
        emitidas.add(llave);

        const a = atribucion();
        const datos = {
            nombre: nombre,
            detalle: recortar(detalle),
            url: recortar(window.location.pathname),
            origen: recortar(a.origen),
            campana: recortar(a.campana),
        };

        // 1) Odoo, que es donde la señal se cruza con el visitante.
        //    sendBeacon no bloquea la navegación y sobrevive al cierre de
        //    la pestaña, que es justo cuando se van los clics de salida.
        const cuerpo = new URLSearchParams(datos);
        try {
            if (navigator.sendBeacon) {
                navigator.sendBeacon(RUTA, cuerpo);
            } else {
                fetch(RUTA, { method: "POST", body: cuerpo, keepalive: true });
            }
        } catch (e) {
            // una señal perdida no rompe la página
        }

        // 2) Lo que haya montado encima, si es que hay algo.
        const props = { detalle: datos.detalle, origen: datos.origen, campana: datos.campana };
        try {
            if (typeof window.plausible === "function") {
                window.plausible(nombre, { props: props });
            }
            if (window.umami && typeof window.umami.track === "function") {
                window.umami.track(nombre, props);
            }
            if (typeof window.gtag === "function") {
                window.gtag("event", nombre, props);
            }
        } catch (e) {
            // ídem
        }
    }

    // ------------------------------------------------------------------
    //  Señales declaradas en el marcado
    // ------------------------------------------------------------------
    function partir(valor) {
        const i = valor.indexOf(":");
        return i === -1 ? [valor, ""] : [valor.slice(0, i), valor.slice(i + 1)];
    }

    function declaradas() {
        // Clic: delegado, para que funcione con lo que Odoo pinte después.
        document.addEventListener("click", function (e) {
            const el = e.target.closest("[data-ferba-senal]");
            if (el && !el.hasAttribute("data-ferba-ver")) {
                const par = partir(el.getAttribute("data-ferba-senal"));
                emitir(par[0], par[1]);
            }
        });

        // Permanencia: el bloque tiene que estar a la vista el rato que
        // pida, no basta con pasar por encima al hacer scroll rápido.
        const mirones = document.querySelectorAll("[data-ferba-senal][data-ferba-ver]");
        if (!mirones.length || !("IntersectionObserver" in window)) {
            return;
        }
        const relojes = new WeakMap();
        const observador = new IntersectionObserver(function (entradas) {
            entradas.forEach(function (entrada) {
                const el = entrada.target;
                if (entrada.isIntersecting) {
                    const espera = parseInt(el.getAttribute("data-ferba-ver"), 10) || 4000;
                    relojes.set(el, window.setTimeout(function () {
                        const par = partir(el.getAttribute("data-ferba-senal"));
                        emitir(par[0], par[1]);
                        observador.unobserve(el);
                    }, espera));
                } else if (relojes.has(el)) {
                    window.clearTimeout(relojes.get(el));
                    relojes.delete(el);
                }
            });
        }, { threshold: 0.4 });
        mirones.forEach(function (el) { observador.observe(el); });
    }

    // ------------------------------------------------------------------
    //  Contacto directo: no se marca en el HTML porque estos enlaces están
    //  repartidos por todo el sitio (pie, cabecera, botón flotante) y
    //  reconocerlos por su href es más fiable que acordarse de etiquetarlos.
    // ------------------------------------------------------------------
    function contactoDirecto() {
        document.addEventListener("click", function (e) {
            const a = e.target.closest("a[href]");
            if (!a) {
                return;
            }
            const href = a.getAttribute("href") || "";
            let canal = null;
            if (href.indexOf("wa.me") !== -1) {
                canal = "whatsapp";
            } else if (href.indexOf("tel:") === 0) {
                canal = "telefono";
            } else if (href.indexOf("mailto:") === 0) {
                canal = "correo";
            }
            if (canal) {
                emitir("contacto_directo", canal);
            }
        });
    }

    // ------------------------------------------------------------------
    //  Formulario: se espera al mensaje de gracias, no al clic en enviar.
    //  Un envío que no pasó la validación no es una solicitud.
    // ------------------------------------------------------------------
    function formulario() {
        const gracias = document.querySelector(".s_website_form_end_message");
        if (!gracias || !("MutationObserver" in window)) {
            return;
        }
        // Odoo limpia el formulario en cuanto el envío sale bien, así que
        // para saber QUÉ pidió hay que quedarse el tipo de solicitud antes:
        // cuando aparece el mensaje de gracias, el desplegable ya está vacío.
        const tipo = document.getElementById("ferba_tipo");
        const boton = document.querySelector(".s_website_form_send");
        let elegido = "";
        if (tipo && boton) {
            boton.addEventListener("click", function () {
                elegido = tipo.value || elegido;
            });
        }
        new MutationObserver(function (cambios, obs) {
            if (!gracias.classList.contains("d-none")) {
                emitir("solicitud_enviada", elegido);
                obs.disconnect();
            }
        }).observe(gracias, { attributes: true, attributeFilter: ["class"] });
    }

    // ------------------------------------------------------------------
    //  El formulario viaja por correo, así que la atribución tiene que ir
    //  DENTRO del mensaje: si no, el correo llega huérfano y no hay forma
    //  de saber qué campaña lo trajo.
    // ------------------------------------------------------------------
    function rellenarAtribucion() {
        const campos = document.querySelectorAll("[data-ferba-utm]");
        if (!campos.length) {
            return;
        }
        const a = atribucion();
        campos.forEach(function (campo) {
            const cual = campo.getAttribute("data-ferba-utm");
            campo.value = (cual === "campana" ? a.campana : cual === "entrada" ? a.entrada : a.origen) || "";
        });
    }

    function iniciar() {
        atribucion();
        rellenarAtribucion();
        declaradas();
        contactoDirecto();
        formulario();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", iniciar);
    } else {
        iniciar();
    }
})();
