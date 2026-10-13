"""RuntimeSettings: perfis, sliders e coerencia entre os dois."""

from __future__ import annotations

import pytest

from core.runtime_settings import (
    PROFILES,
    RECOMMENDED_VALUES,
    SLIDER_MAPPINGS,
    RuntimeSettings,
)


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_profiles_sit_on_smoothness_slider_line(profile):
    """Mexer no slider Suavizacao a partir de um perfil nao pode dar
    salto no beta: o perfil precisa estar (quase) sobre a reta."""
    s = RuntimeSettings(initial_profile=profile)
    slider = s.get_slider_value("smoothness")
    implied = SLIDER_MAPPINGS["smoothness"].slider_to_real(slider)
    assert implied["one_euro_beta"] == pytest.approx(
        s.get("one_euro_beta"),
        rel=0.06,
    )


def test_one_euro_beta_is_in_normalized_scale():
    """beta multiplica velocidade em telas/s (~0.5-3). Valores << 1
    deixam o filtro sem adaptacao (cursor arrasta atras da mao)."""
    for values in (*PROFILES.values(), RECOMMENDED_VALUES):
        assert values["one_euro_beta"] >= 1.0


def test_freeze_slider_roundtrip_in_ms():
    s = RuntimeSettings()
    updates = s.set_slider("anchor_freeze", 0.5)
    assert updates == {"anchor_freeze_duration_ms": pytest.approx(100.0)}
    assert s.get_slider_display("anchor_freeze") == "100ms"
    assert s.get_slider_value("anchor_freeze") == pytest.approx(0.5)


def test_default_profile_keeps_click_freeze_of_120ms():
    assert RuntimeSettings("smooth").get("anchor_freeze_duration_ms") == 120


def test_unknown_profile_is_ignored():
    s = RuntimeSettings("smooth")
    s.apply_profile("nao-existe")
    assert s.current_profile == "smooth"
