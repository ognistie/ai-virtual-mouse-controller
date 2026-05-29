"""
Smoke test integrado do overlay PySide6.
"""

from __future__ import annotations

import math
import os
import sys
import time

import pytest

# Modo offscreen pra Qt funcionar sem display
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


try:
    import PySide6  # noqa: F401
    _PYSIDE_AVAILABLE = True
except ImportError:
    _PYSIDE_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not _PYSIDE_AVAILABLE,
    reason="PySide6 nao instalado",
)


def _hand_at(cx, cy, scale=0.15, t=0.0):
    """21 landmarks formando uma 'mao' animada por t."""
    pts = [(cx, cy, 0.0)] * 21
    pts[0] = (cx, cy + scale * 1.0, 0.0)  # wrist
    pts[1] = (cx - scale * 0.4, cy + scale * 0.7, 0.0)  # thumb CMC
    finger_xs = [-0.65, -0.25, 0.0, 0.25, 0.55]
    for f in range(5):
        base_x = scale * finger_xs[f]
        for j in range(4):
            i = 1 + f * 4 + j
            wave = math.sin(t + f) * 0.04
            pts[i] = (
                cx + base_x + wave * j,
                cy + scale * 0.6 - scale * 0.35 * (j + 1),
                -0.05 + 0.02 * j,
            )
    return pts


def test_full_lifecycle():
    from core.hologram_overlay import HologramOverlay

    h = HologramOverlay(hand_size_px=150, opacity=0.85, target_fps=30)
    try:
        if not h.available:
            pytest.skip()
        h.set_enabled(True)
        assert h.enabled is True

        for i in range(15):
            t = i * 0.05
            pose = _hand_at(0.5, 0.5, t=t)
            sx = 200 + i * 20
            sy = 200 + int(50 * math.sin(t * 2))
            h.update_cursor(sx, sy)
            h.update_pose(pose, sx, sy)
            h.pump()
            time.sleep(0.01)

        # Cliques de varios tipos
        h.fire_burst("click")
        h.fire_burst("right")
        h.fire_burst("double")
        for _ in range(8):
            h.pump()
            time.sleep(0.02)

        # Sem mao
        for _ in range(5):
            h.update_pose(None, 0, 0)
            h.pump()
            time.sleep(0.01)

        h.set_enabled(False)
        assert h.enabled is False

    finally:
        h.close()


def test_pump_after_close_is_safe():
    from core.hologram_overlay import HologramOverlay

    h = HologramOverlay()
    if h.available:
        h.set_enabled(True)
    h.close()
    h.pump()
    h.pump()


def test_paint_cycle_with_pose_runs():
    """Pump dispara repaints — verifica que rodam sem crashar."""
    from core.hologram_overlay import HologramOverlay

    h = HologramOverlay(target_fps=120)
    try:
        if not h.available:
            pytest.skip()
        h.set_enabled(True)
        h.update_cursor(500, 400)
        h.update_pose(_hand_at(0.5, 0.5), 500, 400)
        for _ in range(10):
            h.pump()
            time.sleep(0.01)
    finally:
        h.close()


def test_burst_lifecycle_through_paint():
    """Bursts devem ser criados, animados e removidos via cleanup."""
    from core.hologram_overlay import HologramOverlay

    h = HologramOverlay(target_fps=120)
    try:
        if not h.available:
            pytest.skip()
        h.set_enabled(True)
        h.update_cursor(500, 400)
        h.update_pose(_hand_at(0.5, 0.5), 500, 400)

        # Pumpa pra ter pinch points
        for _ in range(3):
            h.pump()
            time.sleep(0.01)

        h.fire_burst("click")
        assert h._bursts.count() == 1

        # Anima ate expirar (~0.45s pra click)
        end = time.perf_counter() + 0.6
        while time.perf_counter() < end:
            h.pump()
            time.sleep(0.02)

        assert h._bursts.count() == 0  # expirou

    finally:
        h.close()
