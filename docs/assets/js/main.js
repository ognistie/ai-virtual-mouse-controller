(function () {
  "use strict";

  const $ = (selector, context = document) => context.querySelector(selector);
  const $$ = (selector, context = document) => Array.from(context.querySelectorAll(selector));

  function initMenu() {
    const toggle = $(".menu-toggle");
    const nav = $(".topnav");

    if (!toggle || !nav) return;

    toggle.addEventListener("click", () => {
      const isOpen = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(isOpen));
    });

    $$(".topnav a").forEach((link) => {
      link.addEventListener("click", () => {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  function initActiveNav() {
    const navLinks = $$(".topnav a[href^='#']");

    if (!navLinks.length) return;

    const sections = navLinks
      .map((link) => {
        const id = link.getAttribute("href").slice(1);
        const el = document.getElementById(id);
        return el ? { link, el } : null;
      })
      .filter(Boolean);

    if (!sections.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            navLinks.forEach((l) => l.classList.remove("active"));
            const match = sections.find((s) => s.el === entry.target);
            if (match) match.link.classList.add("active");
          }
        });
      },
      {
        rootMargin: "-45% 0px -50% 0px",
        threshold: 0
      }
    );

    sections.forEach((s) => observer.observe(s.el));
  }

  function initCopyButtons() {
    $$(".copy-btn").forEach((button) => {
      const block = button.closest(".code-block") || button.closest(".terminal");
      if (!block) return;

      const code = $("code", block);
      if (!code) return;

      button.addEventListener("click", async () => {
        const originalText = button.textContent;
        try {
          await navigator.clipboard.writeText(code.textContent.trim());
          button.textContent = "Copiado";
          button.classList.add("copied");
        } catch (error) {
          button.textContent = "Erro";
        }

        setTimeout(() => {
          button.textContent = originalText;
          button.classList.remove("copied");
        }, 1600);
      });
    });

    $$(".copy-trigger").forEach((button) => {
      const terminal = button.closest(".terminal");
      if (!terminal) return;
      const code = $("code", terminal);
      if (!code) return;

      button.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(code.textContent.trim());
          button.textContent = "copiado";
          setTimeout(() => {
            button.textContent = "copiar";
          }, 1400);
        } catch (error) {
          button.textContent = "erro";
          setTimeout(() => {
            button.textContent = "copiar";
          }, 1400);
        }
      });
    });
  }

  function initReveal() {
    const elements = $$(
      "section, .feature-card, .gesture-card, .step, .trouble-item, .pipeline-list li, .pipeline-node, .shortcut, .requirements-grid article, .guide-gestures article"
    );

    if (!("IntersectionObserver" in window) || !elements.length) {
      elements.forEach((el) => el.classList.add("in-view"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("in-view");
            observer.unobserve(entry.target);
          }
        });
      },
      {
        threshold: 0.08,
        rootMargin: "0px 0px -40px 0px"
      }
    );

    elements.forEach((element) => {
      element.classList.add("reveal");
      observer.observe(element);
    });
  }

  function initSmoothScroll() {
    $$("a[href^='#']").forEach((link) => {
      const href = link.getAttribute("href");
      if (!href || href === "#") return;

      link.addEventListener("click", (event) => {
        const target = document.querySelector(href);
        if (!target) return;
        event.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  }

  function initTrouble() {
    const items = $$(".trouble-item");

    items.forEach((item) => {
      item.addEventListener("toggle", () => {
        if (item.open) {
          items.forEach((other) => {
            if (other !== item) other.open = false;
          });
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initMenu();
    initActiveNav();
    initCopyButtons();
    initReveal();
    initSmoothScroll();
    initTrouble();
  });
})();
