"""
core/hologram_overlay.py
========================

Holograma da mao na tela — implementacao PySide6 (alpha compositing real,
radial gradients, antialiased curves, click-through nativo).

Substitui a versao Tkinter mantendo API publica IDENTICA:
    HologramOverlay(...)
    .available, .click_through_active, .enabled
    .set_enabled(bool) / .toggle() -> bool
    .update_pose(landmarks, x, y)
    .update_cursor(x, y)
    .fire_burst(kind)
    .pump()
    .close()

DESIGN INTENCIONAL:
- Esta e' uma EXPERIENCIA SECUNDARIA. Se o PySide6 nao estiver instalado,
  ou se algo travar durante init, .available fica False e o projeto roda
  normalmente sem holograma (so a tecla H deixa de fazer efeito visual).
- Nada na arquitetura base do projeto depende deste modulo. Os modulos
  centrais (camera, hand_tracker, gesture_detector, cursor, smoothing,
  service) continuam funcionando exatamente como antes do holograma existir.

Visual:
- Silhueta da palma + 5 dedos como polygons antialiased
- 3 camadas de outline com alpha real (glow stacking de verdade)
- Veias internas (palma → fingertips) com pulse animado
- Radial gradient halos brilhantes nos fingertips
- 9 particulas com twinkle e glow
- Cursor marker pulsante (landmark 9)
- Bursts ciano/azul nos cliques com fade real
"""

from __future__ import annotations

import logging
import math
import sys
import time
from typing import List, Optional, Sequence, Tuple

from .click_burst import BurstManager
from .hand_renderer import compute_hand_primitives, get_particle_cloud
from .smoothing import OneEuroSmoother2D

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────── PySide6 (soft import)

try:
    from PySide6.QtCore import Qt, QPointF, QTimer
    from PySide6.QtGui import (
        QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen,
        QRadialGradient,
    )
    from PySide6.QtWidgets import QApplication, QWidget
    _PYSIDE_OK = True
    _IMPORT_ERROR = None
except ImportError as e:
    _PYSIDE_OK = False
    _IMPORT_ERROR = str(e)


# ─────────────────────────────────────────────────── pseudo-3D constants

# Direcao da luz virtual (em screen coords). Aponta da fonte de luz
# (upper-left) PARA onde as sombras caem (lower-right).
# Resultado: gradient direcional simula iluminacao tipo HUD futurista.
_LIGHT_DX: float = 0.5
_LIGHT_DY: float = 0.7


# ─────────────────────────────────────────────────────────── helpers


def _qcolor(hex_str: str, alpha: int = 255) -> "QColor":
    """Constroi QColor a partir de hex + alpha. Clampa alpha em [0, 255]
    pra prevenir warnings 'QColor::setAlpha: invalid value' quando
    formulas de animacao produzem valores fora do range."""
    c = QColor(hex_str)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


# ─────────────────────────────────────────────────────────── widget


if _PYSIDE_OK:

    class _HologramWidget(QWidget):
        """
        Widget interno que faz o paint. Le estado do HologramOverlay parent
        e desenha na cada paintEvent (acionado pelo QTimer interno).
        """

        def __init__(self, overlay: "HologramOverlay", target_fps: int) -> None:
            super().__init__()
            self._o = overlay

            # Flags pra janela transparente, sempre no topo, click-through
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

            # Cobrir tela primaria inteira
            app = QApplication.instance()
            if app is not None:
                screen = app.primaryScreen()
                if screen is not None:
                    geom = screen.geometry()
                    self.setGeometry(geom)

            # Timer pra disparar repaint na FPS alvo
            interval_ms = max(8, int(1000 / max(10, target_fps)))
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.update)
            self._timer.start(interval_ms)

        def paintEvent(self, event) -> None:  # noqa: N802
            try:
                self._o._render(self)
            except Exception as e:  # pragma: no cover
                logger.debug("Erro no paint: %s", e)


# ─────────────────────────────────────────────────────────── facade


