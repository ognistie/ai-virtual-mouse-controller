"""
core.runtime_settings  (v6.8)
==============================

Gerenciador central das settings que podem mudar em runtime via UI.

Responsabilidades:
- Manter "recommended" baseline (valores que sabemos que funcionam — v6.5)
- Manter "current" (valores ativos no detector agora)
- Converter slider (0-1) ↔ valor real (ex: DPI, threshold, etc.)
- Aplicar profile presets
- Gerar tabela "Recommended vs Current" para o overlay
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List


# ===================================================================
# RECOMMENDED BASELINE (v6.5 — provadamente funciona)
# ===================================================================

RECOMMENDED_VALUES = {
    "dpi_fixed_multiplier": 0.85,
    "aim_assist_slowdown_factor": 0.40,
    "one_euro_min_cutoff": 1.2,
    "one_euro_beta": 0.020,
    "pinch_distance_threshold": 0.075,
    "sticky_friction_factor": 0.75,
    "anchor_freeze_duration_ms": 50,
    "aim_assist_enabled": True,
    "sticky_targeting_enabled": True,
}


# ===================================================================
# PROFILES
# ===================================================================

PROFILES = {
    "smooth": {
        "dpi_fixed_multiplier": 0.85,
        "aim_assist_slowdown_factor": 0.55,
        "one_euro_min_cutoff": 0.8,
        "one_euro_beta": 0.040,
        "pinch_distance_threshold": 0.075,
        "sticky_friction_factor": 0.75,
        "anchor_freeze_duration_ms": 50,
    },
    "precise": {
        "dpi_fixed_multiplier": 0.70,
        "aim_assist_slowdown_factor": 0.40,
        "one_euro_min_cutoff": 0.6,
        "one_euro_beta": 0.025,
        "pinch_distance_threshold": 0.065,
        "sticky_friction_factor": 0.65,
        "anchor_freeze_duration_ms": 80,
    },
    "responsive": {
        "dpi_fixed_multiplier": 1.0,
        "aim_assist_slowdown_factor": 0.75,
        "one_euro_min_cutoff": 1.5,
        "one_euro_beta": 0.060,
        "pinch_distance_threshold": 0.085,
        "sticky_friction_factor": 0.85,
        "anchor_freeze_duration_ms": 30,
    },
    "stable": {
        "dpi_fixed_multiplier": 0.80,
        "aim_assist_slowdown_factor": 0.50,
        "one_euro_min_cutoff": 0.4,
        "one_euro_beta": 0.020,
        "pinch_distance_threshold": 0.075,
        "sticky_friction_factor": 0.70,
        "anchor_freeze_duration_ms": 60,
    },
}


# ===================================================================
# CONVERSAO SLIDER (0-1) ↔ VALOR REAL
# ===================================================================
# Cada slider mapeia para 1 ou mais settings reais via funcoes lineares.

@dataclass
class SliderMapping:
    """Mapeamento entre slider 0-1 e valor real."""
    setting_keys: List[str]   # quais settings esse slider controla
    min_real: List[float]      # valor real quando slider=0
    max_real: List[float]      # valor real quando slider=1
    invert: List[bool]         # True = slider=0 maps to MAX (ex: aim assist)

    def slider_to_real(self, slider_val: float) -> Dict[str, float]:
        """Retorna {setting_key: real_value} dado slider 0-1."""
        result = {}
        for key, mn, mx, inv in zip(self.setting_keys, self.min_real,
                                     self.max_real, self.invert):
            v = slider_val
            if inv:
                v = 1.0 - v
            result[key] = mn + (mx - mn) * v
        return result

    def real_to_slider(self, real_values: Dict[str, float]) -> float:
        """Inverso: dado valores reais, calcula slider equivalente.
        Usa o primeiro setting como referencia."""
        if not self.setting_keys:
            return 0.5
        key = self.setting_keys[0]
        if key not in real_values:
            return 0.5
        real = real_values[key]
        mn = self.min_real[0]
        mx = self.max_real[0]
        if mx == mn:
            return 0.5
        v = (real - mn) / (mx - mn)
        v = max(0.0, min(1.0, v))
        if self.invert[0]:
            v = 1.0 - v
        return v


SLIDER_MAPPINGS: Dict[str, SliderMapping] = {
    "sensitivity": SliderMapping(
        setting_keys=["dpi_fixed_multiplier"],
        min_real=[0.50],
        max_real=[1.50],
        invert=[False],
    ),
    "aim_assist": SliderMapping(
        # 0 = sem aim assist (slowdown=1.0), 1 = max (slowdown=0.20)
        setting_keys=["aim_assist_slowdown_factor"],
        min_real=[0.20],
        max_real=[1.00],
        invert=[True],
    ),
    "smoothness": SliderMapping(
        # combina min_cutoff e beta
        # 0 = raw (high cutoff, low beta filtragem minima)
        # 1 = smooth (low cutoff, high beta — mais filtragem)
        setting_keys=["one_euro_min_cutoff", "one_euro_beta"],
        min_real=[2.0, 0.005],   # raw
        max_real=[0.4, 0.060],   # smooth
        invert=[False, False],
    ),
    "pinch": SliderMapping(
        # 0 = strict (threshold pequeno = pinch fechado), 1 = loose (threshold grande)
        setting_keys=["pinch_distance_threshold"],
        min_real=[0.050],
        max_real=[0.110],
        invert=[False],
    ),
    "sticky": SliderMapping(
        # 0 = sem sticky (friction=1.0), 1 = forte (friction=0.50)
        setting_keys=["sticky_friction_factor"],
        min_real=[0.50],
        max_real=[1.00],
        invert=[True],
    ),
    "anchor_freeze": SliderMapping(
        # 0 = off (0ms), 1 = long (200ms)
        setting_keys=["anchor_freeze_duration_ms"],
        min_real=[0.0],
        max_real=[200.0],
        invert=[False],
    ),
}


# ===================================================================
# RUNTIME SETTINGS MANAGER
# ===================================================================

class RuntimeSettings:
    """
    Gerencia o estado atual das settings + conversao slider/real.
    """

    def __init__(self, initial_profile: str = "smooth"):
        self._current: Dict[str, float] = {}
        self._aim_enabled: bool = True
        self._sticky_enabled: bool = True
        self._current_profile: str = initial_profile
        self.apply_profile(initial_profile)

    # ---- Acesso aos valores ----

    def get(self, key: str) -> float:
        return self._current.get(key, RECOMMENDED_VALUES.get(key, 0.0))

    def get_all(self) -> Dict[str, float]:
        return dict(self._current)

    @property
    def aim_enabled(self) -> bool:
        return self._aim_enabled

    @property
    def sticky_enabled(self) -> bool:
        return self._sticky_enabled

    @property
    def current_profile(self) -> str:
        return self._current_profile

    # ---- Mudancas via slider ----

    def set_slider(self, slider_key: str, slider_val: float) -> Dict[str, float]:
        """
        Atualiza settings reais a partir de um slider 0-1.
        Returns dict {setting_key: new_value} para o caller propagar.
        """
        mapping = SLIDER_MAPPINGS.get(slider_key)
        if mapping is None:
            return {}
        updates = mapping.slider_to_real(slider_val)
        self._current.update(updates)
        return updates

    def get_slider_value(self, slider_key: str) -> float:
        """Calcula o valor do slider 0-1 a partir do estado atual."""
        mapping = SLIDER_MAPPINGS.get(slider_key)
        if mapping is None:
            return 0.5
        return mapping.real_to_slider(self._current)

    def get_slider_display(self, slider_key: str) -> str:
        """Texto formatado do valor real (ex: '0.85', '60ms')."""
        mapping = SLIDER_MAPPINGS.get(slider_key)
        if mapping is None or not mapping.setting_keys:
            return ""
        primary_key = mapping.setting_keys[0]
        val = self._current.get(primary_key, 0.0)
        # Format especial por setting
        if primary_key == "anchor_freeze_duration_ms":
            return f"{val:.0f}ms"
        if primary_key == "pinch_distance_threshold":
            return f"{val:.3f}"
        return f"{val:.2f}"

    # ---- Toggles ----

    def set_aim_enabled(self, enabled: bool):
        self._aim_enabled = enabled
        self._current["aim_assist_enabled"] = enabled

    def set_sticky_enabled(self, enabled: bool):
        self._sticky_enabled = enabled
        self._current["sticky_targeting_enabled"] = enabled

    # ---- Profile ----

    def apply_profile(self, profile: str):
        """Aplica os valores de um profile."""
        if profile not in PROFILES:
            return
        self._current_profile = profile
        self._current.update(PROFILES[profile])
        self._aim_enabled = True
        self._sticky_enabled = True
        self._current["aim_assist_enabled"] = True
        self._current["sticky_targeting_enabled"] = True

    def reset_profile(self):
        """Reseta para os defaults do profile atual."""
        self.apply_profile(self._current_profile)

    def apply_recommended(self):
        """Aplica os valores 'recommended' (baseline v6.5)."""
        self._current.update(RECOMMENDED_VALUES)
        self._aim_enabled = True
        self._sticky_enabled = True

    # ---- Doc rows (Recommended vs Current) ----

    def get_doc_rows(self):
        """Retorna lista de DocRow para exibir na UI."""
        from .ui_overlay import DocRow

        rows = []

        def fmt(val, key):
            if key == "anchor_freeze_duration_ms":
                return f"{val:.0f}ms"
            if key == "pinch_distance_threshold":
                return f"{val:.3f}"
            return f"{val:.2f}"

        items = [
            ("Sensitivity", "dpi_fixed_multiplier"),
            ("Aim assist", "aim_assist_slowdown_factor"),
            ("Smoothness", "one_euro_min_cutoff"),
            ("Pinch", "pinch_distance_threshold"),
            ("Sticky", "sticky_friction_factor"),
            ("Freeze", "anchor_freeze_duration_ms"),
        ]

        for label, key in items:
            rec = RECOMMENDED_VALUES.get(key, 0.0)
            cur = self._current.get(key, rec)
            rows.append(DocRow(
                label=label,
                recommended=fmt(rec, key),
                current=fmt(cur, key),
            ))

        return rows