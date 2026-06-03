/* ============================================================ */
/* AI VIRTUAL MOUSE — main interactions                          */
/* theme · menu · canvases · tabs · copy · reveal · scroll       */
/* ============================================================ */

(function () {
  "use strict";

  const $  = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));

  // Active hand renderers — populated by initCanvases, driven by initMetricTicker
  // so the holographic hands mirror the live "gesto atual" label.
  const handRenderers = [];

  // ============================================================
  // HOLO CONSOLE — Stark-style floating UI driven by gesture cycle
  // ============================================================
  // Each gesture triggers a different "interaction" on the floating cards
  // so the hand visually controls a virtual desktop. Targets correspond to
  // the real action emitted by the gesture detector (CLICK/RIGHT_CLICK/etc).
  const ConsoleSlots = {
    open_hand:    "files",     // MOVE — cursor hovers a file tile
    pinch:        "browser",   // CLICK — ripple on browser link
    pinch_middle: "context",   // RIGHT_CLICK — context menu pops
    peace:        "files",     // DOUBLE_CLICK — files burst-pop
    fist:         "media"      // PAUSE — media halts + global pause veil
  };
  const ActionLabels = {
    MOVE: "cursor.move",
    CLICK: "click.left",
    RIGHT_CLICK: "click.right",
    DOUBLE_CLICK: "click.double",
    PAUSE: "system.pause"
  };

  function initHoloConsole() {
    const root = $("#holoConsole");
    if (!root) return null;
    const stream = $("#holoStream");
    const reticle = $("#holoReticle");
    const cards = {
      browser: $(".hc-browser", root),
      media:   $(".hc-media", root),
      files:   $(".hc-files", root),
      context: $(".hc-context", root)
    };
    const tileCount = 6;

    function clearTransients() {
      cards.browser && cards.browser.classList.remove("click", "active");
      cards.media   && cards.media.classList.remove("active", "dbl");
      cards.files   && cards.files.classList.remove("active", "hover", "dbl");
      cards.context && cards.context.classList.remove("show", "active");
      root.classList.remove("paused");
    }

    function moveReticleTo(card) {
      if (!reticle || !card) return;
      const cb = card.getBoundingClientRect();
      const rb = root.getBoundingClientRect();
      const cx = cb.left - rb.left + cb.width / 2;
      const cy = cb.top - rb.top + cb.height / 2;
      reticle.style.left = cx + "px";
      reticle.style.top  = cy + "px";
      reticle.style.opacity = "0.85";
    }

    function pulseReticleClick() {
      if (!reticle) return;
      reticle.classList.remove("click");
      // Force reflow to restart animation
      void reticle.offsetWidth;
      reticle.classList.add("click");
    }

    function pushStream(evtName, accent) {
      if (!stream) return;
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, "0");
      const mm = String(now.getMinutes()).padStart(2, "0");
      const ss = String(now.getSeconds()).padStart(2, "0");
      const row = document.createElement("div");
      row.className = "hs-row" + (accent ? " act" : "");
      row.innerHTML = `<span class="hs-time">${hh}:${mm}:${ss}</span><span class="hs-evt">${evtName}</span>`;
      stream.insertBefore(row, stream.firstChild);
      // Keep at most 5 rows
      while (stream.children.length > 5) stream.removeChild(stream.lastChild);
    }

    function react(gestureName, action) {
      clearTransients();
      const slot = ConsoleSlots[gestureName] || "files";
      const card = cards[slot];

      if (gestureName === "fist") {
        root.classList.add("paused");
        moveReticleTo(cards.media);
        if (reticle) reticle.style.opacity = "0.35";
        pushStream(ActionLabels[action] || action.toLowerCase(), true);
        return;
      }

      if (card) {
        card.classList.add("active");
        moveReticleTo(card);
      }

      switch (action) {
        case "MOVE": {
          // Hover a random file tile
          const idx = 1 + Math.floor(Math.random() * tileCount);
          if (cards.files) {
            cards.files.style.setProperty("--target", idx);
            cards.files.classList.add("hover");
          }
          pushStream(ActionLabels.MOVE);
          break;
        }
        case "CLICK":
          if (cards.browser) cards.browser.classList.add("click");
          pulseReticleClick();
          pushStream(ActionLabels.CLICK, true);
          break;
        case "RIGHT_CLICK":
          if (cards.context) cards.context.classList.add("show");
          pulseReticleClick();
          pushStream(ActionLabels.RIGHT_CLICK, true);
          break;
        case "DOUBLE_CLICK":
          if (cards.files) cards.files.classList.add("dbl");
          pulseReticleClick();
          // second tick for double feel
          setTimeout(pulseReticleClick, 180);
          pushStream(ActionLabels.DOUBLE_CLICK, true);
          break;
        default:
          pushStream((action || "event").toLowerCase());
      }
    }

    // Reposition reticle on resize so it tracks card centers
    window.addEventListener("resize", () => {
      const activeCard = root.querySelector(".holo-card.active");
      if (activeCard) moveReticleTo(activeCard);
    });

    return { react };
  }

  // ============ THEME TOGGLE ============
  function initTheme() {
    const root = document.documentElement;
    const KEY = "avm-theme";
    const stored = localStorage.getItem(KEY);
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const initial = stored || (prefersDark ? "dark" : "dark"); // default dark
    root.setAttribute("data-theme", initial);

    const toggle = $(".theme-toggle");
    if (!toggle) return;

    toggle.addEventListener("click", () => {
      const current = root.getAttribute("data-theme") || "dark";
      const next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      localStorage.setItem(KEY, next);
    });
  }

  // ============ MOBILE MENU ============
  function initMenu() {
    const toggle = $(".menu-toggle");
    const nav = $(".nav-links");
    if (!toggle || !nav) return;

    toggle.addEventListener("click", () => {
      const isOpen = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(isOpen));
    });

    $$(".nav-links a").forEach((link) => {
      link.addEventListener("click", () => {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  // ============ ACTIVE SECTION ============
  function initActiveNav() {
    const navLinks = $$(".nav-links a[href^='#']");
    if (!navLinks.length) return;

    const map = navLinks
      .map((link) => {
        const id = link.getAttribute("href").slice(1);
        const el = document.getElementById(id);
        return el ? { link, el } : null;
      })
      .filter(Boolean);

    if (!map.length) return;

    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            navLinks.forEach((l) => l.classList.remove("active"));
            const match = map.find((m) => m.el === entry.target);
            if (match) match.link.classList.add("active");
          }
        });
      },
      { rootMargin: "-45% 0px -50% 0px", threshold: 0 }
    );

    map.forEach((m) => obs.observe(m.el));
  }

  // ============ TERMINAL TABS ============
  function initTabs() {
    const tabs = $$(".tab[data-tab]");
    if (!tabs.length) return;

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const target = tab.dataset.tab;
        const container = tab.closest(".terminal");
        if (!container) return;

        $$(".tab", container).forEach((t) => t.classList.toggle("active", t === tab));
        $$(".terminal-body", container).forEach((body) => {
          body.classList.toggle("hidden", body.dataset.tabContent !== target);
        });
      });
    });
  }

  // ============ COPY BUTTONS ============
  function initCopy() {
    $$(".copy-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const container = btn.closest(".terminal");
        if (!container) return;
        const activeBody = $(".terminal-body:not(.hidden)", container);
        if (!activeBody) return;
        const code = activeBody.querySelector("code");
        if (!code) return;

        const text = code.textContent.replace(/^\$\s/gm, "").trim();
        try {
          await navigator.clipboard.writeText(text);
          const label = btn.querySelector("span");
          if (label) {
            const original = label.textContent;
            label.textContent = "copied";
            btn.classList.add("copied");
            setTimeout(() => {
              label.textContent = original;
              btn.classList.remove("copied");
            }, 1600);
          }
        } catch (err) {
          /* silent fail */
        }
      });
    });
  }

  // ============ SMOOTH SCROLL ============
  function initSmoothScroll() {
    $$("a[href^='#']").forEach((link) => {
      const href = link.getAttribute("href");
      if (!href || href === "#" || href === "#top") return;

      link.addEventListener("click", (e) => {
        const target = document.querySelector(href);
        if (!target) return;
        e.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    const topLink = $("a[href='#top']");
    if (topLink) {
      topLink.addEventListener("click", (e) => {
        e.preventDefault();
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    }
  }

  // ============ LIVE METRIC TICKER ============
  // Simulates the look of a real-time tracking panel by gently
  // animating the metric numbers — visual feedback only.
  function initMetricTicker() {
    const fpsEl = $("#metricFps");
    const fpsBar = $("#metricFpsBar");
    const latEl = $("#metricLat");
    const latBar = $("#metricLatBar");
    const confEl = $("#metricConf");
    const confBar = $("#metricConfBar");
    const cursorEl = $("#metricCursor");
    const gestureEl = $("#gestureName");
    const actionEl = $("#actionName");
    const gestureBar = $("#gestureBar");

    if (!fpsEl) return;

    const gestures = [
      { name: "open_hand", action: "MOVE", duration: 4200 },
      { name: "pinch", action: "CLICK", duration: 1400 },
      { name: "open_hand", action: "MOVE", duration: 3800 },
      { name: "peace", action: "DOUBLE_CLICK", duration: 1600 },
      { name: "open_hand", action: "MOVE", duration: 5000 },
      { name: "pinch_middle", action: "RIGHT_CLICK", duration: 1500 },
      { name: "fist", action: "PAUSE", duration: 2200 },
    ];

    let gIdx = 0;
    let lastSwitch = performance.now();
    let lastGestureName = null;

    // Holo console + gesture legend wiring
    const holoConsole = initHoloConsole();
    const legendItems = $$("#gestureLegend li");
    function highlightLegend(name) {
      legendItems.forEach((li) => {
        li.classList.toggle("active", li.dataset.gesture === name);
      });
    }

    // Push initial gesture so hands start aligned with the label.
    handRenderers.forEach((r) => r.setGesture && r.setGesture(gestures[0].name));
    if (holoConsole) holoConsole.react(gestures[0].name, gestures[0].action);
    highlightLegend(gestures[0].name);
    lastGestureName = gestures[0].name;

    function tick(now) {
      // FPS gentle variance 56-60
      const fps = 58 + Math.sin(now * 0.0015) * 1.5 + (Math.random() - 0.5) * 0.4;
      fpsEl.textContent = fps.toFixed(1);
      if (fpsBar) fpsBar.style.width = (fps / 60 * 100).toFixed(1) + "%";

      // Latency 10-16ms
      const lat = 12.5 + Math.sin(now * 0.002) * 1.5 + (Math.random() - 0.5) * 0.3;
      latEl.textContent = lat.toFixed(1);
      if (latBar) latBar.style.width = (lat / 60 * 100).toFixed(1) + "%";

      // Confidence 0.92-0.96
      const conf = 0.94 + Math.sin(now * 0.0012) * 0.02;
      confEl.textContent = conf.toFixed(2);
      if (confBar) confBar.style.width = (conf * 100).toFixed(0) + "%";

      // Cursor coords slowly drift
      const cx = Math.floor(847 + Math.sin(now * 0.0008) * 80);
      const cy = Math.floor(412 + Math.cos(now * 0.0011) * 60);
      cursorEl.textContent = `${cx}, ${cy}`;

      // Gesture cycle
      const g = gestures[gIdx];
      if (now - lastSwitch > g.duration) {
        gIdx = (gIdx + 1) % gestures.length;
        lastSwitch = now;
      }
      const next = gestures[gIdx];
      if (gestureEl) gestureEl.textContent = next.name;
      if (actionEl) actionEl.textContent = next.action;

      // Drive the canvas hands + holo console when the active gesture changes.
      if (next.name !== lastGestureName) {
        handRenderers.forEach((r) => r.setGesture && r.setGesture(next.name));
        if (holoConsole) holoConsole.react(next.name, next.action);
        highlightLegend(next.name);
        lastGestureName = next.name;
      }
      if (gestureBar) {
        const progress = Math.min(100, (now - lastSwitch) / next.duration * 100);
        gestureBar.style.width = (100 - progress) + "%";
      }

      requestAnimationFrame(tick);
    }

    requestAnimationFrame(tick);
  }

  // ============ CANVASES INIT ============
  function initCanvases() {
    if (!window.HandRenderer) return;

    // Hero canvas — main attention-grabbing visualization
    const heroCanvas = $("#handCanvas");
    if (heroCanvas) {
      handRenderers.push(new window.HandRenderer(heroCanvas, {
        accent: "#22d3ee",
        accentSoft: "rgba(34, 211, 238, 0.18)",
        ink: "#cbd5e1",
        sway: 10,
        breathScale: 0.045,
        fingertipsGlow: true
      }));
    }

    // System panel canvas — smaller, more clinical
    const sysCanvas = $("#systemCanvas");
    if (sysCanvas) {
      handRenderers.push(new window.HandRenderer(sysCanvas, {
        accent: "#22d3ee",
        accentSoft: "rgba(34, 211, 238, 0.12)",
        ink: "#94a3b8",
        landmarkRadius: 3,
        landmarkRadiusBig: 4,
        lineWidth: 1,
        sway: 5,
        breathScale: 0.025,
        fingertipsGlow: true
      }));
    }

    // Holographic canvas — volumetric variant
    const holoCanvas = $("#holoCanvas");
    if (holoCanvas && window.HoloRenderer) {
      handRenderers.push(new window.HoloRenderer(holoCanvas, {
        sway: 3,
        breathScale: 0.02
      }));
    }
  }

  // ============ REVEAL ON SCROLL ============
  function initReveal() {
    const els = $$("section, .feature-card, .triad-card, .oss-card, .pipeline-node");
    if (!("IntersectionObserver" in window) || !els.length) {
      els.forEach((el) => el.classList.add("in-view"));
      return;
    }

    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("in-view");
            obs.unobserve(e.target);
          }
        });
      },
      { threshold: 0.08, rootMargin: "0px 0px -40px 0px" }
    );

    els.forEach((el) => obs.observe(el));
  }

  // ============ BOOT ============
  document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initMenu();
    initActiveNav();
    initTabs();
    initCopy();
    initSmoothScroll();
    initCanvases();
    initMetricTicker();
    initReveal();
  });
})();
