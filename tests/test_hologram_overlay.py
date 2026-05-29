"""
Testes do overlay holografico (PySide6).

Roda em modo offscreen (QT_QPA_PLATFORM=offscreen) pra nao precisar de display.
"""

from __future__ import annotations

import os
import sys

import pytest

# Modo offscreen pra QApplication funcionar sem display real
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# Detecta se PySide6 esta instalado pra skip condicional
try:
    import PySide6  # noqa: F401
    _PYSIDE_AVAILABLE = True
except ImportError:
    _PYSIDE_AVAILABLE = False


@pytest.mark.skipif(
    not _PYSIDE_AVAILABLE,
    reason="PySide6 nao instalado",
)
class TestOverlayConstruction:
    def test_overlay_constructs_and_closes(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay(hand_size_px=150, opacity=0.85, target_fps=30)
        try:
            assert isinstance(h.available, bool)
            assert h.enabled is False
        finally:
            h.close()

    def test_available_true_with_pyside(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            assert h.available is True
            # Click-through nativo via WindowTransparentForInput
            assert h.click_through_active is True
        finally:
            h.close()

    def test_update_pose_with_none_is_safe(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            h.update_pose(None, 100, 100)
            h.update_pose([(0, 0, 0)] * 5, 100, 100)  # menos que 21
        finally:
            h.close()

    def test_pump_when_disabled_is_noop(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            for _ in range(5):
                h.pump()
        finally:
            h.close()

    def test_toggle_returns_new_state(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            if not h.available:
                pytest.skip()
            assert h.enabled is False
            assert h.toggle() is True
            assert h.toggle() is False
        finally:
            h.close()

    def test_set_enabled_idempotent(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            if not h.available:
                pytest.skip()
            h.set_enabled(True)
            h.set_enabled(True)
            h.set_enabled(False)
            h.set_enabled(False)
        finally:
            h.close()


@pytest.mark.skipif(
    not _PYSIDE_AVAILABLE,
    reason="PySide6 nao instalado",
)
class TestDorsalView:
    """Mirror X em torno do anchor (landmark 9) quando view_dorsal=True."""

    def test_anchor_x_preserved(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay(view_dorsal=True)
        try:
            if not h.available:
                pytest.skip()
            # landmarks unica chamada — disable smoother resetando depois
            pose = [(0.3 + i * 0.02, 0.5, 0.0) for i in range(21)]
            h.update_pose(pose, 500, 300)
            # Anchor (landmark 9) X deve ser PRESERVADO apos mirror
            anchor_orig_x = pose[9][0]
            anchor_mirrored_x = h._pose_landmarks[9][0]
            assert abs(anchor_mirrored_x - anchor_orig_x) < 0.05
        finally:
            h.close()

    def test_palm_mode_no_mirror(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay(view_dorsal=False)
        try:
            if not h.available:
                pytest.skip()
            pose = [(0.3 + i * 0.02, 0.5, 0.0) for i in range(21)]
            h.update_pose(pose, 500, 300)
            # palm mode: outros landmarks NAO espelhados, OneEuro smoothing
            # converge pra raw values dado tempo
        finally:
            h.close()


@pytest.mark.skipif(
    not _PYSIDE_AVAILABLE,
    reason="PySide6 nao instalado",
)
class TestBurstFiring:
    def test_fire_burst_when_disabled_is_noop(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            if not h.available:
                pytest.skip()
            h.fire_burst("click")
            assert h._bursts.count() == 0
        finally:
            h.close()

    def test_fire_burst_without_pose_uses_cursor(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            if not h.available:
                pytest.skip()
            h.set_enabled(True)
            h.update_cursor(500, 300)
            h.fire_burst("click")
            assert h._bursts.count() == 1
        finally:
            h.close()


@pytest.mark.skipif(
    not _PYSIDE_AVAILABLE,
    reason="PySide6 nao instalado",
)
class TestRenderingNoCrash:
    """Garante que o paint roda sem crashar com varias configuracoes."""

    def test_renders_with_pose(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            if not h.available:
                pytest.skip()
            h.set_enabled(True)
            pose = [(0.5, 0.5, 0.0)] * 21
            pose[0] = (0.5, 0.7, 0.0)
            h.update_pose(pose, 500, 400)
            h.update_cursor(500, 400)
            # Forca varios paints
            for _ in range(5):
                h.pump()
        finally:
            h.close()

    def test_renders_with_bursts(self):
        from core.hologram_overlay import HologramOverlay
        h = HologramOverlay()
        try:
            if not h.available:
                pytest.skip()
            h.set_enabled(True)
            h.update_cursor(500, 400)
            pose = [(0.5, 0.5, 0.0)] * 21
            h.update_pose(pose, 500, 400)
            h.fire_burst("click")
            h.fire_burst("right")
            h.fire_burst("double")
            for _ in range(5):
                h.pump()
        finally:
            h.close()


class TestGracefulDegradation:
    """Quando PySide6 nao esta disponivel, overlay deve falhar com graca."""

    def test_works_without_pyside(self, monkeypatch):
        # Mock para forcar _PYSIDE_OK = False
        import core.hologram_overlay as ho

        monkeypatch.setattr(ho, "_PYSIDE_OK", False)
        monkeypatch.setattr(ho, "_IMPORT_ERROR", "mocked import error")

        h = ho.HologramOverlay()
        assert h.available is False
        assert h.enabled is False
        # Operacoes nao devem crashar
        h.set_enabled(True)
        assert h.enabled is False  # nao mudou porque indisponivel
        h.update_pose(None, 0, 0)
        h.update_cursor(100, 100)
        h.fire_burst("click")
        h.pump()
        h.close()
