"""
core.keyboard.renderer
======================

Renderer holográfico do Smart Adaptive Holographic Keyboard.

PySide6 + QPainter. Reusa estratégias visuais de hologram_overlay.py:
- 2 layers de glowing stroke (outer halo + core).
- QPainterPath com cornerRadius.
- QRadialGradient pra ripple no press.
- QLinearGradient pra fundo glass.

Layout:
- Caixa do teclado centrada na parte INFERIOR da tela primária.
- Sugestões em pílulas acima do teclado.
- Click-through: WindowTransparentForInput (gestos não geram cliques no SO).
"""

from __future__ import annotations

import logging
import math
import time
from typing import List, Optional, Tuple

from .accessibility import AccessibilitySettings
from .controller import KeyboardController
from .hover import KeyRect
from .models import KeyboardState

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────── Qt soft import

try:
    from PySide6.QtCore import Qt, QPointF, QRectF, QTimer
    from PySide6.QtGui import (
        QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient,
        QPainter, QPainterPath, QPen, QRadialGradient,
    )
    from PySide6.QtWidgets import QApplication, QWidget
    _QT_OK = True
    _QT_ERR: Optional[str] = None
except Exception as e:
    _QT_OK = False
    _QT_ERR = str(e)


# ───────────────────────────────────────────────────────── paleta

COLOR_PRIMARY = "#00FFF0"     # brilho principal
COLOR_SECONDARY = "#00BFFF"   # brilho secundário
COLOR_ACCENT = "#33FFD1"      # destaque
COLOR_BG = "#050B16"          # fundo
COLOR_TEXT = "#E0F7FF"


def _qcolor(hex_str: str, alpha: int = 255) -> "QColor":
    c = QColor(hex_str)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


# ───────────────────────────────────────────────────────── widget


if _QT_OK:

    class _KeyboardWidget(QWidget):

        def __init__(self, renderer: "KeyboardRenderer", target_fps: int) -> None:
            super().__init__()
            self._r = renderer

            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowTransparentForInput
                | Qt.WindowType.NoDropShadowWindowHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

            app = QApplication.instance()
            if app is not None:
                screen = app.primaryScreen()
                if screen is not None:
                    geom = screen.geometry()
                    self.setGeometry(geom)
                    self._r._screen_w = geom.width()
                    self._r._screen_h = geom.height()

            # Timer adaptativo: 60 FPS quando algo anima, 20 FPS idle.
            # Intervalos derivados de target_fps pra respeitar config.
            self._interval_active_ms = max(8, int(1000 / max(20, target_fps)))
            self._interval_idle_ms = max(40, self._interval_active_ms * 3)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(self._interval_active_ms)
            self._idle_frames = 0

        def _tick(self) -> None:
            """Decide se dispara repaint via dirty-check ANTES de chamar
            update(). Se estado igual ao frame anterior → skip update()
            (paintEvent nem dispara → sem flicker).

            Quando _render eh chamado (por update() nosso ou expose system),
            ele SEMPRE pinta completo — Qt entao tem backing store valido."""
            r = self._r
            if not r.state.visible:
                return
            # Garante layout atualizado pra _paint_signature ser estavel
            r._compute_layout()
            sig = r._paint_signature()
            if sig != r._last_paint_sig:
                r._last_paint_sig = sig
                self.update()
                self._idle_frames = 0
            else:
                self._idle_frames += 1
                # 30 frames quietos (~0.5s @ 60FPS) → idle mode
                if (self._idle_frames > 30
                        and self._timer.interval() != self._interval_idle_ms):
                    self._timer.setInterval(self._interval_idle_ms)
                return
            # Animacao ativa — volta pra 60 FPS imediatamente
            if r._has_active_animation():
                if self._timer.interval() != self._interval_active_ms:
                    self._timer.setInterval(self._interval_active_ms)

        def paintEvent(self, event) -> None:  # noqa: N802
            try:
                self._r._render(self)
            except Exception as e:
                logger.debug("paint err: %s", e)


