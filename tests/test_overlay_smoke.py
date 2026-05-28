"""
Smoke test integrado do HologramOverlay.

Simula um ciclo de uso real: liga, alimenta varias poses, pumpa, desliga.
Nao verifica pixels, so que nao crasha em uso tipico.
"""

from __future__ import annotations

import math
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Overlay e' Windows-only",
)


def _hand_at(cx, cy, scale=0.1, t=0.0):
    """Gera 21 landmarks formando uma "mao" simples animada por t."""
    pts = []
    # wrist
    pts.append((cx, cy + scale * 1.0, 0.0))
    # 5 dedos x 4 juntas, em ventilador a partir do wrist
    for f in range(5):
        angle = math.pi - (f * math.pi / 6) + math.sin(t + f) * 0.05
        for j in range(4):
            dist = scale * (0.3 + 0.18 * j)
            x = cx + math.cos(angle) * dist
            y = cy - math.sin(angle) * dist
            z = -0.05 + 0.02 * j
            pts.append((x, y, z))
    assert len(pts) == 21
    return pts


def test_full_lifecycle():
    from core.hologram_overlay import HologramOverlay

    h = HologramOverlay(hand_size_px=180, opacity=0.4, target_fps=30)
    try:
        if not h.available:
            pytest.skip("display indisponivel")

        # liga
        h.set_enabled(True)
        assert h.enabled is True

        # simula 30 frames de uma mao se movendo
        for i in range(30):
            t = i * 0.05
            # mao oscilando levemente
            pose = _hand_at(0.5, 0.5, scale=0.15, t=t)
            # cursor varrendo a tela
            sx = 200 + i * 20
            sy = 200 + int(50 * math.sin(t * 2))
            h.update_pose(pose, sx, sy)
            h.pump()
            time.sleep(0.01)  # nao bloquear o teste

        # mao "sai do frame"
        for _ in range(5):
            h.update_pose(None, 0, 0)
            h.pump()
            time.sleep(0.01)

        # desliga
        h.set_enabled(False)
        assert h.enabled is False

        # liga de novo, faz mais alguns frames
        h.set_enabled(True)
        for i in range(10):
            pose = _hand_at(0.5, 0.5, scale=0.12, t=i * 0.1)
            h.update_pose(pose, 400, 400)
            h.pump()
            time.sleep(0.01)

    finally:
        h.close()


def test_pump_after_close_is_safe():
    from core.hologram_overlay import HologramOverlay

    h = HologramOverlay()
    h.set_enabled(True) if h.available else None
    h.close()
    # pump nao pode crashar mesmo depois de close()
    h.pump()
    h.pump()


def test_set_enabled_idempotent():
    from core.hologram_overlay import HologramOverlay

    h = HologramOverlay()
    try:
        if not h.available:
            pytest.skip()
        h.set_enabled(True)
        h.set_enabled(True)  # nao deve crashar
        h.set_enabled(False)
        h.set_enabled(False)  # nao deve crashar
    finally:
        h.close()
