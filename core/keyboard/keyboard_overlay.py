"""
core.keyboard.keyboard_overlay
==============================

Façade pública do teclado holográfico — análoga ao HologramOverlay.

API drop-in para o VirtualMouseService:

    kb = KeyboardOverlay(
        layout_name="ABNT2",
        adaptive_profile_path="data/keyboard/adaptive_profile.json",
        dict_path="data/keyboard/dict_pt_br.txt",
    )
    kb.set_enabled(True)
    kb.on_frame(landmarks, gesture, screen_xy)
    kb.pump()
    kb.close()
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Sequence, Tuple

from .accessibility import AccessibilitySettings
from .adaptive import AdaptiveModel
from .controller import KeyboardController
from .layouts import get_layout
from .models import KeyState, KeyboardState
from .output import SystemTyper
from .prediction import TextPredictor
from .renderer import KeyboardRenderer

logger = logging.getLogger(__name__)


class KeyboardOverlay:
    """
    Wrapper de alto nível que junta state + controller + renderer +
    persistência. Mantém API enxuta pro service.
    """

    def __init__(
        self,
        *,
        layout_name: str = "ABNT2",
        target_fps: int = 60,
        adaptive_profile_path: Optional[str] = None,
        dict_path: Optional[str] = None,
        accessibility: Optional[AccessibilitySettings] = None,
        typer_dry_run: bool = False,
        vertical_anchor: float = 0.50,
        dwell_enabled: bool = True,
        dwell_duration_s: float = 3.0,
        dwell_cooldown_s: float = 0.25,
    ) -> None:
        self.available: bool = False
        self.enabled: bool = False

        layout = get_layout(layout_name)
        self.accessibility = accessibility or AccessibilitySettings()
        self.accessibility.clamp()

        # State
        self.state = KeyboardState(layout=layout, keys={})
        for k in layout.keys:
            self.state.keys[k.code] = KeyState(key=k)

        # Dependências
        self._typer = SystemTyper(dry_run=typer_dry_run)
        self._predictor = TextPredictor(dict_path=dict_path)
        self._adaptive = AdaptiveModel(profile_path=adaptive_profile_path)

        # Controller (com config dwell-to-type)
        self.controller = KeyboardController(
            state=self.state,
            typer=self._typer,
            predictor=self._predictor,
            adaptive=self._adaptive,
            accessibility=self.accessibility,
            dwell_enabled=dwell_enabled,
            dwell_duration_s=dwell_duration_s,
            dwell_cooldown_s=dwell_cooldown_s,
        )

        # Renderer (PySide6 — falha graciosamente se ausente)
        self.renderer = KeyboardRenderer(
            controller=self.controller,
            accessibility=self.accessibility,
            target_fps=target_fps,
        )
        # Override do anchor vertical (F2.1 — centralizacao responsiva)
        self.renderer.VERTICAL_ANCHOR = max(0.0, min(1.0, vertical_anchor))
        self.available = self.renderer.available

        # Sugestões iniciais
        self.state.suggestions = self._predictor.suggestions()

    # ───────────────────────────────────────────────────── public API

    def set_enabled(self, enabled: bool) -> None:
        if not self.available:
            return
        if enabled == self.enabled:
            return
        self.enabled = enabled
        if enabled:
            self.renderer.show()
        else:
            self.renderer.hide()

    def toggle(self) -> bool:
        self.set_enabled(not self.enabled)
        return self.enabled

    def set_layout(self, layout_name: str) -> None:
        """Troca layout em runtime."""
        layout = get_layout(layout_name)
        self.state.layout = layout
        self.state.keys = {k.code: KeyState(key=k) for k in layout.keys}
        # Força rebuild do layout no próximo paint
        self.renderer._cached_layout_sig = ()

    def on_frame(
        self,
        landmarks: Optional[Sequence[Tuple[float, float, float]]],
        pinch_now: bool,
        index_tip_screen_xy: Tuple[float, float],
    ) -> None:
        """
        Chamado a cada frame pelo service.

        landmarks: usado só pra checagem de presença (None = sem mão).
        pinch_now: True se gesto pinch raw ativo.
        index_tip_screen_xy: já em px (mesma transform do cursor).
        """
        if not self.available or not self.enabled:
            return
        if landmarks is None:
            return
        self.controller.on_frame(index_tip_screen_xy, pinch_now)

    def pump(self) -> None:
        if self.available:
            self.renderer.pump()

    def close(self) -> None:
        try:
            self._adaptive.save()
        except Exception as e:
            logger.warning("Falha ao salvar adaptive profile: %s", e)
        if self.renderer is not None:
            self.renderer.close()
        self.available = False
        self.enabled = False

    # ───────────────────────────────────────────────────── accessibility

    def refresh_accessibility(self) -> None:
        """Aplica mudanças em self.accessibility (escala, tremor, etc.)."""
        self.accessibility.clamp()
        self.controller.refresh_smoother()
        # Invalida cache de layout
        self.renderer._cached_layout_sig = ()