# ───────────────────────────────────────────────────────── renderer


class KeyboardRenderer:
    """
    Computa layout (KeyRect) + desenha. Sincroniza com KeyboardController
    via state compartilhado.
    """

    # Posicionamento. VERTICAL_ANCHOR ∈ [0,1]:
    #   0.0 = topo, 0.42 = levemente acima do centro (ergonomico),
    #   0.5 = centro perfeito, 1.0 = fundo.
    # Default 0.42 compensa offset natural palma↓dedo (mao relaxada
    # cai pra baixo). Independe de resolucao.
    VERTICAL_ANCHOR = 0.42
    KEYBOARD_HEIGHT_RATIO = 0.40     # 40% da altura
    SUGGESTION_HEIGHT_PX = 60

    def __init__(
        self,
        controller: KeyboardController,
        accessibility: AccessibilitySettings,
        target_fps: int = 60,
    ) -> None:
        self.controller = controller
        self.state: KeyboardState = controller.state
        self.accessibility = accessibility
        self._target_fps = target_fps

        self.available = _QT_OK
        if not _QT_OK:
            logger.warning("KeyboardRenderer: PySide6 ausente (%s)", _QT_ERR)
            return

        self._app: Optional["QApplication"] = QApplication.instance()
        if self._app is None:
            import sys
            self._app = QApplication(sys.argv if hasattr(sys, "argv") else [])

        self._screen_w: int = 1920
        self._screen_h: int = 1080
        self._widget: Optional["_KeyboardWidget"] = _KeyboardWidget(self, target_fps)
        self._widget.hide()

        # Cache layout
        self._rects: List[KeyRect] = []
        self._suggestion_rects: List[Tuple[QRectF, int]] = []
        self._cell_size: float = 60.0
        self._kb_origin: Tuple[float, float] = (0.0, 0.0)

        self._start_t = time.perf_counter()
        self._cached_layout_sig: Tuple = ()

        # ── caches de objetos pesados (eliminam alocacoes/frame) ──
        # path por (code, expansion_bucket) — bucket=int(exp*8) faz
        # hit-rate ~95% (expansion muda suavemente via lerp).
        self._path_cache: dict = {}
        # Shadow path cache: (code, expansion_bucket, pass_idx) onde
        # pass_idx 0=penumbra (offset 12), 1=sharp (offset 5). Mesma
        # tecnica do path principal — elimina 124 alocacoes/frame.
        self._shadow_path_cache: dict = {}
        # fonts: criados 1x por _compute_layout (mudam so com cell_size).
        self._font_main: Optional[QFont] = None
        self._font_hint: Optional[QFont] = None
        self._font_sugg: Optional[QFont] = None
        # paint signature do frame anterior — usado pelo dirty-check (F1.3).
        self._last_paint_sig: Tuple = ()

    # ───────────────────────────────────────────────────── public

    def show(self) -> None:
        if not self.available or self._widget is None:
            return
        self.state.visible = True
        self._compute_layout()
        self._widget.show()
        self._widget.raise_()
        self._widget.update()
        # Bomba o event loop algumas vezes pra forcar o expose/paint inicial
        # da janela transparente (Windows so compoe apos os show events).
        if self._app is not None:
            for _ in range(3):
                try:
                    self._app.processEvents()
                except Exception:
                    break

    def hide(self) -> None:
        if not self.available or self._widget is None:
            return
        self.state.visible = False
        self._widget.hide()

    def toggle(self) -> bool:
        if self.state.visible:
            self.hide()
        else:
            self.show()
        return self.state.visible

    def pump(self) -> None:
        if self.available and self._app is not None:
            try:
                self._app.processEvents()
            except Exception:
                pass

    def close(self) -> None:
        if self._widget is not None:
            try:
                self._widget.close()
                self._widget.deleteLater()
            except Exception:
                pass
            self._widget = None
        self.available = False

    # ───────────────────────────────────────────────────── layout

    def _compute_layout(self) -> None:
        """Recalcula KeyRect a partir de KeyLayout + scale + screen."""
        layout = self.state.layout
        sig = (
            layout.name, self._screen_w, self._screen_h,
            self.accessibility.keyboard_scale,
        )
        if sig == self._cached_layout_sig and self._rects:
            return
        self._cached_layout_sig = sig

        scale = self.accessibility.keyboard_scale
        kb_height = self._screen_h * self.KEYBOARD_HEIGHT_RATIO * scale
        cell_h = kb_height / layout.rows
        # Cell size pega min entre h e (largura disponível / cols)
        max_w = self._screen_w * 0.92
        cell_w_by_cols = max_w / layout.cols
        cell_size = min(cell_h, cell_w_by_cols)
        self._cell_size = cell_size

        total_w = layout.cols * cell_size
        total_h = layout.rows * cell_size
        origin_x = (self._screen_w - total_w) / 2.0
        # Centralizacao vertical responsiva — VERTICAL_ANCHOR controla
        # posicionamento (0=topo, 0.5=centro, 1=base). Garante centralizado
        # em qualquer resolucao sem reservar espaco fixo.
        origin_y = (self._screen_h - total_h) * self.VERTICAL_ANCHOR
        self._kb_origin = (origin_x, origin_y)

        # Build rects + state.keys
        # Hit area estendida nas bordas: top row pra cima, bottom row pra
        # baixo. Compensa offset natural da mao (palma cai abaixo do dedo).
        last_row_idx = layout.rows - 1
        extra_h = cell_size * 0.85   # ~uma celula extra de tolerancia
        rects: List[KeyRect] = []
        for k in layout.keys:
            x = origin_x + k.col * cell_size
            y = origin_y + k.row * cell_size
            w = k.width * cell_size
            h = k.height * cell_size
            cx = x + w / 2.0
            cy = y + h / 2.0
            half_h = h / 2.0 - 4.0
            # Hit half-h: padrao = half_h. Top row estende pra cima.
            # Bottom row estende pra baixo. Tudo invisivel ao usuario.
            hit_up = half_h + extra_h if k.row == 0 else half_h
            hit_down = half_h + extra_h if k.row == last_row_idx else half_h
            rects.append(KeyRect(
                key=k, cx=cx, cy=cy,
                half_w=w / 2.0 - 4.0, half_h=half_h,
                hit_half_h_up=hit_up, hit_half_h_down=hit_down,
            ))
            self.state.keys.setdefault(k.code, _new_key_state(k))

        self._rects = rects
        self.controller.set_rects_from(rects, self._screen_w, self._screen_h)

        # Fonts dependem so de cell_size — recomputa 1x quando layout muda.
        self._font_main = QFont("Segoe UI", int(max(10, cell_size * 0.36)))
        self._font_main.setBold(True)
        self._font_hint = QFont("Segoe UI", int(max(8, cell_size * 0.22)))
        self._font_sugg = QFont(
            "Segoe UI", int(max(12, cell_size * 0.30)),
        )
        self._font_sugg.setBold(True)
        # Invalida caches de paths (geometria mudou)
        self._path_cache.clear()
        self._shadow_path_cache.clear()

    # ───────────────────────────────────────────────────── render

    def _has_active_animation(self) -> bool:
        """True se algo precisa repintar nos proximos frames.

        Considerado ativo se: hover_score > 0, expansion nao convergiu,
        ripple ativo, ou arcos orbitais animando (sempre, exceto se
        reduced_motion ON).

        Usado pelo timer adaptativo (F1.5) — false libera CPU pra 20 FPS."""
        # Marker do cursor pulsa enquanto mao visivel → mantem 60 FPS.
        if self.state.cursor_xy is not None:
            return True
        # Dwell progress animando → repaint continuo.
        if self.state.dwell_progress > 0.001:
            return True
        for ks in self.state.keys.values():
            if ks.hover_score > 0.01:
                return True
            if abs(ks.expansion - 1.0) > 0.01:
                return True
            if ks.ripple_t > 0.0:
                return True
        return False

    def _paint_signature(self) -> Tuple:
        """Hash compacto do estado visual. Quantiza floats pra evitar
        invalidacao por ruido de lerp (1e-6 nao muda pixel).

        Inclui ripples ativos — ripple agendado deve repintar todo frame
        ate fade out completo. Inclui tempo quantizado quando arcos
        orbitais ativos (reduced_motion OFF) pra animacao fluida."""
        s = self.state
        keys_sig = tuple(
            (code, int(ks.hover_score * 32), int(ks.expansion * 64),
             int(ks.ripple_t * 100) if ks.ripple_t > 0 else 0)
            for code, ks in s.keys.items()
        )
        mods_sig = (
            s.shift_on, s.caps_on, s.altgr_on, s.ctrl_on, s.alt_on,
        )
        # Tempo quantizado: marker pulsa (5Hz), dwell arc invalida
        # via dwell_progress. Mantemos tick fino apenas se cursor visivel.
        time_sig = (
            int((time.perf_counter() - self._start_t) * 20)
            if self.state.cursor_xy is not None else 0
        )
        # Marker do cursor: quantiza posicao em 4px pra evitar invalidar
        # signature a cada micro-movimento (~250 Hz seria desperdicio).
        cur = s.cursor_xy
        cur_sig = (int(cur[0]) // 4, int(cur[1]) // 4) if cur else None
        # Dwell progress quantizado em 30 niveis — 100ms grain @ 3s.
        dwell_sig = int(s.dwell_progress * 30)
        return (
            s.layout.name, s.hovered_code, s.suggestions,
            mods_sig, keys_sig, time_sig, cur_sig, dwell_sig,
        )

    def _render(self, widget) -> None:
        """Sempre pinta completo quando chamado. Dirty-check vive no timer
        (_tick) que decide se chama update() — evita flicker do backing
        store transparente no Windows quando paintEvent retorna sem pintar."""
        if not self.state.visible:
            return
        self._compute_layout()

        painter = QPainter(widget)
        # AA OFF por default — ligamos so quando precisa (paths/ripple).
        # TextAntialiasing fica ON sempre (texto custa pouco, qualidade muito).
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter) -> None:
        """Desenha o teclado no QPainter dado (widget ou QImage/QPixmap).

        Separado de _render pra permitir render offscreen (preview/teste)
        sem depender do backing store do widget."""
        t = time.perf_counter() - self._start_t
        # Glass panel (F3.1 substitui por halo difuso radial — sem AA)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._draw_glass_panel(painter)
        # Sugestoes: paths arredondados → precisa AA
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_suggestions(painter, t)
        # Teclas: paths arredondados → AA ON (mantem)
        self._draw_keys(painter, t)
        # Marker do dedo POR CIMA das teclas — usuario mira nele.
        self._draw_cursor_marker(painter, t)

    def render_to_image(self, width: Optional[int] = None,
                        height: Optional[int] = None):
        """Renderiza o teclado num QImage ARGB (preview/screenshot/teste)."""
        w = width or self._screen_w
        h = height or self._screen_h
        self._compute_layout()
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        try:
            self._paint(painter)
        finally:
            painter.end()
        return img

    # ───────────────────── glass panel

    def _draw_glass_panel(self, painter) -> None:
        """Infinity Edge: halo radial difuso.

        Sem painel/borda — teclas flutuam sobre nuvem cyan que dissolve no
        BG. Arcos orbitais REMOVIDOS pra visual mais limpo (commit pos-46fc29d).
        """
        if not self._rects:
            return
        ox, oy = self._kb_origin
        layout = self.state.layout
        total_w = layout.cols * self._cell_size
        total_h = layout.rows * self._cell_size
        cx = ox + total_w / 2.0
        cy = oy + total_h / 2.0

        # Halo principal — gradient radial cyan→BG→transparente.
        # Raio 0.50x diagonal = halo concentrado ao redor do teclado,
        # fade total antes de alcancar bordas da tela (Infinity Edge real).
        diag = math.hypot(total_w, total_h)
        max_r = diag * 0.50

        op = self.accessibility.opacity
        grad = QRadialGradient(QPointF(cx, cy), max_r)
        # Centro: cyan MUITO sutil — nao compete com outline das teclas.
        grad.setColorAt(0.0, _qcolor(COLOR_PRIMARY, int(20 * op)))
        # Meio: BG escurecido leve — apenas base discreta sem bordas duras.
        grad.setColorAt(0.40, _qcolor(COLOR_BG, int(90 * op)))
        # Fade out — alpha decresce ate zero (Infinity Edge)
        grad.setColorAt(0.75, _qcolor(COLOR_BG, int(35 * op)))
        grad.setColorAt(1.0, _qcolor(COLOR_BG, 0))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(
            cx - max_r, cy - max_r, max_r * 2.0, max_r * 2.0,
        ))

    # ───────────────────── sugestões

    def _draw_suggestions(self, painter, t: float) -> None:
        sugs = self.state.suggestions
        if not sugs:
            self._suggestion_rects = []
            return
        ox, oy = self._kb_origin
        layout = self.state.layout
        total_w = layout.cols * self._cell_size
        y = oy - self.SUGGESTION_HEIGHT_PX - 4
        h = self.SUGGESTION_HEIGHT_PX - 12
        gap = 12.0
        n = len(sugs)
        # Centraliza
        pill_w = min(220.0, (total_w - gap * (n - 1)) / max(n, 1))
        start_x = ox + (total_w - (pill_w * n + gap * (n - 1))) / 2.0

        self._suggestion_rects = []
        font = QFont("Segoe UI", int(max(12, self._cell_size * 0.30)))
        font.setBold(True)
        painter.setFont(font)

        for i, word in enumerate(sugs):
            rx = start_x + i * (pill_w + gap)
            rect = QRectF(rx, y, pill_w, h)
            path = QPainterPath()
            path.addRoundedRect(rect, h / 2, h / 2)

            # Fill cyan sutil
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0.0, _qcolor(COLOR_PRIMARY, 50))
            grad.setColorAt(1.0, _qcolor(COLOR_SECONDARY, 30))
            painter.setBrush(QBrush(grad))
            painter.setPen(QPen(_qcolor(COLOR_PRIMARY, 180), 1.5))
            painter.drawPath(path)

            # Texto
            painter.setPen(QPen(_qcolor(COLOR_TEXT, 240)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, word)

            self._suggestion_rects.append((rect, i))

    # ───────────────────── teclas

    # Offsets Y de cada pass de sombra. (pass_idx, offset_y, alpha).
    _SHADOW_PASSES = ((0, 12.0, 25), (1, 5.0, 45))

    def _get_shadow_path(self, rect: "KeyRect", expansion: float,
                         pass_idx: int, offset_y: float) -> "QPainterPath":
        """Path da sombra cacheado por (code, exp_bucket, pass_idx)."""
        bucket = int(round((expansion - 1.0) * self._EXP_BUCKETS * 5.0))
        cache_key = (rect.key.code, bucket, pass_idx)
        cached = self._shadow_path_cache.get(cache_key)
        if cached is not None:
            return cached
        half_w_eff = rect.half_w * expansion
        half_h_eff = rect.half_h * expansion
        qrect = QRectF(
            rect.cx - half_w_eff,
            rect.cy - half_h_eff + offset_y,
            half_w_eff * 2.0, half_h_eff * 2.0,
        )
        radius = min(half_w_eff, half_h_eff) * 0.30
        path = QPainterPath()
        path.addRoundedRect(qrect, radius, radius)
        self._shadow_path_cache[cache_key] = path
        if len(self._shadow_path_cache) > 2048:
            self._shadow_path_cache.clear()
        return path

    def _draw_keys_shadow_pass(self, painter) -> None:
        """Sombra projetada de TODAS as teclas via paths cacheados.

        Two passes (offset Y crescente, alpha crescente):
          1. Penumbra difusa (offset 12px, alpha 25)
          2. Sharp shadow (offset 5px, alpha 45)
        Cor COLOR_BG (cyan-escuro) — integra na paleta sem competir
        com glow das teclas.
        """
        painter.setPen(Qt.PenStyle.NoPen)
        for pass_idx, offset_y, alpha in self._SHADOW_PASSES:
            painter.setBrush(QBrush(_qcolor(COLOR_BG, alpha)))
            for rect in self._rects:
                ks = self.state.keys.get(rect.key.code)
                if ks is None:
                    continue
                path = self._get_shadow_path(
                    rect, ks.expansion, pass_idx, offset_y,
                )
                painter.drawPath(path)

    # Quantiza expansion pra bucket discreto. Cache key = (code, bucket).
    # 8 buckets entre 1.0 e ~1.18 → step ~0.025 → diferença visual
    # imperceptível mas garante hit-rate ~95% no path cache.
    _EXP_BUCKETS = 8

    def _get_key_path(self, rect: "KeyRect", expansion: float) -> "QPainterPath":
        """Path roundedrect cacheado por (code, expansion_bucket)."""
        bucket = int(round((expansion - 1.0) * self._EXP_BUCKETS * 5.0))
        cache_key = (rect.key.code, bucket)
        cached = self._path_cache.get(cache_key)
        if cached is not None:
            return cached
        half_w_eff = rect.half_w * expansion
        half_h_eff = rect.half_h * expansion
        qrect = QRectF(
            rect.cx - half_w_eff, rect.cy - half_h_eff,
            half_w_eff * 2.0, half_h_eff * 2.0,
        )
        radius = min(half_w_eff, half_h_eff) * 0.30
        path = QPainterPath()
        path.addRoundedRect(qrect, radius, radius)
        self._path_cache[cache_key] = path
        # Cap defensivo (62 teclas × 8 buckets = 496 max — bem abaixo).
        if len(self._path_cache) > 1024:
            self._path_cache.clear()
        return path

    def _draw_keys(self, painter, t: float) -> None:
        cs = self._cell_size
        # Fonts garantidos em _compute_layout(); guardas defensivas.
        font_main = self._font_main or QFont("Segoe UI", 14)
        font_hint = self._font_hint or QFont("Segoe UI", 9)

        # Sombra direcional (efeito "voando"): desenha PRIMEIRO todas as
        # sombras embaixo de tudo, depois as teclas em cima. Garante que
        # sombra de uma tecla nao corte glow de outra adjacente.
        self._draw_keys_shadow_pass(painter)

        for rect in self._rects:
            k = rect.key
            ks = self.state.keys.get(k.code)
            if ks is None:
                continue
            scale = ks.expansion
            half_w_eff = rect.half_w * scale
            half_h_eff = rect.half_h * scale
            qrect = QRectF(
                rect.cx - half_w_eff, rect.cy - half_h_eff,
                half_w_eff * 2.0, half_h_eff * 2.0,
            )
            path = self._get_key_path(rect, scale)

            # Estado do modificador (caps/shift/altgr/ctrl/alt ativo destaca)
            is_active_mod = (
                (k.code in ("shift", "shift_r") and self.state.shift_on)
                or (k.code == "caps" and self.state.caps_on)
                or (k.code == "altgr" and self.state.altgr_on)
                or (k.code in ("ctrl", "ctrl_r") and self.state.ctrl_on)
                or (k.code in ("alt", "alt_r") and self.state.alt_on)
            )
            hover_t = ks.hover_score

            # Fill — interior MUITO translucido (match referencia visual).
            # Glow vive na borda (_stroke_outline). Interior so respira
            # cyan suave quando hover ativo. Sem hover, fill quase invisivel.
            base_alpha = int(10 + 55 * hover_t + (40 if is_active_mod else 0))
            grad = QLinearGradient(qrect.topLeft(), qrect.bottomRight())
            grad.setColorAt(0.0, _qcolor(COLOR_PRIMARY, base_alpha))
            grad.setColorAt(1.0, _qcolor(
                COLOR_SECONDARY, max(8, base_alpha - 8)
            ))
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)

            # Borda — outline glow (F1.2 decide layers)
            self._stroke_outline(
                painter, path,
                COLOR_PRIMARY if not is_active_mod else COLOR_ACCENT,
                hover_t,
                is_active_mod,
            )

            # Ripple no press (skip se reduced_motion ON)
            if not self.accessibility.reduced_motion:
                self._draw_ripple(painter, rect, ks, t)

            # Texto — branco brilhante. Hover sobe alpha pra 255.
            # Sem hover ainda fica visivel (235) pra leitura confortavel.
            label = self._label_for(k)
            painter.setFont(font_main)
            text_alpha = 255 if hover_t > 0.05 else 235
            painter.setPen(QPen(_qcolor(COLOR_TEXT, text_alpha)))
            painter.drawText(qrect, Qt.AlignmentFlag.AlignCenter, label)

            # AltGr hint (canto inferior esquerdo) — só em modo standard
            if k.label_altgr and not self.accessibility.high_contrast:
                painter.setFont(font_hint)
                hint_rect = QRectF(
                    qrect.left() + 4, qrect.bottom() - cs * 0.30,
                    cs * 0.35, cs * 0.28,
                )
                painter.setPen(QPen(_qcolor(COLOR_ACCENT, 150)))
                painter.drawText(
                    hint_rect,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                    k.label_altgr,
                )

    def _label_for(self, k) -> str:
        if k.role != "char":
            return k.label
        if self.state.altgr_on and k.label_altgr:
            return k.label_altgr
        if (self.state.shift_on ^ self.state.caps_on) and k.label_shift:
            return k.label_shift
        return k.label

    def _stroke_outline(self, painter, path, hex_color: str,
                        hover_t: float, is_active_mod: bool) -> None:
        """Borda neon. Idle = 1 stroke (perf), hover/modifier = 3 strokes
        (halo wide + halo mid + core sharp) pra match referência visual."""
        painter.setBrush(Qt.BrushStyle.NoBrush)

        idle = (hover_t < 0.05) and not is_active_mod
        if idle:
            # Idle: 2 strokes leves (outer halo fino + core) — match
            # referência visual onde TODA tecla tem outline neon visivel.
            # 2 strokes ainda 3x mais barato que o caminho hover (3 strokes wide).
            pen = QPen(_qcolor(hex_color, 65), 4.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)
            pen = QPen(_qcolor(hex_color, 220), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPath(path)
            return

        # Triple stroke pra neon glow forte (referência imagem)
        # Layer 1: outer halo (largo, alpha baixo, simula difusao no ar)
        outer_alpha = int(40 + 80 * hover_t + (60 if is_active_mod else 0))
        pen = QPen(_qcolor(hex_color, outer_alpha), 7.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)
        # Layer 2: mid halo (medio, alpha medio, ponte visual)
        mid_alpha = int(120 + 100 * hover_t + (40 if is_active_mod else 0))
        pen = QPen(_qcolor(hex_color, min(255, mid_alpha)), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)
        # Layer 3: core sharp (fino, brilho maximo, linha precisa)
        core_alpha = int(220 + 35 * hover_t)
        pen = QPen(_qcolor(hex_color, min(255, core_alpha)), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)

    def _draw_cursor_marker(self, painter, t: float) -> None:
        """Anel pulsante + dwell progress arc na posicao do indicador.

        Componentes (z-order de baixo pra cima):
          1. Halo externo difuso (gradient radial).
          2. Anel base (3 strokes glow).
          3. Dwell progress arc — preenche 0→360 conforme state.dwell_progress.
          4. Dot central branco.
        """
        xy = self.state.cursor_xy
        if xy is None:
            return
        x, y = xy
        # Pulso suave 2.5Hz pra atrair olho
        pulse = 1.0 + 0.18 * math.sin(t * 5.0)

        # 1. Halo externo difuso
        r_outer = 22.0 * pulse
        grad = QRadialGradient(QPointF(x, y), r_outer)
        grad.setColorAt(0.0, _qcolor(COLOR_ACCENT, 180))
        grad.setColorAt(0.45, _qcolor(COLOR_PRIMARY, 110))
        grad.setColorAt(1.0, _qcolor(COLOR_PRIMARY, 0))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, y), r_outer, r_outer)

        # 2. Anel base (3 strokes glow) — sempre visivel
        r_ring = 9.0 * pulse
        pen = QPen(_qcolor(COLOR_ACCENT, 90), 5.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(x, y), r_ring, r_ring)
        pen = QPen(_qcolor(COLOR_ACCENT, 200), 2.5)
        painter.setPen(pen)
        painter.drawEllipse(QPointF(x, y), r_ring, r_ring)
        pen = QPen(_qcolor("#FFFFFF", 240), 1.0)
        painter.setPen(pen)
        painter.drawEllipse(QPointF(x, y), r_ring, r_ring)

        # 3. Dwell progress arc (raio maior — orbita o ring base)
        prog = max(0.0, min(1.0, self.state.dwell_progress))
        if prog > 0.01:
            r_arc = 22.0
            # QPainter.drawArc: angulos em 1/16 de grau, start no eixo X (3h),
            # CCW por default. Forcamos start no TOPO (90°) e sentido horario
            # passando span negativo.
            start_angle = 90 * 16  # topo (12 horas)
            span = int(-360 * 16 * prog)
            # Outer glow
            pen = QPen(_qcolor(COLOR_ACCENT, 110), 6.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            rect = QRectF(x - r_arc, y - r_arc, r_arc * 2.0, r_arc * 2.0)
            painter.drawArc(rect, start_angle, span)
            # Sharp core do arco (mais brilhante conforme prog cresce)
            sharp_alpha = int(180 + 75 * prog)
            pen = QPen(_qcolor("#FFFFFF", min(255, sharp_alpha)), 2.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, start_angle, span)

        # 4. Dot central brilhante (ponto de mira)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_qcolor("#FFFFFF", 255)))
        painter.drawEllipse(QPointF(x, y), 2.5, 2.5)

    def _draw_ripple(self, painter, rect: KeyRect, ks, t: float) -> None:
        if ks.ripple_t <= 0.0:
            return
        age = time.perf_counter() - ks.ripple_t
        duration = 0.45
        if age > duration:
            ks.ripple_t = 0.0
            return
        progress = age / duration
        max_r = math.hypot(rect.half_w, rect.half_h) * 1.6
        r = max_r * progress
        alpha = int(220 * (1.0 - progress))
        grad = QRadialGradient(QPointF(rect.cx, rect.cy), r)
        grad.setColorAt(0.0, _qcolor(COLOR_PRIMARY, 0))
        grad.setColorAt(0.7, _qcolor(COLOR_PRIMARY, max(0, alpha // 2)))
        grad.setColorAt(1.0, _qcolor(COLOR_PRIMARY, 0))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(rect.cx, rect.cy), r, r)


def _new_key_state(key):
    """Lazy import pra evitar ciclo na declaração."""
    from .models import KeyState
    return KeyState(key=key)
