"""
core.presentation_controller
=============================

Modo apresentacao: passa slides com gestos em vez de controlar o cursor.

Gesto: PEACE deitado (indice + medio extendidos APONTANDO LATERALMENTE,
nao pra cima). Anelar e minimo curvados.

Swipe (mao-agnostico, independe de destro/canhoto):
- Mover a mao NA DIRECAO em que os dedos apontam = NEXT_SLIDE
- Mover a mao na DIRECAO OPOSTA (puxar pra tras) = PREV_SLIDE

Estado:
- IDLE      : sem PEACE deitado -> nao faz nada
- ARMED     : PEACE deitado detectado, posicao de referencia gravada
- COOLDOWN  : acabou de disparar, aguarda PRESENTATION_COOLDOWN_S
             antes de aceitar novo disparo

Design:
- Modulo desacoplado: nao depende do GestureDetector principal nem do
  CursorController. Recebe HandLandmarks por frame, retorna Optional[Gesture].
- Direcao de referencia = vetor MCP->TIP medio (indice + medio).
  Robusto: usar TIP-MCP em vez de TIP-WRIST elimina contribuicao da pose
  do braco, fica preso a orientacao real dos dedos.
- Threshold de swipe usa coordenadas normalizadas (0-1) do frame, escala
  com tamanho da mao no enquadramento (curto = ainda detecta swipe).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from core.finger_posture import (
    FINGER_CHAIN_INDEX,
    FINGER_CHAIN_MIDDLE,
    FINGER_CHAIN_PINKY,
    FINGER_CHAIN_RING,
    finger_extension,
    is_clearly_curled,
    is_clearly_extended,
)
from core.gesture_detector import Gesture
from core.hand_tracker import HandLandmarks


logger = logging.getLogger(__name__)


# Landmarks usados pra computar direcao dos dedos
_LM_INDEX_MCP = 5
_LM_INDEX_TIP = 8
_LM_MIDDLE_MCP = 9
_LM_MIDDLE_TIP = 12


@dataclass
class _SwipeState:
    armed: bool = False
    ref_x: float = 0.0
    ref_y: float = 0.0
    ref_dir_x: float = 0.0
    ref_dir_y: float = 0.0
    last_emit_t: float = 0.0


class PresentationController:
    """Detecta PEACE deitado + swipe direcional e emite NEXT/PREV_SLIDE."""

    def __init__(
        self,
        *,
        lateral_ratio: float = 1.3,
        swipe_threshold: float = 0.12,
        cooldown_s: float = 0.8,
        extension_threshold: float = 0.88,
        curl_threshold: float = 0.70,
    ) -> None:
        self._ratio = lateral_ratio
        self._threshold = swipe_threshold
        self._cooldown = cooldown_s
        self._ext_th = extension_threshold
        self._curl_th = curl_threshold
        self._state = _SwipeState()

    def reset(self) -> None:
        self._state = _SwipeState()

    def update(self, hand: Optional[HandLandmarks]) -> Optional[Gesture]:
        """Processa um frame. Retorna Gesture.NEXT_SLIDE / PREV_SLIDE ou None."""
        if hand is None:
            self._state.armed = False
            return None

        if not self._is_peace_lateral(hand):
            self._state.armed = False
            return None

        lm = hand.landmarks
        anchor_x = (lm[_LM_INDEX_MCP][0] + lm[_LM_MIDDLE_MCP][0]) * 0.5
        anchor_y = (lm[_LM_INDEX_MCP][1] + lm[_LM_MIDDLE_MCP][1]) * 0.5
        dir_x, dir_y = self._finger_direction(hand)

        now = time.perf_counter()

        if not self._state.armed:
            self._state = _SwipeState(
                armed=True,
                ref_x=anchor_x,
                ref_y=anchor_y,
                ref_dir_x=dir_x,
                ref_dir_y=dir_y,
                last_emit_t=self._state.last_emit_t,
            )
            return None

        if now - self._state.last_emit_t < self._cooldown:
            return None

        # Deslocamento projetado na direcao dos dedos (referencia inicial).
        # Sinal positivo = mao moveu PRA FRENTE (mesma direcao que dedos
        # apontam) -> next. Sinal negativo = puxou pra tras -> prev.
        dx = anchor_x - self._state.ref_x
        dy = anchor_y - self._state.ref_y
        projection = dx * self._state.ref_dir_x + dy * self._state.ref_dir_y

        if abs(projection) < self._threshold:
            return None

        emitted = Gesture.NEXT_SLIDE if projection > 0 else Gesture.PREV_SLIDE
        # Rearmar com a posicao atual: usuario pode continuar swipando
        # mas espera cooldown antes de novo disparo.
        self._state.ref_x = anchor_x
        self._state.ref_y = anchor_y
        self._state.last_emit_t = now
        logger.info(
            "Presentation swipe: %s (projection=%.3f, threshold=%.3f)",
            emitted.value,
            projection,
            self._threshold,
        )
        return emitted

    def _is_peace_lateral(self, hand: HandLandmarks) -> bool:
        lm = hand.landmarks
        idx_ext = finger_extension(lm, FINGER_CHAIN_INDEX)
        mid_ext = finger_extension(lm, FINGER_CHAIN_MIDDLE)
        ring_ext = finger_extension(lm, FINGER_CHAIN_RING)
        pinky_ext = finger_extension(lm, FINGER_CHAIN_PINKY)

        peace_posture = (
            is_clearly_extended(idx_ext, threshold=self._ext_th)
            and is_clearly_extended(mid_ext, threshold=self._ext_th)
            and is_clearly_curled(ring_ext, threshold=self._curl_th)
            and is_clearly_curled(pinky_ext, threshold=self._curl_th)
        )
        if not peace_posture:
            return False

        dir_x, dir_y = self._finger_direction(hand)
        # Lateral = vetor predominantemente horizontal
        return abs(dir_x) >= self._ratio * abs(dir_y)

    @staticmethod
    def _finger_direction(hand: HandLandmarks) -> tuple[float, float]:
        """Vetor unitario da direcao indice+medio (MCP -> TIP, media)."""
        lm = hand.landmarks
        ix = lm[_LM_INDEX_TIP][0] - lm[_LM_INDEX_MCP][0]
        iy = lm[_LM_INDEX_TIP][1] - lm[_LM_INDEX_MCP][1]
        mx = lm[_LM_MIDDLE_TIP][0] - lm[_LM_MIDDLE_MCP][0]
        my = lm[_LM_MIDDLE_TIP][1] - lm[_LM_MIDDLE_MCP][1]
        vx = (ix + mx) * 0.5
        vy = (iy + my) * 0.5
        mag = (vx * vx + vy * vy) ** 0.5
        if mag < 1e-9:
            return 0.0, 0.0
        return vx / mag, vy / mag
