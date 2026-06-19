"""
core.presentation_controller
=============================

Modo apresentacao: passa slides com gestos em vez de controlar o cursor.

Gesto: MAO ABERTA cruzando do MEIO do frame pra um dos LADOS.

    |--LEFT--|----MIDDLE (neutro)----|--RIGHT--|
    0       lo                       hi        1
    PREV         (nao dispara)            NEXT

- Cruzou MEIO -> LEFT  -> PREV_SLIDE
- Cruzou MEIO -> RIGHT -> NEXT_SLIDE
- Mao parada num lado nao re-dispara; voltar pro MEIO rearma

Frame ja vem espelhado pelo service (mirror=True), entao x cresce pra
a direita na vista do usuario.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.finger_posture import (
    FINGER_CHAIN_INDEX,
    FINGER_CHAIN_MIDDLE,
    FINGER_CHAIN_PINKY,
    FINGER_CHAIN_RING,
    finger_extension,
    is_clearly_extended,
)
from core.gesture_detector import Gesture
from core.hand_tracker import HandLandmarks


logger = logging.getLogger(__name__)


# Middle MCP: centro estavel da palma, mais robusto que TIP a oclusao
_LM_MIDDLE_MCP = 9


class Zone(Enum):
    LEFT = "left"
    MIDDLE = "middle"
    RIGHT = "right"


@dataclass
class DebugSnapshot:
    """Estado do ultimo frame, exposto pra overlay visual."""
    has_hand: bool = False
    idx_ext: float = 0.0
    mid_ext: float = 0.0
    ring_ext: float = 0.0
    pinky_ext: float = 0.0
    open_hand: bool = False
    anchor_x: float = 0.5
    zone: Zone = Zone.MIDDLE


class PresentationController:
    """Detecta MAO ABERTA cruzando do meio pra um lado -> NEXT/PREV_SLIDE."""

    def __init__(
        self,
        *,
        dead_zone: float = 0.30,
        cooldown_s: float = 0.5,
        extension_threshold: float = 0.80,
    ) -> None:
        """
        Args:
            dead_zone: Largura da zona neutra central (fracao do frame).
                       0.20 = 20% central nao dispara. Aumente pra
                       'buffer' maior entre LEFT/RIGHT.
            cooldown_s: Tempo minimo entre disparos consecutivos.
            extension_threshold: Score minimo de extensao dos 4 dedos
                                 longos pra reconhecer MAO ABERTA.
        """
        self._dead_zone = max(0.0, min(0.8, dead_zone))
        self._cooldown = cooldown_s
        self._ext_th = extension_threshold
        self._lo = 0.5 - self._dead_zone / 3.0
        self._hi = 0.5 + self._dead_zone / 3.0
        self._last_zone: Zone = Zone.MIDDLE
        self._last_emit_t: float = 0.0
        self._debug = DebugSnapshot()

    def reset(self) -> None:
        self._last_zone = Zone.MIDDLE
        self._last_emit_t = 0.0
        self._debug = DebugSnapshot()

    @property
    def debug(self) -> DebugSnapshot:
        return self._debug

    @property
    def dead_zone_bounds(self) -> tuple[float, float]:
        """Retorna (lo, hi) normalizado [0,1] das bordas da zona neutra."""
        return self._lo, self._hi

    def update(self, hand: Optional[HandLandmarks]) -> Optional[Gesture]:
        """Processa um frame. Retorna NEXT_SLIDE / PREV_SLIDE ou None."""
        if hand is None:
            self._last_zone = Zone.MIDDLE
            self._debug = DebugSnapshot()
            return None

        lm = hand.landmarks
        idx_ext = finger_extension(lm, FINGER_CHAIN_INDEX)
        mid_ext = finger_extension(lm, FINGER_CHAIN_MIDDLE)
        ring_ext = finger_extension(lm, FINGER_CHAIN_RING)
        pinky_ext = finger_extension(lm, FINGER_CHAIN_PINKY)
        open_hand = (
            is_clearly_extended(idx_ext, threshold=self._ext_th)
            and is_clearly_extended(mid_ext, threshold=self._ext_th)
            and is_clearly_extended(ring_ext, threshold=self._ext_th)
            and is_clearly_extended(pinky_ext, threshold=self._ext_th)
        )
        anchor_x = lm[_LM_MIDDLE_MCP][0]
        zone = self._zone_of(anchor_x)

        self._debug = DebugSnapshot(
            has_hand=True,
            idx_ext=idx_ext, mid_ext=mid_ext,
            ring_ext=ring_ext, pinky_ext=pinky_ext,
            open_hand=open_hand,
            anchor_x=anchor_x,
            zone=zone,
        )

        if not open_hand:
            # Fechar a mao rearma o gatilho — sem isto, reabrir num lado
            # ja "presente" dispararia sem o usuario ter atravessado o meio.
            self._last_zone = Zone.MIDDLE
            return None

        emitted = self._maybe_emit(zone)
        self._last_zone = zone
        return emitted

    def _zone_of(self, x: float) -> Zone:
        if x < self._lo:
            return Zone.LEFT
        if x > self._hi:
            return Zone.RIGHT
        return Zone.MIDDLE

    def _maybe_emit(self, zone: Zone) -> Optional[Gesture]:
        if self._last_zone != Zone.MIDDLE or zone == Zone.MIDDLE:
            return None
        now = time.perf_counter()
        if now - self._last_emit_t < self._cooldown:
            return None
        emitted = Gesture.NEXT_SLIDE if zone == Zone.RIGHT else Gesture.PREV_SLIDE
        self._last_emit_t = now
        logger.info("Presentation: meio -> %s", emitted.value)
        return emitted