class HologramOverlay:
    """
    Facade publica do holograma. API identica a versao Tkinter para ser
    drop-in. Toda a parte de rendering acontece em PySide6 internamente.
    """

    # Cor secundaria (right click)
    _DEEP_BLUE = "#0070ff"

    def __init__(
        self,
        *,
        hand_size_px: int = 150,
        opacity: float = 0.85,
        target_fps: int = 24,
        bone_color: str = "#00d4ff",
        point_color: str = "#e0f7ff",
        transparent_color: str = "#010203",  # ignorado em PySide6 (alpha real)
        view_dorsal: bool = True,
        particles_enabled: bool = True,
        particle_count: int = 180,
    ) -> None:
        self.available: bool = False
        self.click_through_active: bool = False
        self._enabled: bool = False

        self._hand_size_px = hand_size_px
        self._opacity = max(0.0, min(1.0, opacity))
        self._target_fps = max(10, target_fps)
        self._bone_color_hex = bone_color
        self._point_color_hex = point_color
        self._deep_blue_hex = self._DEEP_BLUE
        # True = mirror X dos landmarks ao redor do anchor (landmark 9).
        # Vira palm view em dorsal view (costas da mao).
        self._view_dorsal = bool(view_dorsal)

        # Nuvem densa de particles — cached, gerada 1x.
        self._particles_enabled = bool(particles_enabled)
        self._particle_count = max(0, int(particle_count))
        self._cloud = (
            get_particle_cloud(count=self._particle_count, seed=42)
            if self._particles_enabled and self._particle_count > 0
            else ()
        )

        # Pre-converte pra QColor base (alpha aplicado dinamicamente no paint)
        self._screen_w: int = 1920
        self._screen_h: int = 1080

        # Estado da pose
        self._pose_landmarks: Optional[Sequence[Tuple[float, float, float]]] = None
        self._pose_x: float = 0.0
        self._pose_y: float = 0.0
        self._pose_visible: bool = False

        # Smoothing adaptativo dos landmarks usando o MESMO OneEuroFilter
        # do projeto — heavy smoothing quando parado (sem jitter), light
        # smoothing quando em movimento (sem lag perceptivel).
        # 21 filtros 2D (um por landmark), z fica raw.
        # Parametros tunados pra dar suavidade sem atrasar a fluidez do cursor.
        self._lm_smoothers: Optional[List[OneEuroSmoother2D]] = None
        # Tunning para SUAVIDADE consistente (UX agradavel):
        # min_cutoff baixo (0.8): mais smoothing baseline = estavel parada
        # beta moderado (1.5): adapta SEM gerar snaps em movimento
        # Sente como um cursor com inercia suave, sem jumps
        self._lm_smoother_min_cutoff: float = 0.8
        self._lm_smoother_beta: float = 1.5

        # Click anchors atualizados a cada paint
        self._latest_pinch_left: Tuple[float, float] = (0.0, 0.0)
        self._latest_pinch_right: Tuple[float, float] = (0.0, 0.0)
        self._latest_pinch_double: Tuple[float, float] = (0.0, 0.0)

        # Cursor pra idle ring
        self._cursor_x: float = 0.0
        self._cursor_y: float = 0.0
        self._has_cursor: bool = False

        # Sistema de bursts
        self._bursts = BurstManager()

        self._start_time = time.perf_counter()

        # Qt refs
        self._app: Optional["QApplication"] = None
        self._widget: Optional["_HologramWidget"] = None

        if not _PYSIDE_OK:
            logger.warning(
                "HologramOverlay: PySide6 nao instalado (%s). "
                "Holograma desativado — projeto continua funcional sem ele. "
                "Instale com: pip install PySide6",
                _IMPORT_ERROR,
            )
            return

        try:
            self._init_qt()
            self.available = True
            self.click_through_active = True  # nativo via WindowTransparentForInput
        except Exception as e:
            logger.warning(
                "HologramOverlay: falha ao inicializar PySide6 (%s). "
                "Holograma desativado, projeto continua funcional.",
                e,
            )
            self.available = False

    # ───────────────────────────────────────────────────────── setup Qt

    def _init_qt(self) -> None:
        # Pega ou cria QApplication (algumas libs podem ter criado uma)
        self._app = QApplication.instance()
        if self._app is None:
            self._app = QApplication(sys.argv if hasattr(sys, "argv") else [])

        # Tamanho real da tela primaria
        screen = self._app.primaryScreen()
        if screen is not None:
            geom = screen.geometry()
            self._screen_w = geom.width()
            self._screen_h = geom.height()

        self._widget = _HologramWidget(self, self._target_fps)
        # Comeca escondido — toggle on via set_enabled(True)
        self._widget.hide()

    # ───────────────────────────────────────────────────────── public API

    def set_enabled(self, enabled: bool) -> None:
        if not self.available or self._widget is None:
            return
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._widget.show()
            self._widget.raise_()
        else:
            self._widget.hide()
            self._bursts.clear()
            self._pose_visible = False

    def toggle(self) -> bool:
        self.set_enabled(not self._enabled)
        return self._enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def update_pose(
        self,
        landmarks: Optional[Sequence[Tuple[float, float, float]]],
        screen_x: float,
        screen_y: float,
    ) -> None:
        if landmarks is None or len(landmarks) < 21:
            self._pose_visible = False
            self._pose_landmarks = None
            self._lm_smoothers = None  # reset smoothers
            return
        # Suaviza com OneEuroFilter por landmark: adaptativo, sem trade-off
        # ruim entre delay e jitter (mesmo filtro do cursor do projeto).
        smoothed = self._smooth_landmarks(landmarks)
        # Dorsal view: espelha X em torno do anchor (landmark 9).
        # Resultado: hologramo mostra costas da mao em vez de palma.
        # Anchor X fixo → posicao na tela nao muda, so a forma vira.
        if self._view_dorsal:
            anchor_x = smoothed[9][0]
            smoothed = [
                (2.0 * anchor_x - lm[0], lm[1], lm[2])
                for lm in smoothed
            ]
        self._pose_landmarks = smoothed
        self._pose_visible = True
        self._pose_x = screen_x
        self._pose_y = screen_y

    def _smooth_landmarks(
        self,
        raw: Sequence[Tuple[float, float, float]],
    ) -> List[Tuple[float, float, float]]:
        """
        OneEuroFilter por landmark (x, y). z fica raw pra economizar e
        porque a variacao de z e' menos critica visualmente.

        Quando voce esta parado: filtro fica forte → sem tremor.
        Quando voce mexe: filtro abre → sem lag.
        """
        if self._lm_smoothers is None or len(self._lm_smoothers) != len(raw):
            self._lm_smoothers = [
                OneEuroSmoother2D(
                    freq=60.0,
                    min_cutoff=self._lm_smoother_min_cutoff,
                    beta=self._lm_smoother_beta,
                    d_cutoff=1.0,
                )
                for _ in raw
            ]

        out: List[Tuple[float, float, float]] = []
        for smoother, p in zip(self._lm_smoothers, raw):
            sx, sy = smoother(p[0], p[1])
            out.append((sx, sy, p[2]))
        return out

    def update_cursor(self, screen_x: float, screen_y: float) -> None:
        self._cursor_x = screen_x
        self._cursor_y = screen_y
        self._has_cursor = True

    def fire_burst(self, kind: str = "click") -> None:
        """Dispara burst na posicao do pinch correspondente ao gesto."""
        if not self._enabled:
            return
        if self._pose_visible:
            if kind == "right":
                x, y = self._latest_pinch_right
            elif kind == "double":
                x, y = self._latest_pinch_double
            else:
                x, y = self._latest_pinch_left
        elif self._has_cursor:
            x, y = self._cursor_x, self._cursor_y
        else:
            return
        self._bursts.fire(x, y, kind)

    def pump(self) -> None:
        """Bomba o event loop do Qt. Chamado do _tick do service."""
        if not self.available or self._app is None:
            return
        try:
            self._app.processEvents()
        except Exception as e:  # pragma: no cover
            logger.debug("Erro em pump: %s", e)

    def close(self) -> None:
        if self._widget is not None:
            try:
                self._widget.close()
                self._widget.deleteLater()
            except Exception:
                pass
            self._widget = None
        self.available = False
        self._enabled = False

    # ───────────────────────────────────────────────────────── render

    def _render(self, widget: "_HologramWidget") -> None:
        """
        Chamado pelo paintEvent do widget. Desenha tudo no QPainter.
        Le estado de self (pose, bursts, cursor) e renderiza.
        """
        if not self._enabled:
            return

        painter = QPainter(widget)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        try:
            t = time.perf_counter() - self._start_time
            self._bursts.cleanup()

            # CACHE de valores time-based: calculados UMA vez por frame.
            # Antes: cada draw method chamava math.sin separado (~7x/frame).
            # Agora: 2 chamadas de sin → todos os draws compartilham.
            # Resultado: animacoes perfeitamente sincronizadas + menos CPU.
            two_pi_t = t * 6.2831853  # 2*pi*t
            self._anim_pulse = 1.0 + 0.15 * math.sin(two_pi_t * 1.2)
            self._anim_breath = 0.85 + 0.15 * math.sin(two_pi_t * 1.2)
            self._anim_t = t

            if self._pose_visible and self._pose_landmarks is not None:
                prims = compute_hand_primitives(
                    self._pose_landmarks,
                    self._pose_x, self._pose_y,
                    self._hand_size_px,
                )
                # Atualiza pinch points pro fire_burst
                self._latest_pinch_left = prims.pinch_left
                self._latest_pinch_right = prims.pinch_right
                self._latest_pinch_double = prims.pinch_double

                # Z-order otimizado (bottom→top):
                # 1. palma (silhueta com gradient direcional)
                # 2. nuvem densa (particles dentro da silhueta)
                # 3. cursor marker — ANTES dos dedos pra eles ficarem POR CIMA
                # 4. dedos (cobrem o cursor: ponto final do pinch)
                # 5. tip halos (auras radiais)
                # 6. tip cores (pontos brilhantes)
                self._draw_palm(painter, prims.palm, t)
                if self._cloud:
                    self._draw_cloud(painter, t)
                self._draw_cursor_marker(painter, prims.cursor_marker, t)
                self._draw_fingers(painter, prims.fingers, t)
                self._draw_tip_halos(painter, prims.tips, t)
                self._draw_tip_cores(painter, prims.tips, t)
            elif self._has_cursor:
                self._draw_idle_ring(painter, self._cursor_x, self._cursor_y, t)

            self._draw_bursts(painter, t)
        finally:
            painter.end()

    # ───────────────────────────────────── primitivas de paint

    def _path_from_points(self, points) -> "QPainterPath":
        """
        Constroi QPainterPath usando interpolacao Catmull-Rom (convertida
        em segmentos de Bezier cubico). Curvas suaves passando pelos
        vertices, com tensao moderada (0.4) — naturalmente anatomico
        em vez de poligonal anguloso.
        """
        path = QPainterPath()
        n = len(points)
        if n == 0:
            return path
        if n == 1:
            path.moveTo(QPointF(points[0][0], points[0][1]))
            return path

        path.moveTo(QPointF(points[0][0], points[0][1]))
        if n == 2:
            path.lineTo(QPointF(points[1][0], points[1][1]))
            path.closeSubpath()
            return path

        # Catmull-Rom → cubic Bezier. Tension 0.7 = curvas com leve bulge
        # entre vertices = palma arredondada + dedos com volume.
        # Limite superior: acima disso polygons se sobre-curvam.
        tension = 0.7
        factor = tension / 3.0

        for i in range(n):
            p0 = points[(i - 1) % n]
            p1 = points[i]
            p2 = points[(i + 1) % n]
            p3 = points[(i + 2) % n]

            c1x = p1[0] + (p2[0] - p0[0]) * factor
            c1y = p1[1] + (p2[1] - p0[1]) * factor
            c2x = p2[0] - (p3[0] - p1[0]) * factor
            c2y = p2[1] - (p3[1] - p1[1]) * factor

            path.cubicTo(
                QPointF(c1x, c1y),
                QPointF(c2x, c2y),
                QPointF(p2[0], p2[1]),
            )

        return path

    def _make_directional_gradient(
        self, points, hex_color, light_alpha, shadow_alpha,
    ):
        """
        Constroi um QLinearGradient simulando luz vinda do canto superior
        esquerdo. Light side = mais claro/intenso, shadow side = mais
        escuro/transparente. Os polygons preenchidos com isso ganham
        sensacao de volume (pseudo-3D).
        """
        if not points:
            return None
        # Centroide
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        # Raio do bounding circle (extendido um pouco pra suavidade)
        radius = max(
            ((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5
            for p in points
        ) * 1.15
        if radius < 1.0:
            radius = 1.0

        # Normaliza direcao da luz
        norm = (_LIGHT_DX ** 2 + _LIGHT_DY ** 2) ** 0.5
        lx, ly = _LIGHT_DX / norm, _LIGHT_DY / norm

        # Endpoints do gradiente: lado iluminado e lado sombreado
        p_light = (cx - lx * radius, cy - ly * radius)
        p_shadow = (cx + lx * radius, cy + ly * radius)

        grad = QLinearGradient(
            QPointF(*p_light), QPointF(*p_shadow),
        )
        grad.setColorAt(0.0, _qcolor(hex_color, light_alpha))
        grad.setColorAt(0.6, _qcolor(hex_color,
                                      int((light_alpha + shadow_alpha) / 2)))
        grad.setColorAt(1.0, _qcolor(hex_color, shadow_alpha))
        return grad

    def _draw_glowing_stroke(
        self, painter, path, hex_color, width, opacity_scale=1.0,
    ) -> None:
        """
        2 camadas de outline (otimizado de 3 → 2):
        - Outer halo: largura 3x, alpha baixo
        - Core: largura base, alpha alto
        Diferenca visual minima vs 3 camadas, 33% menos paint ops.
        """
        # Outer halo
        pen = QPen(_qcolor(hex_color, int(90 * opacity_scale)), width * 3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)
        # Core
        pen = QPen(_qcolor(hex_color, int(230 * opacity_scale)), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)

    def _draw_palm(self, painter, palm, t):
        if not palm.points:
            return
        path = self._path_from_points(palm.points)

        # Fill com GRADIENT DIRECIONAL. Contrast maior = mais volume 3D.
        # Light side mais brilhante, shadow quase imperceptivel = "respira".
        fill_grad = self._make_directional_gradient(
            palm.points, self._bone_color_hex,
            light_alpha=95, shadow_alpha=6,
        )
        if fill_grad is not None:
            painter.setBrush(QBrush(fill_grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)

        # Glow stack (contorno principal — define a silhueta)
        self._draw_glowing_stroke(
            painter, path, self._bone_color_hex,
            palm.outline_width, self._opacity,
        )

        # MESH INTERNO DA PALMA — wireframe diagonal pra dar impressao
        # de "quads" subdivididos (estilo modelo low-poly da referencia)
        self._draw_palm_wireframe(painter, palm.points)

        # Energy flow no contorno — alpha reduzido pra ser bem discreto
        pen = QPen(_qcolor(self._point_color_hex, 75),
                   max(0.7, palm.outline_width * 0.30))
        pen.setDashPattern([2.0, 14.0])
        pen.setDashOffset(-t * 8.0)
        painter.setPen(pen)
        painter.drawPath(path)

    # Buckets de alpha pra batch render. Mais buckets = mais paint ops
    # mas gradiente mais suave. 5 da equilibrio bom.
    _CLOUD_BUCKET_ALPHAS = (50, 95, 140, 185, 225)

    def _draw_cloud(self, painter, t):
        """
        Renderiza nuvem densa de particles via batch drawPoints.

        Estrategia:
        - Transforma cada particle pra screen coords (host landmark + offset)
        - Computa alpha = base_alpha * twinkle(t, phase)
        - Bucketiza em 5 grupos por alpha range
        - 1 drawPoints call por bucket = 5 paint ops totais (vs N individuais)

        Custo: O(N) na transform + 5 ops paint. Folga absurda.
        """
        landmarks = self._pose_landmarks
        if not self._cloud or landmarks is None or len(landmarks) < 21:
            return

        # Scale igual ao compute_hand_primitives (mantem consistencia visual)
        anchor = landmarks[9]
        ax, ay = anchor[0], anchor[1]
        xs = [lm[0] for lm in landmarks]
        ys = [lm[1] for lm in landmarks]
        bbox_w = max(max(xs) - min(xs), 1e-6)
        bbox_h = max(max(ys) - min(ys), 1e-6)
        scale = self._hand_size_px / max(bbox_w, bbox_h)
        cx, cy = self._pose_x, self._pose_y
        size_px = self._hand_size_px

        # Bucketiza particles por alpha atual
        buckets: list[list[QPointF]] = [[] for _ in range(5)]
        for p in self._cloud:
            host = landmarks[p.host_idx]
            host_sx = cx + (host[0] - ax) * scale
            host_sy = cy + (host[1] - ay) * scale
            px = host_sx + p.offset_x * size_px
            py = host_sy + p.offset_y * size_px
            # Twinkle: range [0.4, 1.0], sempre positivo
            twk = 0.7 + 0.3 * math.sin(t * 2.0 + p.phase)
            alpha = p.base_alpha * twk
            # Bucket idx [0, 4]
            idx = min(4, max(0, int(alpha * 5.0)))
            buckets[idx].append(QPointF(px, py))

        # Render 1 pass por bucket — paint ops totais = 5
        for i, pts in enumerate(buckets):
            if not pts:
                continue
            pen = QPen(
                _qcolor(self._point_color_hex, self._CLOUD_BUCKET_ALPHAS[i]),
                1.8,
            )
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPoints(pts)

    def _draw_palm_wireframe(self, painter, points):
        """
        Mesh interno simplificado (2 diagonais em vez de 3 — mais clean).
        Da impressao de subdivisao quad sem competir visualmente.
        """
        if len(points) < 6:
            return
        pen = QPen(_qcolor(self._bone_color_hex, 45), 0.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 2 diagonais principais — alpha mais baixo = clean
        pairs = ((0, 3), (1, 4))
        for a, b in pairs:
            painter.drawLine(
                QPointF(points[a][0], points[a][1]),
                QPointF(points[b][0], points[b][1]),
            )

    def _draw_fingers(self, painter, fingers, t):
        """
        Dedos OTIMIZADOS:
        - 2 layers de glow (era 3)
        - 1 rib em vez de 2 (so o PIP, junta mais visivel)
        - SEM energy flow dashed (palma ja tem, dedo ficava ruidoso)
        """
        for finger in fingers:
            if not finger.points or len(finger.points) < 3:
                continue
            path = self._path_from_points(finger.points)

            # Fill GRADIENT DIRECIONAL — contrast maior simula cilindricidade
            # dos dedos. Light claro, shadow quase invisivel = volume real.
            fill_grad = self._make_directional_gradient(
                finger.points, self._bone_color_hex,
                light_alpha=85, shadow_alpha=4,
            )
            if fill_grad is not None:
                painter.setBrush(QBrush(fill_grad))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPath(path)

            # Glow stack (2 layers)
            self._draw_glowing_stroke(
                painter, path, self._bone_color_hex,
                finger.outline_width, self._opacity,
            )

            # 1 rib no PIP (junta intermediaria mais visivel)
            self._draw_finger_rib(painter, finger.points)

    def _draw_finger_rib(self, painter, points):
        """
        UMA linha atravessando o dedo no PIP (junta intermediaria).
        Tube polygon = [L0, L1, L2, L3, R3, R2, R1, R0]
        PIP: L1 (1) <-> R1 (6).
        Simplificado de 2 ribs para 1 (mais clean).
        """
        if len(points) < 8:
            return
        pen = QPen(_qcolor(self._bone_color_hex, 60), 0.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(
            QPointF(points[1][0], points[1][1]),
            QPointF(points[6][0], points[6][1]),
        )

    def _draw_tip_halos(self, painter, tips, t):
        """Halos radiais nos fingertips. Breath cached em _render."""
        painter.setPen(Qt.PenStyle.NoPen)
        breath = self._anim_breath  # sincronizado com pulse
        for tip in tips:
            grad = QRadialGradient(QPointF(tip.x, tip.y), tip.halo_radius)
            inner_a = int(180 * breath * self._opacity)
            grad.setColorAt(0.0, _qcolor(self._point_color_hex, inner_a))
            grad.setColorAt(0.4, _qcolor(self._point_color_hex,
                                          int(inner_a * 0.5)))
            grad.setColorAt(1.0, _qcolor(self._point_color_hex, 0))
            painter.setBrush(QBrush(grad))
            painter.drawEllipse(QPointF(tip.x, tip.y),
                                tip.halo_radius, tip.halo_radius)

    def _draw_tip_cores(self, painter, tips, t):
        """Cores brilhantes nos fingertips. Pulse cached em _render."""
        pulse = self._anim_pulse  # sincronizado com halos e cursor
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_qcolor(self._point_color_hex, 255)))
        for tip in tips:
            r = tip.core_radius * pulse
            painter.drawEllipse(QPointF(tip.x, tip.y), r, r)

    def _draw_cursor_marker(self, painter, position, t):
        """
        Marker no PINCH ANCHOR (= ponto exato onde o clique vai cair).
        Desenhado ANTES dos dedos no z-order: os dedos ficam visualmente
        POR CIMA deste marker. Quando voce pincha, os fingertips passam
        em cima desse ponto, reforcando 'aqui e' onde os dedos se encontram'.
        Discreto pra nao competir visualmente com a mao.
        """
        x, y = position
        # Reusa pulse cached (mais leve), aplicando 80% da amplitude
        pulse = 1.0 + (self._anim_pulse - 1.0) * 0.8

        # Halo difuso (pequeno, gentil glow ciano)
        r_halo = 7.5 * pulse
        grad = QRadialGradient(QPointF(x, y), r_halo * 2.0)
        grad.setColorAt(0.0, _qcolor(self._point_color_hex, 90))
        grad.setColorAt(0.5, _qcolor(self._bone_color_hex, 45))
        grad.setColorAt(1.0, _qcolor(self._bone_color_hex, 0))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, y), r_halo * 2.0, r_halo * 2.0)

        # Core central pequeno e brilhante
        r_inner = 2.2
        painter.setBrush(QBrush(_qcolor(self._point_color_hex, 230)))
        painter.drawEllipse(QPointF(x, y), r_inner, r_inner)

    def _draw_idle_ring(self, painter, x, y, t):
        """Anel pulsante no cursor quando overlay ligado sem mao detectada."""
        # Usa pulse cached, amplificado 20% pra mais visibilidade
        pulse = 1.0 + (self._anim_pulse - 1.0) * 1.2
        r = 22.0 * pulse
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_qcolor(self._bone_color_hex, 150), 1.8))
        painter.drawEllipse(QPointF(x, y), r, r)
        # Tiny dot central
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_qcolor(self._point_color_hex, 220)))
        painter.drawEllipse(QPointF(x, y), 2.5, 2.5)

    def _draw_bursts(self, painter, t):
        """Aneis expandindo nos cliques. Alpha real via QColor."""
        snapshots = self._bursts.snapshots()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for snap in snapshots:
            # Mapping: "red" do burst_kind = ciano (left), "cyan" = azul (right)
            base = (
                self._bone_color_hex if snap.color_kind == "red"
                else self._deep_blue_hex
            )
            # Primary ring
            if snap.radius > 0 and snap.alpha > 0.05:
                alpha = int(255 * snap.alpha)
                pen = QPen(_qcolor(base, alpha), snap.width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawEllipse(
                    QPointF(snap.x, snap.y),
                    snap.radius, snap.radius,
                )
            # Secondary (DOUBLE_CLICK)
            if snap.secondary_radius > 0 and snap.secondary_alpha > 0.05:
                alpha2 = int(255 * snap.secondary_alpha)
                pen = QPen(_qcolor(base, alpha2), 2.0)
                painter.setPen(pen)
                painter.drawEllipse(
                    QPointF(snap.x, snap.y),
                    snap.secondary_radius, snap.secondary_radius,
                )
