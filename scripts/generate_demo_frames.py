"""
scripts/generate_demo_frames.py
================================

Gera sequencia de PNGs mostrando o fluxo de digitacao:
  01_idle.png      — teclado sem mao detectada
  02_hover_edge.png — dedo proximo da tecla (hover parcial)
  03_hover_full.png — dedo centrado (hover maximo + glow forte)
  04_press.png     — momento do pinch (ripple expandindo)
  05_typed.png     — tecla digitada, ripple fade out, suggestions

Util pra:
  - Demonstrar uso visual sem video.
  - Documentar UX em READMEs/docs.
  - Validar render pipeline offline.

Saida: docs/demo/01..05.png + docs/demo/strip.png (composto horizontal).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.keyboard import KeyboardOverlay  # noqa: E402

try:
    from PySide6.QtGui import (
        QColor, QFont, QImage, QPainter, QPen,
    )
    from PySide6.QtCore import Qt, QPointF, QRectF
except ImportError:
    print("PySide6 indisponivel — abort")
    sys.exit(1)


OUT_DIR = os.path.join(_ROOT, "docs", "demo")
os.makedirs(OUT_DIR, exist_ok=True)

SCREEN_W = 1280
SCREEN_H = 720


def _annotate(img: QImage, title: str, subtitle: str = "") -> QImage:
    """Adiciona titulo + subtitulo no canto superior esquerdo do frame."""
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    # Box semi-transparente
    box = QRectF(20, 20, 460, 70 if subtitle else 50)
    p.fillRect(box, QColor(5, 11, 22, 200))
    p.setPen(QPen(QColor(0, 255, 240, 180), 1.5))
    p.drawRect(box)
    # Titulo
    font = QFont("Segoe UI", 18)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QPen(QColor(224, 247, 255, 255)))
    p.drawText(QRectF(35, 30, 440, 28),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               title)
    if subtitle:
        font2 = QFont("Segoe UI", 11)
        p.setFont(font2)
        p.setPen(QPen(QColor(51, 255, 209, 200)))
        p.drawText(QRectF(35, 58, 440, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   subtitle)
    p.end()
    return img


def _render_state(
    kb: KeyboardOverlay,
    *,
    target_key: Optional[str],
    hover_score: float,
    cursor_xy: Optional[tuple],
    ripple_at: Optional[str] = None,
    suggestions: tuple = (),
    pulsing_t: float = 0.0,
    dwell_progress: float = 0.0,
) -> QImage:
    """Configura state e renderiza frame."""
    # Reset all hover
    for ks in kb.state.keys.values():
        ks.hover_score = 0.0
        ks.expansion = 1.0
        ks.ripple_t = 0.0
    if target_key and target_key in kb.state.keys:
        kb.state.keys[target_key].hover_score = hover_score
        kb.state.keys[target_key].expansion = 1.0 + 0.18 * hover_score
        kb.state.hovered_code = target_key if hover_score > 0.15 else None
    else:
        kb.state.hovered_code = None

    if ripple_at and ripple_at in kb.state.keys:
        # Ripple animando — t=0.18 (40% do ciclo de 0.45s)
        kb.state.keys[ripple_at].ripple_t = time.perf_counter() - 0.18

    kb.state.cursor_xy = cursor_xy
    kb.state.suggestions = suggestions
    kb.state.dwell_progress = max(0.0, min(1.0, dwell_progress))
    # Reset start_t pra controlar fase de arcos/pulse
    kb.renderer._start_t = time.perf_counter() - pulsing_t
    return kb.renderer.render_to_image(SCREEN_W, SCREEN_H)


def main() -> int:
    kb = KeyboardOverlay(
        layout_name="ABNT2",
        typer_dry_run=True,
        dict_path=os.path.join(_ROOT, "data", "keyboard", "dict_pt_br.txt"),
        vertical_anchor=0.42,
    )
    if not kb.available:
        print("KeyboardOverlay indisponivel")
        return 1
    kb.renderer._screen_w = SCREEN_W
    kb.renderer._screen_h = SCREEN_H
    kb.set_enabled(True)
    kb.renderer._compute_layout()

    # Acha rect da tecla "t" pra posicionar marker
    t_rect = next(
        (r for r in kb.renderer._rects if r.key.code == "t"), None,
    )
    space_rect = next(
        (r for r in kb.renderer._rects if r.key.code == "space"), None,
    )
    a_rect = next(
        (r for r in kb.renderer._rects if r.key.code == "a"), None,
    )
    assert t_rect and space_rect and a_rect

    # ── Frame 01 — idle (sem mao)
    img1 = _render_state(
        kb, target_key=None, hover_score=0.0,
        cursor_xy=None, suggestions=(),
        pulsing_t=0.0,
    )
    _annotate(
        img1, "1. Teclado pronto",
        "Mostre a mao pra camera. Marker cyan vai aparecer.",
    )
    img1.save(os.path.join(OUT_DIR, "01_idle.png"))

    # ── Frame 02 — hover edge (dedo proximo, score parcial)
    img2 = _render_state(
        kb, target_key="t", hover_score=0.35,
        cursor_xy=(t_rect.cx + t_rect.half_w * 0.6,
                   t_rect.cy - t_rect.half_h * 0.3),
        suggestions=(),
        pulsing_t=0.5,
    )
    _annotate(
        img2, "2. Aproximando do alvo",
        "Marker entra na area da tecla T. Hover parcial.",
    )
    img2.save(os.path.join(OUT_DIR, "02_hover_edge.png"))

    # ── Frame 03 — hover full + dwell iniciando (~30% progress)
    img3 = _render_state(
        kb, target_key="t", hover_score=0.92,
        cursor_xy=(t_rect.cx, t_rect.cy),
        suggestions=(),
        pulsing_t=1.0,
        dwell_progress=0.30,
    )
    _annotate(
        img3, "3. Mantenha o dedo (1s)",
        "Marker centrado. Arco comeca a preencher no topo.",
    )
    img3.save(os.path.join(OUT_DIR, "03_hover_full.png"))

    # ── Frame 04 — dwell quase completo (~85% progress)
    img4 = _render_state(
        kb, target_key="t", hover_score=0.95,
        cursor_xy=(t_rect.cx, t_rect.cy),
        suggestions=(),
        pulsing_t=1.2,
        dwell_progress=0.85,
    )
    _annotate(
        img4, "4. Quase la (2.5s)",
        "Arco quase completo. Solte se nao quiser confirmar.",
    )
    img4.save(os.path.join(OUT_DIR, "04_press.png"))

    # ── Frame 05 — completou + ripple ativo
    img5 = _render_state(
        kb, target_key="t", hover_score=0.95,
        cursor_xy=(t_rect.cx, t_rect.cy),
        ripple_at="t",
        suggestions=("inteligencia", "interface", "integrado"),
        pulsing_t=1.8,
        dwell_progress=0.0,   # reset apos disparo
    )
    _annotate(
        img5, "5. T digitada — predicao aparece",
        "Ripple confirma. Sugestoes aparecem acima.",
    )
    img5.save(os.path.join(OUT_DIR, "05_typed.png"))

    # ── Strip composto horizontal (5 frames lado a lado)
    strip_w = SCREEN_W // 3
    strip_h = SCREEN_H // 3
    n = 5
    strip = QImage(strip_w * n, strip_h, QImage.Format.Format_ARGB32)
    strip.fill(QColor(5, 11, 22, 255))
    p = QPainter(strip)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    for i, img in enumerate((img1, img2, img3, img4, img5)):
        scaled = img.scaled(
            strip_w, strip_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = i * strip_w + (strip_w - scaled.width()) // 2
        y = (strip_h - scaled.height()) // 2
        p.drawImage(x, y, scaled)
        # Separador
        if i < n - 1:
            p.setPen(QPen(QColor(0, 255, 240, 90), 1))
            p.drawLine((i + 1) * strip_w, 0, (i + 1) * strip_w, strip_h)
    p.end()
    strip.save(os.path.join(OUT_DIR, "strip.png"))

    print(f"OK — {n} frames + strip em {OUT_DIR}/")
    for f in sorted(os.listdir(OUT_DIR)):
        path = os.path.join(OUT_DIR, f)
        size = os.path.getsize(path)
        print(f"  {f}  ({size // 1024} KB)")
    kb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
