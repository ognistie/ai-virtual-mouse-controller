/* ============================================================ */
/* HAND VISUALIZATION — canvas-based landmark renderer           */
/* 21-point hand skeleton with subtle motion + cyan glow         */
/* ============================================================ */

(function () {
  "use strict";

  // MediaPipe-style hand topology — 21 landmarks, 20 connections
  // 0=wrist  1-4=thumb  5-8=index  9-12=middle  13-16=ring  17-20=pinky
  const CONNECTIONS = [
    // thumb
    [0, 1], [1, 2], [2, 3], [3, 4],
    // index
    [0, 5], [5, 6], [6, 7], [7, 8],
    // middle
    [9, 10], [10, 11], [11, 12],
    // ring
    [13, 14], [14, 15], [15, 16],
    // pinky
    [0, 17], [17, 18], [18, 19], [19, 20],
    // palm bridges (between MCP joints)
    [5, 9], [9, 13], [13, 17]
  ];

  // Canonical hand pose in normalized [0, 1] coords (open palm facing camera)
  const REST_POSE = [
    [0.50, 0.92],  // 0 wrist
    [0.40, 0.84],  // 1 thumb CMC
    [0.32, 0.74],  // 2 thumb MCP
    [0.26, 0.64],  // 3 thumb IP
    [0.22, 0.55],  // 4 thumb TIP
    [0.42, 0.62],  // 5 index MCP
    [0.40, 0.48],  // 6 index PIP
    [0.39, 0.37],  // 7 index DIP
    [0.38, 0.28],  // 8 index TIP
    [0.50, 0.60],  // 9 middle MCP
    [0.50, 0.43],  // 10 middle PIP
    [0.50, 0.30],  // 11 middle DIP
    [0.50, 0.20],  // 12 middle TIP
    [0.58, 0.62],  // 13 ring MCP
    [0.60, 0.46],  // 14 ring PIP
    [0.61, 0.34],  // 15 ring DIP
    [0.62, 0.25],  // 16 ring TIP
    [0.66, 0.66],  // 17 pinky MCP
    [0.69, 0.52],  // 18 pinky PIP
    [0.71, 0.42],  // 19 pinky DIP
    [0.73, 0.34]   // 20 pinky TIP
  ];

  /**
   * HandRenderer — renders a 21-landmark hand on a canvas with subtle motion.
   * Auto-pauses when offscreen + respects prefers-reduced-motion.
   */
  class HandRenderer {
    constructor(canvas, options = {}) {
      if (!canvas) return;
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.opts = Object.assign({
        accent: "#22d3ee",
        accentSoft: "rgba(34, 211, 238, 0.18)",
        ink: "#cbd5e1",
        bg: null,
        landmarkRadius: 3.5,
        landmarkRadiusBig: 5,
        lineWidth: 1.2,
        breathing: true,
        sway: 8,            // px max sway
        breathScale: 0.04,   // relative breathing scale
        pulse: true,
        fingertipsGlow: true
      }, options);

      this.t = 0;
      this.running = false;
      this.reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      this._resize();
      this._observeVisibility();
      window.addEventListener("resize", () => this._resize());
    }

    _resize() {
      const c = this.canvas;
      const rect = c.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      c.width = rect.width * this.dpr;
      c.height = rect.height * this.dpr;
      this.w = rect.width;
      this.h = rect.height;
      this.ctx.scale(this.dpr, this.dpr);
    }

    _observeVisibility() {
      if (!("IntersectionObserver" in window)) {
        this.start();
        return;
      }
      const io = new IntersectionObserver((entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) this.start();
          else this.stop();
        });
      }, { threshold: 0.01 });
      io.observe(this.canvas);
    }

    start() {
      if (this.running) return;
      this.running = true;
      this._tick();
    }

    stop() {
      this.running = false;
      if (this._raf) cancelAnimationFrame(this._raf);
    }

    _tick() {
      if (!this.running) return;
      this.t += this.reduced ? 0 : 0.012;
      this.draw();
      this._raf = requestAnimationFrame(() => this._tick());
    }

    /**
     * Returns transformed landmarks for current frame.
     */
    _computeLandmarks() {
      const cx = this.w / 2;
      const cy = this.h / 2;
      const scale = Math.min(this.w, this.h) * 0.78;
      const breath = this.opts.breathing
        ? 1 + Math.sin(this.t * 1.8) * this.opts.breathScale
        : 1;
      const swayX = this.opts.sway ? Math.sin(this.t * 0.7) * this.opts.sway : 0;
      const swayY = this.opts.sway ? Math.cos(this.t * 0.9) * this.opts.sway * 0.6 : 0;

      return REST_POSE.map(([x, y], i) => {
        // Per-finger micro-jitter for fingertips (give "alive" feel)
        const isTip = i === 4 || i === 8 || i === 12 || i === 16 || i === 20;
        const jitter = isTip && !this.reduced
          ? Math.sin(this.t * 2.3 + i * 1.7) * 1.5
          : 0;
        const nx = (x - 0.5) * scale * breath;
        const ny = (y - 0.5) * scale * breath;
        return [
          cx + nx + swayX + jitter,
          cy + ny + swayY + jitter * 0.5
        ];
      });
    }

    draw() {
      const ctx = this.ctx;
      const pts = this._computeLandmarks();

      // clear
      ctx.clearRect(0, 0, this.w, this.h);

      // background tint
      if (this.opts.bg) {
        ctx.fillStyle = this.opts.bg;
        ctx.fillRect(0, 0, this.w, this.h);
      }

      // Glow halo behind the hand
      const palmCenter = pts[9]; // middle MCP as center
      const gradient = ctx.createRadialGradient(
        palmCenter[0], palmCenter[1], 10,
        palmCenter[0], palmCenter[1], Math.min(this.w, this.h) * 0.45
      );
      gradient.addColorStop(0, this.opts.accentSoft);
      gradient.addColorStop(1, "transparent");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, this.w, this.h);

      // Connections
      ctx.lineWidth = this.opts.lineWidth;
      ctx.strokeStyle = this.opts.accent;
      ctx.lineCap = "round";

      // Glow layer (wider, low alpha)
      ctx.globalAlpha = 0.18;
      ctx.lineWidth = this.opts.lineWidth + 3;
      this._strokeAllConnections(pts);

      // Core layer
      ctx.globalAlpha = 0.72;
      ctx.lineWidth = this.opts.lineWidth;
      this._strokeAllConnections(pts);

      ctx.globalAlpha = 1;

      // Landmarks (circles)
      for (let i = 0; i < pts.length; i++) {
        const [x, y] = pts[i];
        const isTip = i === 4 || i === 8 || i === 12 || i === 16 || i === 20;
        const isWrist = i === 0;
        const r = isWrist ? this.opts.landmarkRadiusBig
                : isTip ? this.opts.landmarkRadius * 1.15
                : this.opts.landmarkRadius;

        // Glow
        if (this.opts.fingertipsGlow && isTip) {
          const tipPulse = this.opts.pulse
            ? 1 + Math.sin(this.t * 3 + i) * 0.25
            : 1;
          ctx.beginPath();
          ctx.fillStyle = this.opts.accentSoft;
          ctx.arc(x, y, r * 2.6 * tipPulse, 0, Math.PI * 2);
          ctx.fill();
        }

        // Outer ring
        ctx.beginPath();
        ctx.strokeStyle = this.opts.accent;
        ctx.lineWidth = 1.2;
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.stroke();

        // Core dot
        ctx.beginPath();
        ctx.fillStyle = isTip ? this.opts.accent : this.opts.ink;
        ctx.arc(x, y, r * 0.55, 0, Math.PI * 2);
        ctx.fill();
      }

      // Cursor crosshair on middle TIP — implies cursor following
      const cursorPt = pts[8]; // index tip as cursor
      this._drawCursor(cursorPt[0], cursorPt[1]);
    }

    _strokeAllConnections(pts) {
      const ctx = this.ctx;
      ctx.beginPath();
      for (const [a, b] of CONNECTIONS) {
        const [ax, ay] = pts[a];
        const [bx, by] = pts[b];
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
      }
      ctx.stroke();
    }

    _drawCursor(x, y) {
      const ctx = this.ctx;
      const sz = 14;
      ctx.save();
      ctx.translate(x, y);
      // Outer ring pulse
      const p = this.opts.pulse ? 1 + Math.sin(this.t * 4) * 0.15 : 1;
      ctx.strokeStyle = this.opts.accent;
      ctx.globalAlpha = 0.4;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(0, 0, sz * p, 0, Math.PI * 2);
      ctx.stroke();
      // Crosshair lines
      ctx.globalAlpha = 0.6;
      ctx.beginPath();
      ctx.moveTo(-sz - 3, 0); ctx.lineTo(-sz + 3, 0);
      ctx.moveTo(sz + 3, 0); ctx.lineTo(sz - 3, 0);
      ctx.moveTo(0, -sz - 3); ctx.lineTo(0, -sz + 3);
      ctx.moveTo(0, sz + 3); ctx.lineTo(0, sz - 3);
      ctx.stroke();
      ctx.restore();
    }
  }

  /**
   * HoloRenderer — variant that draws a more "filled" volumetric hand
   * for the holographic section. Uses thicker fills + brighter glow.
   */
  class HoloRenderer extends HandRenderer {
    constructor(canvas, options = {}) {
      super(canvas, Object.assign({
        accent: "#22d3ee",
        accentSoft: "rgba(34, 211, 238, 0.22)",
        lineWidth: 2.4,
        landmarkRadius: 4.5,
        landmarkRadiusBig: 6,
        sway: 4,
        breathScale: 0.025,
        fingertipsGlow: true
      }, options));
    }

    draw() {
      const ctx = this.ctx;
      const pts = this._computeLandmarks();

      ctx.clearRect(0, 0, this.w, this.h);

      // Strong cyan halo
      const palmCenter = pts[9];
      const gradient = ctx.createRadialGradient(
        palmCenter[0], palmCenter[1], 10,
        palmCenter[0], palmCenter[1], Math.min(this.w, this.h) * 0.55
      );
      gradient.addColorStop(0, "rgba(34, 211, 238, 0.25)");
      gradient.addColorStop(0.5, "rgba(34, 211, 238, 0.08)");
      gradient.addColorStop(1, "transparent");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, this.w, this.h);

      // Filled "palm" approximation
      ctx.fillStyle = "rgba(34, 211, 238, 0.12)";
      ctx.strokeStyle = "rgba(34, 211, 238, 0.6)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      const palmIdx = [0, 1, 5, 9, 13, 17];
      ctx.moveTo(pts[palmIdx[0]][0], pts[palmIdx[0]][1]);
      palmIdx.forEach((i) => ctx.lineTo(pts[i][0], pts[i][1]));
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      // Finger "tubes" — strokes with glow
      const fingers = [
        [1, 2, 3, 4],
        [5, 6, 7, 8],
        [9, 10, 11, 12],
        [13, 14, 15, 16],
        [17, 18, 19, 20]
      ];

      ctx.lineCap = "round";
      ctx.lineJoin = "round";

      fingers.forEach((chain) => {
        // Glow layer
        ctx.globalAlpha = 0.25;
        ctx.lineWidth = 14;
        ctx.strokeStyle = this.opts.accent;
        ctx.beginPath();
        ctx.moveTo(pts[chain[0]][0], pts[chain[0]][1]);
        chain.forEach((i) => ctx.lineTo(pts[i][0], pts[i][1]));
        ctx.stroke();

        // Mid layer
        ctx.globalAlpha = 0.5;
        ctx.lineWidth = 8;
        ctx.beginPath();
        ctx.moveTo(pts[chain[0]][0], pts[chain[0]][1]);
        chain.forEach((i) => ctx.lineTo(pts[i][0], pts[i][1]));
        ctx.stroke();

        // Core layer
        ctx.globalAlpha = 0.85;
        ctx.lineWidth = 3;
        ctx.strokeStyle = "#67e8f9";
        ctx.beginPath();
        ctx.moveTo(pts[chain[0]][0], pts[chain[0]][1]);
        chain.forEach((i) => ctx.lineTo(pts[i][0], pts[i][1]));
        ctx.stroke();
      });

      ctx.globalAlpha = 1;

      // Fingertip bright cores
      [4, 8, 12, 16, 20].forEach((i) => {
        const [x, y] = pts[i];
        const tipPulse = 1 + Math.sin(this.t * 3 + i) * 0.3;

        ctx.beginPath();
        ctx.fillStyle = "rgba(34, 211, 238, 0.4)";
        ctx.arc(x, y, 10 * tipPulse, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.fillStyle = "#e0f7ff";
        ctx.arc(x, y, 3.5, 0, Math.PI * 2);
        ctx.fill();
      });
    }
  }

  // Expose globally
  window.HandRenderer = HandRenderer;
  window.HoloRenderer = HoloRenderer;
})();
