/**
 * Movimiento del sitio de Ferba: revelados por scroll y contador del hero.
 *
 * Decisiones:
 *  - Revelado una sola vez por elemento. Volver a animar cada vez que pasa
 *    por pantalla es la interfaz peleándose con quien la lee.
 *  - Lo que ya está en pantalla al cargar NO se revela por scroll: el hero
 *    tiene su propia secuencia de entrada en CSS.
 *  - El escalonado se calcula por grupo, tope de 7 pasos. Sin tope, una
 *    rejilla de 10 frutos dejaría el último medio segundo en blanco.
 *  - Barrido de seguridad: el observador solo avisa cuando algo CRUZA el
 *    umbral; si se salta media página de golpe (tecla Fin, arrastrar la
 *    barra, llegar por ancla) lo saltado se quedaría invisible.
 *  - Sin IntersectionObserver o con movimiento reducido, todo se muestra de
 *    inmediato y el contador salta al valor final.
 */
(function () {
    "use strict";

    const PASO = 80;        // ms entre hermanos
    const MAX_PASOS = 7;    // tope del escalonado
    const MARGEN = "0px 0px -12% 0px";

    const sinMovimiento =
        window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // ------------------------------------------------------------------
    //  Contador: "+20 años" cuenta de 0 a 20 al cargar, una vez.
    // ------------------------------------------------------------------
    function contadores() {
        document.querySelectorAll("[data-ferba-contador]").forEach((el) => {
            const fin = parseInt(el.getAttribute("data-ferba-contador"), 10);
            if (!isFinite(fin)) {
                return;
            }
            if (sinMovimiento || !("requestAnimationFrame" in window)) {
                el.textContent = String(fin);
                return;
            }
            const inicio = performance.now() + 760;    // arranca con las credenciales
            const dur = 1100;
            el.textContent = "0";
            function paso(t) {
                const k = Math.min(1, Math.max(0, (t - inicio) / dur));
                const e = 1 - Math.pow(1 - k, 3);           // ease-out cúbico
                el.textContent = String(Math.round(e * fin));
                if (k < 1) {
                    requestAnimationFrame(paso);
                }
            }
            requestAnimationFrame(paso);
        });
    }

    // ------------------------------------------------------------------
    //  Revelados por scroll
    // ------------------------------------------------------------------
    function revelados() {
        const doc = document;
        const objetivos = doc.querySelectorAll(".o_ferba_reveal");
        if (!objetivos.length) {
            return;
        }

        if (!("IntersectionObserver" in window) || sinMovimiento) {
            objetivos.forEach((el) => el.classList.add("is-visible"));
            return;
        }

        // Retraso por posición dentro del grupo.
        doc.querySelectorAll("[data-ferba-grupo]").forEach((grupo) => {
            const hijos = grupo.querySelectorAll(":scope > .o_ferba_reveal");
            hijos.forEach((hijo, i) => {
                hijo.style.setProperty(
                    "--ferba-retraso",
                    Math.min(i, MAX_PASOS) * PASO + "ms"
                );
            });
        });

        const pendientes = new Set();

        function mostrar(el, animado) {
            if (!animado) {
                el.style.setProperty("--ferba-retraso", "0ms");
            }
            el.classList.add("is-visible");
            pendientes.delete(el);
            observador.unobserve(el);
            if (!pendientes.size) {
                window.removeEventListener("scroll", alHacerScroll);
                window.removeEventListener("resize", alHacerScroll);
            }
        }

        const observador = new IntersectionObserver(
            (entradas) => {
                entradas.forEach((e) => {
                    if (e.isIntersecting) {
                        mostrar(e.target, true);
                    }
                });
            },
            { rootMargin: MARGEN, threshold: 0.01 }
        );

        let pedido = null;
        function alHacerScroll() {
            if (pedido) {
                return;
            }
            pedido = requestAnimationFrame(() => {
                pedido = null;
                const alto = window.innerHeight;
                pendientes.forEach((el) => {
                    const caja = el.getBoundingClientRect();
                    if (caja.top < alto) {
                        // Lo que ya quedó por encima de la pantalla se enseña
                        // sin animar: animar lo que el usuario ya pasó es ruido.
                        mostrar(el, caja.bottom > 0);
                    }
                });
            });
        }

        objetivos.forEach((el) => {
            const caja = el.getBoundingClientRect();
            if (caja.top < window.innerHeight && caja.bottom > 0) {
                el.style.setProperty("--ferba-retraso", "0ms");
                el.classList.add("is-visible");
                return;
            }
            pendientes.add(el);
            observador.observe(el);
        });

        if (pendientes.size) {
            window.addEventListener("scroll", alHacerScroll, { passive: true });
            window.addEventListener("resize", alHacerScroll, { passive: true });
        }
    }

    // ------------------------------------------------------------------
    //  Menú móvil de la cabecera de Ferba: abre/cierra, cierra con Escape,
    //  al elegir un enlace y al pasar a escritorio.
    // ------------------------------------------------------------------
    function menu() {
        const cab = document.querySelector(".o_ferba_header");
        if (!cab) {
            return;
        }
        const btn = cab.querySelector(".o_ferba_header__toggle");
        const nav = cab.querySelector(".o_ferba_header__nav");
        if (!btn || !nav) {
            return;
        }
        const poner = (abierto) => {
            cab.classList.toggle("is-open", abierto);
            btn.setAttribute("aria-expanded", String(abierto));
            btn.setAttribute("aria-label", abierto ? "Cerrar menú" : "Abrir menú");
        };
        btn.addEventListener("click", () => poner(!cab.classList.contains("is-open")));
        nav.addEventListener("click", (e) => { if (e.target.closest("a")) { poner(false); } });
        document.addEventListener("keydown", (e) => { if (e.key === "Escape") { poner(false); } });
        window.addEventListener("resize", () => { if (window.innerWidth > 991) { poner(false); } }, { passive: true });
    }

    // ------------------------------------------------------------------
    //  Adjunto del formulario: arrastrar y soltar.
    //  El clic ya funciona sin JavaScript porque el recuadro es un <label>
    //  que apunta al input. Esto añade el arrastre y el acuse de recibo con
    //  el nombre del archivo, que es lo que convierte un campo de fichero en
    //  algo que se entiende sin leer la ayuda.
    // ------------------------------------------------------------------
    function adjunto() {
        const zona = document.querySelector(".o_ferba_soltar");
        if (!zona) {
            return;
        }
        const input = zona.querySelector(".o_ferba_soltar__input");
        const rotulo = zona.querySelector(".o_ferba_soltar__accion");
        if (!input || !rotulo) {
            return;
        }
        const original =
            rotulo.getAttribute("data-ferba-soltar-rotulo") || rotulo.textContent;

        function pintar() {
            const f = input.files && input.files[0];
            zona.classList.toggle("is-loaded", !!f);
            rotulo.textContent = f ? f.name : original;
        }

        ["dragenter", "dragover"].forEach(function (ev) {
            zona.addEventListener(ev, function (e) {
                e.preventDefault();
                zona.classList.add("is-dragging");
            });
        });
        ["dragleave", "dragend", "drop"].forEach(function (ev) {
            zona.addEventListener(ev, function () {
                zona.classList.remove("is-dragging");
            });
        });

        zona.addEventListener("drop", function (e) {
            e.preventDefault();
            const dt = e.dataTransfer;
            if (!dt || !dt.files || !dt.files.length) {
                return;
            }
            // Un input de fichero solo acepta una FileList, y DataTransfer es
            // la única forma de fabricar una. Si el navegador no lo permite se
            // sale sin romper nada: el clic sigue funcionando.
            try {
                const lista = new DataTransfer();
                lista.items.add(dt.files[0]);
                input.files = lista.files;
            } catch (err) {
                return;
            }
            pintar();
        });

        input.addEventListener("change", pintar);
    }

    function iniciar() {
        revelados();
        contadores();
        menu();
        adjunto();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", iniciar);
    } else {
        iniciar();
    }
})();
