"""Testes do painel de ajustes Qt (SettingsPanel).

Constroi widgets PySide6 em modo offscreen — marcado 'gpu' porque
depende de Qt e pode variar em ambientes headless. Rodar com
`pytest --gpu`.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

pytestmark = pytest.mark.gpu

from core.settings_panel import SettingsPanel


class _FakeCallbacks:
    """Registra as chamadas recebidas pra verificar o forwarding."""

    def __init__(self) -> None:
        self.log: list = []

    def on_slider_change(self, key: str, value: float) -> None:
        self.log.append(("slider", key, round(value, 3)))

    def on_profile_change(self, profile: str) -> None:
        self.log.append(("profile", profile))

    def on_toggle(self, key: str, value: bool) -> None:
        self.log.append(("toggle", key, value))

    def on_reset(self) -> None:
        self.log.append(("reset",))

    def on_apply_recommended(self) -> None:
        self.log.append(("rec",))


def _panel(cb=None):
    return SettingsPanel(callbacks=cb or _FakeCallbacks(), width=320, side="right")


def test_constructs_available():
    p = _panel()
    try:
        assert p.available is True
    finally:
        p.close()


def test_forwards_all_callbacks():
    cb = _FakeCallbacks()
    p = _panel(cb)
    try:
        p.on_slider("sensitivity", 0.8)
        p.on_profile_change("precise")
        p.on_toggle("aim", False)
        p.on_reset()
        p.on_apply_recommended()
    finally:
        p.close()
    assert ("slider", "sensitivity", 0.8) in cb.log
    assert ("profile", "precise") in cb.log
    assert ("toggle", "aim", False) in cb.log
    assert ("reset",) in cb.log
    assert ("rec",) in cb.log


def test_sync_does_not_crash():
    p = _panel()
    try:
        p.sync(
            {"sensitivity": 0.5, "aim_assist": 0.6, "smoothness": 0.5,
             "pinch": 0.4, "sticky": 0.7, "anchor_freeze": 0.25},
            {"sensitivity": "1.00", "aim_assist": "0.55", "smoothness": "0.8",
             "pinch": "0.075", "sticky": "0.75", "anchor_freeze": "50ms"},
            aim=True, sticky=True, profile="smooth",
        )
    finally:
        p.close()


def test_open_close_state():
    p = _panel()
    try:
        assert p._open is False
        p.show_panel()
        assert p._open is True
        p.show_panel()  # idempotente
        assert p._open is True
        p.hide_panel()
        assert p._open is False
    finally:
        p.close()


def test_toggle_flips_state():
    p = _panel()
    try:
        p.toggle()
        assert p._open is True
        p.toggle()
        assert p._open is False
    finally:
        p.close()


def test_pump_is_safe():
    p = _panel()
    try:
        for _ in range(3):
            p.pump()
    finally:
        p.close()


def test_close_marks_unavailable():
    p = _panel()
    p.close()
    assert p.available is False
    # Idempotente
    p.close()


class _RuntimeAdapter:
    """Adapter que liga o painel a um RuntimeSettings REAL — replica o
    mapeamento que o service faz, provando o contrato de integracao."""

    def __init__(self, rs) -> None:
        self.rs = rs

    def on_slider_change(self, key: str, value: float) -> None:
        self.rs.set_slider(key, value)

    def on_profile_change(self, profile: str) -> None:
        self.rs.apply_profile(profile)

    def on_toggle(self, key: str, value: bool) -> None:
        if key == "aim":
            self.rs.set_aim_enabled(value)
        elif key == "sticky":
            self.rs.set_sticky_enabled(value)

    def on_reset(self) -> None:
        self.rs.reset_profile()

    def on_apply_recommended(self) -> None:
        self.rs.apply_recommended()


def test_wiring_updates_runtime_settings_end_to_end():
    """Painel -> callbacks -> RuntimeSettings: os ajustes propagam."""
    from core.runtime_settings import RuntimeSettings

    rs = RuntimeSettings(initial_profile="smooth")
    p = SettingsPanel(callbacks=_RuntimeAdapter(rs), width=320, side="right")
    try:
        # Slider sensitivity=1.0 -> dpi_fixed_multiplier no maximo (1.5)
        p.on_slider("sensitivity", 1.0)
        assert rs.get("dpi_fixed_multiplier") == pytest.approx(1.5)

        # Profile precise -> valores do preset aplicados
        p.on_profile_change("precise")
        assert rs.current_profile == "precise"
        assert rs.get("dpi_fixed_multiplier") == pytest.approx(0.70)

        # Toggle aim off propaga
        p.on_toggle("aim", False)
        assert rs.aim_enabled is False

        # Reset volta ao preset do profile atual (e religa os toggles)
        p.on_reset()
        assert rs.aim_enabled is True
        assert rs.get("dpi_fixed_multiplier") == pytest.approx(0.70)
    finally:
        p.close()


def test_slider_display_updates_without_moving_slider():
    p = _panel()
    try:
        p.sync(
            dict.fromkeys(("sensitivity", "aim_assist", "smoothness", "pinch", "sticky", "anchor_freeze"), 0.5),
            dict.fromkeys(("sensitivity", "aim_assist", "smoothness", "pinch", "sticky", "anchor_freeze"), "0.50"),
            aim=True, sticky=True, profile="smooth",
        )
        before = p._panel._sliders["sensitivity"].value()
        p.set_slider_display("sensitivity", "1.42")
        after = p._panel._sliders["sensitivity"].value()
        assert before == after  # display muda, posicao nao
        assert p._panel._slider_vals["sensitivity"].text() == "1.42"
    finally:
        p.close()
