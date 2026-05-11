(function () {
  "use strict";

  const STORAGE_KEY = "aivm-lang";
  const supportedLangs = ["pt", "en"];

  const $ = (selector, context = document) => context.querySelector(selector);
  const $$ = (selector, context = document) => Array.from(context.querySelectorAll(selector));

  function getCurrentLang() {
    const stored = localStorage.getItem(STORAGE_KEY);

    if (stored && supportedLangs.includes(stored)) {
      return stored;
    }

    const browserLang = (navigator.language || "").toLowerCase();
    return browserLang.startsWith("pt") ? "pt" : "en";
  }

  function applyLang(lang) {
    document.documentElement.lang = lang === "pt" ? "pt-BR" : "en";

    $$("[data-pt]").forEach((element) => {
      const value = element.getAttribute(`data-${lang}`);

      if (value !== null) {
        element.innerHTML = value;
      }
    });

    $$(".lang-toggle button").forEach((button) => {
      button.classList.toggle("active", button.dataset.lang === lang);
    });

    localStorage.setItem(STORAGE_KEY, lang);
  }

  function initLangToggle() {
    $$(".lang-toggle button").forEach((button) => {
      button.addEventListener("click", () => {
        applyLang(button.dataset.lang);
      });
    });

    applyLang(getCurrentLang());
  }

  function initMenu() {
    const button = $(".menu-btn");
    const nav = $(".main-nav");

    if (!button || !nav) return;

    button.addEventListener("click", () => {
      nav.classList.toggle("open");
    });

    $$(".main-nav a").forEach((link) => {
      link.addEventListener("click", () => {
        nav.classList.remove("open");
      });
    });
  }

  function initActiveNav() {
    const path = window.location.pathname.split("/").pop() || "index.html";

    $$(".main-nav a").forEach((link) => {
      const href = link.getAttribute("href");

      if (href === path) {
        link.classList.add("active");
      }
    });
  }

  function initCopyButtons() {
    $$(".terminal").forEach((terminal) => {
      const button = $(".copy-trigger", terminal);
      const code = $("code", terminal);

      if (!button || !code) return;

      button.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(code.textContent.trim());
          button.textContent = "copied";

          setTimeout(() => {
            button.textContent = "copy";
          }, 1400);
        } catch (error) {
          button.textContent = "error";

          setTimeout(() => {
            button.textContent = "copy";
          }, 1400);
        }
      });
    });
  }

  function initReveal() {
    const elements = $$(
      "section, .gesture-card, .requirements-grid article, .guide-gestures article, .terminal, .text-panel, .fake-window"
    );

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("in-view");
          }
        });
      },
      {
        threshold: 0.12
      }
    );

    elements.forEach((element) => {
      element.classList.add("reveal");
      observer.observe(element);
    });
  }

  function initGlitchHover() {
    const targets = $$("h1, h2, .logo strong, .btn, .gesture-card");

    targets.forEach((target) => {
      target.addEventListener("mouseenter", () => {
        target.style.filter = "contrast(1.25) saturate(1.35)";
        target.style.transform += " skewX(-1deg)";

        setTimeout(() => {
          target.style.filter = "";
          target.style.transform = target.style.transform.replace(" skewX(-1deg)", "");
        }, 180);
      });
    });
  }

  function initKonami() {
    const sequence = [
      "ArrowUp",
      "ArrowUp",
      "ArrowDown",
      "ArrowDown",
      "ArrowLeft",
      "ArrowRight",
      "ArrowLeft",
      "ArrowRight",
      "b",
      "a"
    ];

    let index = 0;

    document.addEventListener("keydown", (event) => {
      if (event.key.toLowerCase() === sequence[index].toLowerCase()) {
        index++;

        if (index === sequence.length) {
          document.body.style.transition = "filter .25s";
          document.body.style.filter = "invert(1) hue-rotate(160deg) contrast(1.4)";

          setTimeout(() => {
            document.body.style.filter = "";
          }, 1800);

          index = 0;
        }
      } else {
        index = 0;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initLangToggle();
    initMenu();
    initActiveNav();
    initCopyButtons();
    initReveal();
    initGlitchHover();
    initKonami();
  });
})();