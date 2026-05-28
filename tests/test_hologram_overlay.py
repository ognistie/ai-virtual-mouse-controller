"""
Testes do overlay holografico.

Foca em funcoes puras + smoke test da construcao. Nao tenta validar a
renderizacao visual (precisaria de display rodando + screenshot diff).
"""

from __future__ import annotations

import sys

import pytest

from core.hologram_overlay import _make_color_with_alpha


class TestMakeColorWithAlpha:
    def test_full_opacity_returns_original(self):
        out = _make_color_with_alpha("#ff0000", 1.0, "#000000")
        assert out == "#ff0000"

    def test_zero_opacity_returns_bg(self):
        out = _make_color_with_alpha("#ff0000", 0.0, "#202020")
        assert out == "#202020"

    def test_half_opacity_blends(self):
        # 50% red + 50% black = #800000
        out = _make_color_with_alpha("#ff0000", 0.5, "#000000")
        # arredondamento pode dar 7f ou 80
        assert out in ("#7f0000", "#800000")

    def test_negative_alpha_clamped_to_zero(self):
        out = _make_color_with_alpha("#ff0000", -1.0, "#000000")
        assert out == "#000000"

    def test_alpha_above_one_clamped(self):
        out = _make_color_with_alpha("#ff0000", 5.0, "#000000")
        assert out == "#ff0000"

    def test_output_is_valid_hex(self):
        out = _make_color_with_alpha("#abcdef", 0.3, "#123456")
        assert len(out) == 7
        assert out.startswith("#")
        int(out[1:], 16)  # parseavel

    def test_intermediate_alpha_between_extremes(self):
        # com cores muito diferentes, alpha=0.5 fica entre ambas
        out = _make_color_with_alpha("#ffffff", 0.5, "#000000")
        r = int(out[1:3], 16)
        # ~127 ou 128
        assert 120 < r < 140


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Overlay e' Windows-only (transparentcolor + WS_EX_TRANSPARENT)",
)
class TestOverlayConstruction:
    """Smoke test: ver se a janela ao menos sobe sem explodir."""

    def test_overlay_constructs_and_closes(self):
        # Importa lazy pra nao quebrar coleta em outros OS
        from core.hologram_overlay import HologramOverlay

        h = HologramOverlay(
            hand_size_px=180,
            opacity=0.4,
            target_fps=30,
        )
        try:
            # No CI sem display, available pode ser False — e' ok.
            # O contrato e' "nao crashar".
            assert isinstance(h.available, bool)
            assert h.enabled is False
        finally:
            h.close()

    def test_update_pose_with_none_is_safe(self):
        from core.hologram_overlay import HologramOverlay

        h = HologramOverlay()
        try:
            h.update_pose(None, 100, 100)
            h.update_pose([(0, 0, 0)] * 5, 100, 100)  # menos que 21
            # nao deve travar
        finally:
            h.close()

    def test_pump_when_disabled_is_noop(self):
        from core.hologram_overlay import HologramOverlay

        h = HologramOverlay()
        try:
            # default: nao habilitado
            for _ in range(5):
                h.pump()  # nao deve crashar
        finally:
            h.close()

    def test_toggle_returns_new_state(self):
        from core.hologram_overlay import HologramOverlay

        h = HologramOverlay()
        try:
            if not h.available:
                pytest.skip("display nao disponivel")
            assert h.enabled is False
            assert h.toggle() is True
            assert h.toggle() is False
        finally:
            h.close()
