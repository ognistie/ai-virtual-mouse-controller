
(function () {
    "use strict";

    const STORAGE_KEY = "aivm-lang";
    const langs = ["pt", "en"];

    const $ = (selector, context = document) => context.querySelector(selector);
    const $$ = (selector, context = document) => Array.from(context.querySelectorAll(selector));

    function getLang() {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (langs.includes(stored)) return stored;
        return (navigator.language || "").toLowerCase().startsWith("pt") ? "pt" : "en";
    }

    function applyLang(lang) {
        document.documentElement.lang = lang === "pt" ? "pt-BR" : "en";

        $$("[data-pt]").forEach((element) => {
            const value = element.getAttribute(`data-${lang}`);
            if (value !== null) element.innerHTML = value;
        });

        $$(".lang-toggle button").forEach((button) => {
            button.classList.toggle("active", button.dataset.lang === lang);
        });

        localStorage.setItem(STORAGE_KEY, lang);
    }

    function initLang() {
        $$(".lang-toggle button").forEach((button) => {
            button.addEventListener("click", () => applyLang(button.dataset.lang));
        });

        applyLang(getLang());
    }

    function initMenu() {
        const button = $(".menu-btn");
        const panel = $(".nav-panel");
        if (!button || !panel) return;

        button.addEventListener("click", () => {
            const open = panel.classList.toggle("open");
            button.setAttribute("aria-expanded", String(open));
            document.body.classList.toggle("lock", open);
        });

        $$(".nav-panel a").forEach((link) => {
            link.addEventListener("click", () => {
                panel.classList.remove("open");
                button.setAttribute("aria-expanded", "false");
                document.body.classList.remove("lock");
            });
        });
    }

    function initActiveNav() {
        const path = window.location.pathname.split("/").pop() || "index.html";

        $$(".nav-panel a").forEach((link) => {
            const href = link.getAttribute("href");
            if (href === path) link.classList.add("active");
        });
    }

    function initCopy() {
        $$("pre").forEach((pre) => {
            if (pre.querySelector(".copy-btn")) return;

            pre.style.position = "relative";

            const button = document.createElement("button");
            button.className = "copy-btn";
            button.type = "button";
            button.textContent = "COPY";

            button.addEventListener("click", async () => {
                const code = pre.querySelector("code") || pre;

                try {
                    await navigator.clipboard.writeText(code.textContent.trim());
                    button.textContent = "COPIED";
                    setTimeout(() => (button.textContent = "COPY"), 1400);
                } catch (error) {
                    button.textContent = "ERR";
                    setTimeout(() => (button.textContent = "COPY"), 1400);
                }
            });

            pre.appendChild(button);
        });
    }

    function initReveal() {
        const items = $$("section, .gesture-tile, .info-panel, .stack-cards article, .pipeline div, .timeline div");

        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        entry.target.classList.add("in-view");
                    }
                });
            },
            { threshold: 0.12 }
        );

        items.forEach((item) => {
            item.classList.add("reveal");
            observer.observe(item);
        });
    }

    function initCursorOrb() {
        const orb = $(".cursor-orb");
        if (!orb) return;

        let x = window.innerWidth / 2;
        let y = window.innerHeight / 2;
        let targetX = x;
        let targetY = y;

        window.addEventListener("pointermove", (event) => {
            targetX = event.clientX;
            targetY = event.clientY;
        });

        function animate() {
            x += (targetX - x) * 0.08;
            y += (targetY - y) * 0.08;
            orb.style.left = `${x}px`;
            orb.style.top = `${y}px`;
            requestAnimationFrame(animate);
        }

        animate();
    }

    function initRandomGlitch() {
        const titles = $$(".glitch-title");
        if (!titles.length) return;

        setInterval(() => {
            const title = titles[Math.floor(Math.random() * titles.length)];
            title.style.transform = `translate(${Math.random() * 6 - 3}px, ${Math.random() * 4 - 2}px) skewX(${Math.random() * 4 - 2}deg)`;
            setTimeout(() => {
                title.style.transform = "";
            }, 90);
        }, 1800);
    }

    function initKonami() {
        const sequence = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "b", "a"];
        let index = 0;

        document.addEventListener("keydown", (event) => {
            if (event.key.toLowerCase() === sequence[index].toLowerCase()) {
                index++;

                if (index === sequence.length) {
                    document.body.classList.add("konami");
                    setTimeout(() => document.body.classList.remove("konami"), 2500);
                    index = 0;
                }
            } else {
                index = 0;
            }
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        initLang();
        initMenu();
        initActiveNav();
        initCopy();
        initReveal();
        initCursorOrb();
        initRandomGlitch();
        initKonami();
    });
})();
