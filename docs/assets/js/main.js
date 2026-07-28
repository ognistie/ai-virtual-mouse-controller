(() => {
  "use strict";

  const root = document.documentElement;
  const themeToggle = document.querySelector(".theme-toggle");
  const menuToggle = document.querySelector(".menu-toggle");
  const nav = document.querySelector(".nav-links");
  const heroHandCanvas = document.getElementById("heroHandCanvas");
  const trackingPanel = document.querySelector(".tracking-panel");
  const gestureButtons = Array.from(document.querySelectorAll("[data-gesture-pose]"));
  const heroGestureName = document.getElementById("heroGestureName");
  const heroGestureCue = document.getElementById("heroGestureCue");
  const heroActionName = document.getElementById("heroActionName");
  const heroActionDetail = document.getElementById("heroActionDetail");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const gestureStates = {
    open_hand: {
      gesture: "OPEN_HAND",
      cue: "mão aberta",
      action: "MOVE",
      detail: "move o cursor com suavização adaptativa"
    },
    pinch: {
      gesture: "PINCH",
      cue: "polegar + indicador",
      action: "CLICK / DRAG",
      detail: "clica rapidamente ou arrasta ao sustentar"
    },
    pinch_middle: {
      gesture: "PINCH_MIDDLE",
      cue: "polegar + dedo médio",
      action: "RIGHT_CLICK",
      detail: "abre o menu de contexto do sistema"
    },
    peace: {
      gesture: "PEACE",
      cue: "indicador + médio",
      action: "DOUBLE_CLICK",
      detail: "reproduz dois cliques com intervalo controlado"
    },
    fist: {
      gesture: "FIST",
      cue: "punho fechado",
      action: "PAUSE",
      detail: "congela o cursor durante o reposicionamento"
    }
  };
  let heroHandRenderer = null;
  let gestureCycle = null;
  let activeGestureIndex = 0;

  if (heroHandCanvas && window.HandRenderer) {
    heroHandRenderer = new window.HandRenderer(heroHandCanvas, {
      accent: "#55dce4",
      accentSoft: "rgba(85, 220, 228, 0.16)",
      ink: "#d8f1f2",
      lineWidth: 1.35,
      sway: 4,
      breathScale: 0.018
    });
  }

  function activateGesture(index) {
    const button = gestureButtons[index];
    const pose = button?.dataset.gesturePose;
    const state = pose ? gestureStates[pose] : null;
    if (!button || !pose || !state) return;

    activeGestureIndex = index;
    gestureButtons.forEach((item, itemIndex) => {
      item.setAttribute("aria-pressed", String(itemIndex === index));
    });
    heroHandRenderer?.setGesture(pose);
    if (heroGestureName) heroGestureName.textContent = state.gesture;
    if (heroGestureCue) heroGestureCue.textContent = state.cue;
    if (heroActionName) heroActionName.textContent = state.action;
    if (heroActionDetail) heroActionDetail.textContent = state.detail;
  }

  function stopGestureCycle() {
    if (gestureCycle === null) return;
    window.clearInterval(gestureCycle);
    gestureCycle = null;
  }

  function startGestureCycle() {
    stopGestureCycle();
    if (reducedMotion.matches || gestureButtons.length < 2) return;
    gestureCycle = window.setInterval(() => {
      activateGesture((activeGestureIndex + 1) % gestureButtons.length);
    }, 3600);
  }

  gestureButtons.forEach((button, index) => {
    button.addEventListener("click", () => {
      activateGesture(index);
      startGestureCycle();
    });
  });

  trackingPanel?.addEventListener("pointerenter", stopGestureCycle);
  trackingPanel?.addEventListener("pointerleave", startGestureCycle);
  trackingPanel?.addEventListener("focusin", stopGestureCycle);
  trackingPanel?.addEventListener("focusout", (event) => {
    if (!trackingPanel.contains(event.relatedTarget)) startGestureCycle();
  });
  reducedMotion.addEventListener?.("change", startGestureCycle);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopGestureCycle();
    else startGestureCycle();
  });
  activateGesture(0);
  startGestureCycle();

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
