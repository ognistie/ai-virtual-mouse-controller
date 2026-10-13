"""
core/hand_soft_renderer.py
==========================

Renderer 2D (QPainter) da mao no MESMO estilo do renderer 3D — fallback
pra maquinas sem OpenGL 3.3 (RDP, VM sem aceleracao).

Usa a mesma pose (`core.hand_pose.PosedHand`) e a mesma paleta
(`core.hand_gl_renderer.HandStyle`), entao proporcoes, posicao e
movimento sao identicos aos do GL — so o sombreamento e' aproximado:

- partes (palma+antebraco, 5 dedos) desenhadas em ordem de PROFUNDIDADE
  (mais longe primeiro) numa imagem offscreen OPACA, depois composta com
  a opacidade da mao: translucidez de camada unica (sem raio-x);
- dedo = segmentos com ponta redonda em camadas (borda, sombra, base,
  realce deslocado pra luz) -> leitura de cilindro;
- palma com gradiente direcional + borda clara;
- antebraco some em fade; brilho nas pontas do polegar/indicador conforme
  a pinca fecha.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from .hand_gl_renderer import STYLE_PRESETS, HandStyle
from .hand_mesh import FINGER_CHAINS, FINGER_RADII, PALM_WRIST_WIDTH
from .hand_pose import LM_INDEX_TIP, LM_THUMB_TIP, PosedHand

try:
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (
        QBrush,
        QColor,
        QImage,
        QLinearGradient,
        QPainter,
        QPainterPath,
        QPen,
        QRadialGradient,
    )

    _PYSIDE_OK = True
except ImportError:  # pragma: no cover - depende do ambiente
    _PYSIDE_OK = False

# Direcao (2D, tela com y pra baixo) apontando PARA a luz: cima-esquerda.
_LIGHT = np.array([-0.47, -0.88])
_PALM_DEPTH_BIAS = 0.35


def _qc(rgb: Sequence[float], alpha: float = 1.0) -> "QColor":
    c = QColor()
    c.setRgbF(
        float(min(1.0, max(0.0, rgb[0]))),
        float(min(1.0, max(0.0, rgb[1]))),
        float(min(1.0, max(0.0, rgb[2]))),
        float(min(1.0, max(0.0, alpha))),
    )
    return c


def _mix(a: Sequence[float], b: Sequence[float], t: float) -> Tuple[float, float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


class SoftHandRenderer:
    """Desenha uma PosedHand num QPainter (coordenadas de tela)."""

    def __init__(self, style: Optional[HandStyle] = None, opacity: float = 0.85) -> None:
        self.style = style or STYLE_PRESETS["quest"]
        self.opacity = max(0.0, min(1.0, float(opacity)))

    # ------------------------------------------------------------ API

    def paint(self, painter: "QPainter", hand: PosedHand) -> None:
        pts = np.asarray(hand.points, dtype=np.float64)
        palm_px = max(1.0, float(hand.palm_px))
        margin = palm_px * 0.45
        x0 = float(pts[:, 0].min() - margin)
        y0 = float(pts[:, 1].min() - margin)
        x1 = float(pts[:, 0].max() + margin)
        y1 = float(pts[:, 1].max() + palm_px * 1.0)
        w = max(1, int(x1 - x0))
        h = max(1, int(y1 - y0))

        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(-x0, -y0)

        parts = self._parts(pts, palm_px)
        for _, draw in sorted(parts, key=lambda item: item[0]):
            draw(p)
        self._wrist_fade(p, pts, palm_px, x0, y0, w, h)
        p.end()

        painter.save()
        painter.setOpacity(self.opacity * self.style.body_alpha)
        painter.drawImage(QPointF(x0, y0), img)
        painter.restore()
        self._pinch_glow(painter, hand)

    # -------------------------------------------------------- partes

    def _parts(self, pts: np.ndarray, palm_px: float):
        parts = []
        # Palma levemente "atras": a base dos dedos sai de cima dela (dedos
        # realmente curvados pra tras continuam atras — diferenca grande).
        palm_depth = float(pts[[0, 5, 9, 13, 17], 2].mean()) - palm_px * _PALM_DEPTH_BIAS
        parts.append((palm_depth, lambda p: self._draw_palm(p, pts, palm_px)))
        for idx, chain in enumerate(FINGER_CHAINS):
            depth = float(pts[list(chain), 2].mean())
            parts.append(
                (
                    depth,
                    lambda p, i=idx, c=chain: self._draw_finger(p, pts, c, i, palm_px),
                )
            )
        return parts

    def _palm_outline(self, pts: np.ndarray, palm_px: float) -> "QPainterPath":
        wrist = pts[0, :2]
        idx_mcp, pky_mcp = pts[5, :2], pts[17, :2]
        mid = (idx_mcp + pky_mcp) * 0.5
        main = mid - wrist
        length = max(float(np.linalg.norm(main)), 1e-6)
        main_u = main / length
        lat = idx_mcp - pky_mcp
        lat = lat - main_u * float(np.dot(lat, main_u))
        lat = lat / max(float(np.linalg.norm(lat)), 1e-6)
        half_w = 0.5 * PALM_WRIST_WIDTH * palm_px
        idx_edge = idx_mcp + lat * FINGER_RADII[1][1] * palm_px
        pky_edge = pky_mcp - lat * FINGER_RADII[4][1] * palm_px
        thumb_side = 1.0 if float(np.dot(pts[1, :2] - wrist, lat)) > 0 else -1.0
        arm = -main_u * length * 0.8
        wrist_a = wrist + lat * half_w
        wrist_b = wrist - lat * half_w
        thenar = wrist + (pts[5, :2] - wrist) * 0.45 + lat * thumb_side * palm_px * 0.22
        top = pts[9, :2] + main_u * palm_px * 0.06
        if thumb_side > 0:
            ring = [wrist_b + arm, wrist_b, pky_edge, top, idx_edge, thenar, wrist_a, wrist_a + arm]
        else:
            ring = [wrist_a + arm, wrist_a, idx_edge, top, pky_edge, thenar, wrist_b, wrist_b + arm]
        path = QPainterPath()
        path.moveTo(QPointF(*ring[0]))
        # Curvas suaves entre os pontos (Catmull-Rom -> Bezier).
        n = len(ring)
        for i in range(n - 1):
            p0 = ring[max(0, i - 1)]
            p1 = ring[i]
            p2 = ring[i + 1]
            p3 = ring[min(n - 1, i + 2)]
            c1 = p1 + (p2 - p0) / 6.0
            c2 = p2 - (p3 - p1) / 6.0
            path.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(*p2))
        path.closeSubpath()
        return path

    def _draw_palm(self, p: "QPainter", pts: np.ndarray, palm_px: float) -> None:
        st = self.style
        path = self._palm_outline(pts, palm_px)
        rect = path.boundingRect()
        c = rect.center()
        r = max(rect.width(), rect.height()) * 0.5
        grad = QLinearGradient(
            QPointF(c.x() + _LIGHT[0] * r, c.y() + _LIGHT[1] * r),
            QPointF(c.x() - _LIGHT[0] * r, c.y() - _LIGHT[1] * r),
        )
        grad.setColorAt(0.0, _qc(_mix(st.base, st.rim, 0.15)))
        grad.setColorAt(0.55, _qc(st.base))
        grad.setColorAt(1.0, _qc(_mix(st.shadow, st.base, 0.45)))
        p.setPen(QPen(_qc(_mix(st.base, st.rim, 0.7)), max(1.2, palm_px * 0.02)))
        p.setBrush(QBrush(grad))
        p.drawPath(path)

    def _draw_finger(
        self,
        p: "QPainter",
        pts: np.ndarray,
        chain,
        idx: int,
        palm_px: float,
    ) -> None:
        st = self.style
        radii = np.asarray(FINGER_RADII[idx][1:], dtype=np.float64) * palm_px
        joints = pts[list(chain), :2].copy()
        if idx == 0:
            # Polegar nasce da tenar (meio caminho teia -> CMC).
            web = pts[0, :2] + (pts[5, :2] - pts[0, :2]) * 0.32
            root = (web + joints[0]) * 0.5
            joints = np.vstack([root[None, :], joints])
            radii = np.concatenate([[FINGER_RADII[0][0] * palm_px], radii])
        # Ponta: recua o centro pra tampa redonda terminar no landmark.
        d = joints[-1] - joints[-2]
        dl = float(np.linalg.norm(d))
        if dl > 1e-6:
            joints[-1] = joints[-1] - d / dl * min(radii[-1] * 0.9, dl * 0.6)
        layers = (
            (1.0 + 0.08, _mix(st.base, st.rim, 0.6), 0.0),  # borda clara
            (1.0, _mix(st.shadow, st.base, 0.55), 0.0),  # lado da sombra
            (0.78, st.base, 0.18),  # corpo
            (0.32, _mix(st.base, st.rim, 0.35), 0.42),  # realce
        )
        for scale, rgb, shift in layers:
            for k in range(len(joints) - 1):
                a, b = joints[k], joints[k + 1]
                seg = b - a
                sl = float(np.linalg.norm(seg))
                normal = np.array([-seg[1], seg[0]]) / sl if sl > 1e-6 else np.zeros(2)
                if float(np.dot(normal, _LIGHT)) < 0:
                    normal = -normal
                rad = float(radii[k] + radii[k + 1]) * 0.5
                off = normal * rad * shift
                pen = QPen(_qc(rgb), 2.0 * rad * scale)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(*(a + off)), QPointF(*(b + off)))

    # --------------------------------------------------------- efeitos

    def _wrist_fade(self, p, pts, palm_px, x0, y0, w, h) -> None:
        wrist = pts[0, :2]
        main = (pts[5, :2] + pts[17, :2]) * 0.5 - wrist
        ml = max(float(np.linalg.norm(main)), 1e-6)
        main_u = main / ml
        start = wrist - main_u * ml * 0.75
        end = wrist - main_u * ml * 0.05
        grad = QLinearGradient(QPointF(*start), QPointF(*end))
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        p.fillRect(QRectF(x0, y0, w, h), QBrush(grad))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _pinch_glow(self, painter: "QPainter", hand: PosedHand) -> None:
        if hand.pinch <= 0.02:
            return
        radius = max(3.0, hand.palm_px * 0.22)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for lm in (LM_THUMB_TIP, LM_INDEX_TIP):
            x, y = float(hand.points[lm][0]), float(hand.points[lm][1])
            g = QRadialGradient(QPointF(x, y), radius)
            g.setColorAt(0.0, _qc(self.style.glow, 0.85 * hand.pinch * self.opacity))
            g.setColorAt(1.0, _qc(self.style.glow, 0.0))
            painter.setBrush(QBrush(g))
            painter.drawEllipse(QPointF(x, y), radius, radius)
        painter.restore()


def paint_parts_order(hand: PosedHand) -> List[str]:
    """Ordem de desenho (mais longe primeiro) — exposto pra testes."""
    pts = np.asarray(hand.points)
    names = ["palm", "thumb", "index", "middle", "ring", "pinky"]
    depths = [float(pts[[0, 5, 9, 13, 17], 2].mean())] + [
        float(pts[list(c), 2].mean()) for c in FINGER_CHAINS
    ]
    return [n for _, n in sorted(zip(depths, names), key=lambda t: t[0])]
