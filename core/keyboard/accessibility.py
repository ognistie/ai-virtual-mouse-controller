"""
core.keyboard.accessibility
===========================

Settings de acessibilidade — escala, contraste, animações reduzidas,
feedback sonoro, compensação de tremor.

Tremor compensation: aumenta agressividade do OneEuroFilter aplicado ao
fingertip antes do hover dispatch. Ver controller.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AccessibilitySettings:
    keyboard_scale: float = 1.0      # 0.6 .. 1.8
    opacity: float = 0.85            # 0.4 .. 1.0
    high_contrast: bool = False
    reduced_motion: bool = False
    audio_feedback: bool = False
    # 0 = off; 1 = leve; 2 = moderado; 3 = forte
    tremor_compensation: int = 0

    def clamp(self) -> None:
        self.keyboard_scale = max(0.6, min(1.8, self.keyboard_scale))
        self.opacity = max(0.4, min(1.0, self.opacity))
        self.tremor_compensation = max(0, min(3, self.tremor_compensation))

    def tremor_one_euro_params(self) -> tuple[float, float]:
        """
        Retorna (min_cutoff, beta) pro OneEuroFilter do fingertip.

        Off (0): valores defaults responsivos.
        Forte (3): cutoff muito baixo + beta menor = filtro pesado.
        """
        presets = [
            (1.2, 0.020),   # 0 — default (mesmo do cursor)
            (0.9, 0.015),   # 1
            (0.6, 0.010),   # 2
            (0.4, 0.006),   # 3
        ]
        return presets[self.tremor_compensation]
