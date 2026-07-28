(() => {
  "use strict";

  const root = document.documentElement;
  const themeToggle = document.querySelector(".theme-toggle");
  const menuToggle = document.querySelector(".menu-toggle");
  const nav = document.querySelector(".nav-links");

  function readStoredTheme() {
    try {
      return localStorage.getItem("avmc-theme");
    } catch {
      return null;
    }
  }

  function saveTheme(theme) {
    try {
      localStorage.setItem("avmc-theme", theme);
    } catch {
      // The preference remains active for this page even when storage is blocked.
    }
  }

  function preferredTheme() {
    const saved = readStoredTheme();
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(theme) {
    root.dataset.theme = theme;
    if (!themeToggle) return;
    const dark = theme === "dark";
    themeToggle.setAttribute("aria-label", dark ? "Ativar tema claro" : "Ativar tema escuro");
    const icon = themeToggle.querySelector(".theme-icon");
    if (icon) icon.textContent = dark ? "☼" : "◐";
  }

  function closeMenu() {
    if (!nav || !menuToggle) return;
    nav.classList.remove("open");
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.setAttribute("aria-label", "Abrir menu");
    document.body.classList.remove("menu-open");
  }

  applyTheme(preferredTheme());

  themeToggle?.addEventListener("click", () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    saveTheme(next);
    applyTheme(next);
  });

  menuToggle?.addEventListener("click", () => {
    if (!nav) return;
    const open = !nav.classList.contains("open");
    nav.classList.toggle("open", open);
    menuToggle.setAttribute("aria-expanded", String(open));
    menuToggle.setAttribute("aria-label", open ? "Fechar menu" : "Abrir menu");
    document.body.classList.toggle("menu-open", open);
  });

  nav?.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", closeMenu);
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 900) closeMenu();
  });

  const sections = Array.from(document.querySelectorAll("main section[id]"));
  const anchorLinks = Array.from(document.querySelectorAll('.nav-links a[href^="#"]'));

  if ("IntersectionObserver" in window && sections.length && anchorLinks.length) {
    const linkById = new Map(
      anchorLinks.map((link) => [link.getAttribute("href")?.slice(1), link])
    );

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];

        if (!visible) return;
        anchorLinks.forEach((link) => link.classList.remove("active"));
        linkById.get(visible.target.id)?.classList.add("active");
      },
      { rootMargin: "-30% 0px -60% 0px", threshold: [0, 0.1, 0.5] }
    );

    sections.forEach((section) => observer.observe(section));
  }

  const tabButtons = Array.from(document.querySelectorAll("[data-tab]"));
  const tabPanels = Array.from(document.querySelectorAll("[data-tab-panel]"));
  const installCopyButton = document.querySelector(".terminal-tabs .copy-button");

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const selected = button.dataset.tab;
      tabButtons.forEach((item) => {
        item.setAttribute("aria-selected", String(item === button));
      });
      tabPanels.forEach((panel) => {
        panel.hidden = panel.dataset.tabPanel !== selected;
      });
      if (installCopyButton) installCopyButton.dataset.copyTarget = `install-${selected}`;
    });
  });

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget || "");
      if (!target) return;

      try {
        await navigator.clipboard.writeText(target.textContent?.trim() || "");
        const original = button.textContent;
        button.textContent = "Copiado";
        window.setTimeout(() => {
          button.textContent = original;
        }, 1600);
      } catch {
        button.textContent = "Selecione o texto";
        window.setTimeout(() => {
          button.textContent = "Copiar";
        }, 1800);
      }
    });
  });
})();
