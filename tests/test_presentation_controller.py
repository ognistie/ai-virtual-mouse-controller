"""Testes do PresentationController (modo apresentacao)."""

from __future__ import annotations

import time

import pytest

# core.presentation_controller importa core.hand_tracker que importa
# mediapipe no top-level. Skip em ambientes sem mediapipe.
pytest.importorskip("mediapipe")

from core.gesture_detector import Gesture
from core.hand_tracker import HandLandmarks
from core.presentation_controller import PresentationController


# ---------------------------------------------------------------------
# Builders de mao
# ---------------------------------------------------------------------

def _peace_lateral_right(offset_x: float = 0.0) -> HandLandmarks:
    """PEACE deitado apontando pra DIREITA (dedos com x crescente).

    Anelar + minimo curvados (TIP perto do MCP).
    Index + middle TIPs a 0.20 do MCP em x (dedo reto).
    """
    base_x = 0.40 + offset_x
    landmarks = [(0.5, 0.5, 0.0)] * 21
    # Wrist + thumb
    landmarks[0] = (base_x - 0.05, 0.5, 0.0)
    landmarks[1] = (base_x - 0.03, 0.52, 0.0)
    landmarks[2] = (base_x - 0.01, 0.53, 0.0)
    landmarks[3] = (base_x + 0.01, 0.54, 0.0)
    landmarks[4] = (base_x + 0.03, 0.55, 0.0)
    # Index (MCP=5, PIP=6, DIP=7, TIP=8) — horizontal pra direita
    landmarks[5] = (base_x, 0.48, 0.0)
    landmarks[6] = (base_x + 0.07, 0.48, 0.0)
    landmarks[7] = (base_x + 0.14, 0.48, 0.0)
    landmarks[8] = (base_x + 0.20, 0.48, 0.0)
    # Middle — horizontal pra direita
    landmarks[9] = (base_x, 0.52, 0.0)
    landmarks[10] = (base_x + 0.07, 0.52, 0.0)
    landmarks[11] = (base_x + 0.14, 0.52, 0.0)
    landmarks[12] = (base_x + 0.20, 0.52, 0.0)
    # Ring — CURVADO (TIP perto do MCP, ratio direta/segmentos baixo)
    landmarks[13] = (base_x, 0.56, 0.0)
    landmarks[14] = (base_x + 0.04, 0.56, 0.0)
    landmarks[15] = (base_x + 0.04, 0.60, 0.0)
    landmarks[16] = (base_x + 0.01, 0.58, 0.0)
    # Pinky — CURVADO
    landmarks[17] = (base_x, 0.60, 0.0)
    landmarks[18] = (base_x + 0.04, 0.60, 0.0)
    landmarks[19] = (base_x + 0.04, 0.64, 0.0)
    landmarks[20] = (base_x + 0.01, 0.62, 0.0)
    return HandLandmarks(
        landmarks=tuple(landmarks), handedness="Right", score=0.95
    )


def _peace_vertical() -> HandLandmarks:
    """PEACE classico (dedos apontando pra CIMA). NAO deve disparar."""
    landmarks = [(0.5, 0.5, 0.0)] * 21
    landmarks[0] = (0.5, 0.70, 0.0)
    landmarks[1] = (0.48, 0.68, 0.0)
    landmarks[2] = (0.46, 0.66, 0.0)
    landmarks[3] = (0.44, 0.64, 0.0)
    landmarks[4] = (0.42, 0.62, 0.0)
    # Index vertical
    landmarks[5] = (0.50, 0.65, 0.0)
    landmarks[6] = (0.50, 0.58, 0.0)
    landmarks[7] = (0.50, 0.51, 0.0)
    landmarks[8] = (0.50, 0.45, 0.0)
    # Middle vertical
    landmarks[9] = (0.54, 0.65, 0.0)
    landmarks[10] = (0.54, 0.58, 0.0)
    landmarks[11] = (0.54, 0.51, 0.0)
    landmarks[12] = (0.54, 0.45, 0.0)
    # Ring curvado
    landmarks[13] = (0.58, 0.65, 0.0)
    landmarks[14] = (0.58, 0.61, 0.0)
    landmarks[15] = (0.61, 0.61, 0.0)
    landmarks[16] = (0.60, 0.64, 0.0)
    # Pinky curvado
    landmarks[17] = (0.62, 0.65, 0.0)
    landmarks[18] = (0.62, 0.61, 0.0)
    landmarks[19] = (0.65, 0.61, 0.0)
    landmarks[20] = (0.64, 0.64, 0.0)
    return HandLandmarks(
        landmarks=tuple(landmarks), handedness="Right", score=0.95
    )


# ---------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------

def test_no_hand_returns_none():
    ctrl = PresentationController()
    assert ctrl.update(None) is None


def test_peace_lateral_alone_arms_but_no_emit():
    # Primeiro frame: arma referencia, nao emite.
    ctrl = PresentationController()
    assert ctrl.update(_peace_lateral_right(offset_x=0.0)) is None


def test_swipe_forward_emits_next_slide():
    ctrl = PresentationController(
        swipe_threshold=0.10, cooldown_s=0.0,
    )
    # Frame 1: arma na posicao x=0.40
    assert ctrl.update(_peace_lateral_right(offset_x=0.0)) is None
    # Frame 2: mao moveu +0.15 na direcao dos dedos (direita) -> NEXT
    result = ctrl.update(_peace_lateral_right(offset_x=0.15))
    assert result is Gesture.NEXT_SLIDE


def test_swipe_backward_emits_prev_slide():
    ctrl = PresentationController(
        swipe_threshold=0.10, cooldown_s=0.0,
    )
    # Frame 1: arma em x=0.20 (deslocado pra direita do default)
    assert ctrl.update(_peace_lateral_right(offset_x=0.20)) is None
    # Frame 2: mao puxou pra tras (x menor, direcao oposta aos dedos)
    result = ctrl.update(_peace_lateral_right(offset_x=0.05))
    assert result is Gesture.PREV_SLIDE


def test_vertical_peace_does_not_trigger():
    ctrl = PresentationController(
        swipe_threshold=0.05, cooldown_s=0.0,
    )
    # PEACE vertical nao satisfaz lateral_ratio -> sem armar
    assert ctrl.update(_peace_vertical()) is None
    # Mesmo se mao "mover" depois, sem armar nao emite
    assert ctrl.update(_peace_vertical()) is None


def test_cooldown_blocks_back_to_back_emits():
    ctrl = PresentationController(
        swipe_threshold=0.10, cooldown_s=10.0,
    )
    ctrl.update(_peace_lateral_right(offset_x=0.0))
    first = ctrl.update(_peace_lateral_right(offset_x=0.15))
    assert first is Gesture.NEXT_SLIDE
    # Segundo swipe imediato deve ser bloqueado pelo cooldown
    second = ctrl.update(_peace_lateral_right(offset_x=0.30))
    assert second is None


def test_losing_hand_resets_arm_state():
    ctrl = PresentationController(
        swipe_threshold=0.10, cooldown_s=0.0,
    )
    ctrl.update(_peace_lateral_right(offset_x=0.0))
    # Mao perdida -> desarma
    ctrl.update(None)
    # Volta mao na MESMA posicao final do swipe — sem ter armado de novo,
    # nao deve emitir mesmo deslocamento aparente
    ctrl.update(_peace_lateral_right(offset_x=0.15))  # rearma aqui
    # So no proximo movimento que pode disparar
    result = ctrl.update(_peace_lateral_right(offset_x=0.30))
    assert result is Gesture.NEXT_SLIDE
