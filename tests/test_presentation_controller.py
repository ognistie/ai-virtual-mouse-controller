"""Testes do PresentationController (modo apresentacao por zonas)."""

from __future__ import annotations

import pytest

# core.presentation_controller importa core.hand_tracker que importa
# mediapipe no top-level. Skip em ambientes sem mediapipe.
pytest.importorskip("mediapipe")

from core.gesture_detector import Gesture
from core.hand_tracker import HandLandmarks
from core.presentation_controller import PresentationController, Zone


# ---------------------------------------------------------------------
# Builders de mao
# Anchor (middle MCP, landmark 9) ficara em x = 0.50 + offset_x.
# Com dead_zone=0.20 (default): LEFT < 0.40, MIDDLE [0.40,0.60], RIGHT > 0.60
# Logo offset_x:
#   < -0.10 -> LEFT
#   [-0.10, 0.10] -> MIDDLE
#   > 0.10 -> RIGHT
# ---------------------------------------------------------------------

def _open_hand(offset_x: float = 0.0) -> HandLandmarks:
    """Mao ABERTA (todos os 4 dedos longos extendidos verticalmente)."""
    base_x = 0.50 + offset_x
    landmarks = [(base_x, 0.50, 0.0)] * 21
    landmarks[0] = (base_x, 0.78, 0.0)
    landmarks[1] = (base_x - 0.04, 0.72, 0.0)
    landmarks[2] = (base_x - 0.07, 0.67, 0.0)
    landmarks[3] = (base_x - 0.09, 0.63, 0.0)
    landmarks[4] = (base_x - 0.11, 0.60, 0.0)
    landmarks[5] = (base_x - 0.03, 0.65, 0.0)
    landmarks[6] = (base_x - 0.03, 0.55, 0.0)
    landmarks[7] = (base_x - 0.03, 0.47, 0.0)
    landmarks[8] = (base_x - 0.03, 0.40, 0.0)
    landmarks[9] = (base_x, 0.65, 0.0)
    landmarks[10] = (base_x, 0.55, 0.0)
    landmarks[11] = (base_x, 0.47, 0.0)
    landmarks[12] = (base_x, 0.38, 0.0)
    landmarks[13] = (base_x + 0.03, 0.65, 0.0)
    landmarks[14] = (base_x + 0.03, 0.56, 0.0)
    landmarks[15] = (base_x + 0.03, 0.48, 0.0)
    landmarks[16] = (base_x + 0.03, 0.41, 0.0)
    landmarks[17] = (base_x + 0.06, 0.66, 0.0)
    landmarks[18] = (base_x + 0.06, 0.58, 0.0)
    landmarks[19] = (base_x + 0.06, 0.52, 0.0)
    landmarks[20] = (base_x + 0.06, 0.46, 0.0)
    return HandLandmarks(
        landmarks=tuple(landmarks), handedness="Right", score=0.95
    )


def _fist(offset_x: float = 0.0) -> HandLandmarks:
    """Punho fechado. NAO deve disparar."""
    base_x = 0.50 + offset_x
    landmarks = [(base_x, 0.50, 0.0)] * 21
    landmarks[0] = (base_x, 0.65, 0.0)
    landmarks[5] = (base_x - 0.03, 0.58, 0.0)
    landmarks[6] = (base_x - 0.03, 0.52, 0.0)
    landmarks[7] = (base_x - 0.03, 0.55, 0.0)
    landmarks[8] = (base_x - 0.03, 0.59, 0.0)
    landmarks[9] = (base_x, 0.58, 0.0)
    landmarks[10] = (base_x, 0.52, 0.0)
    landmarks[11] = (base_x, 0.55, 0.0)
    landmarks[12] = (base_x, 0.59, 0.0)
    landmarks[13] = (base_x + 0.03, 0.58, 0.0)
    landmarks[14] = (base_x + 0.03, 0.52, 0.0)
    landmarks[15] = (base_x + 0.03, 0.55, 0.0)
    landmarks[16] = (base_x + 0.03, 0.59, 0.0)
    landmarks[17] = (base_x + 0.06, 0.58, 0.0)
    landmarks[18] = (base_x + 0.06, 0.52, 0.0)
    landmarks[19] = (base_x + 0.06, 0.55, 0.0)
    landmarks[20] = (base_x + 0.06, 0.59, 0.0)
    return HandLandmarks(
        landmarks=tuple(landmarks), handedness="Right", score=0.95
    )


# ---------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------

def test_no_hand_returns_none():
    ctrl = PresentationController()
    assert ctrl.update(None) is None


def test_open_hand_middle_no_emit():
    ctrl = PresentationController(cooldown_s=0.0)
    assert ctrl.update(_open_hand(offset_x=0.0)) is None
    assert ctrl.debug.open_hand is True
    assert ctrl.debug.zone is Zone.MIDDLE


def test_cross_middle_to_right_emits_next():
    ctrl = PresentationController(cooldown_s=0.0)
    # frame 1: middle (rearma)
    ctrl.update(_open_hand(offset_x=0.0))
    # frame 2: cruza pra direita
    result = ctrl.update(_open_hand(offset_x=0.20))
    assert result is Gesture.NEXT_SLIDE


def test_cross_middle_to_left_emits_prev():
    ctrl = PresentationController(cooldown_s=0.0)
    ctrl.update(_open_hand(offset_x=0.0))
    result = ctrl.update(_open_hand(offset_x=-0.20))
    assert result is Gesture.PREV_SLIDE


def test_stay_in_right_no_re_emit():
    """Uma vez na zona direita, mover MAIS pra direita nao re-dispara."""
    ctrl = PresentationController(cooldown_s=0.0)
    ctrl.update(_open_hand(offset_x=0.0))  # middle
    first = ctrl.update(_open_hand(offset_x=0.20))  # right (emit)
    assert first is Gesture.NEXT_SLIDE
    # Mao continua na direita, mais um pouco mais a direita ainda
    second = ctrl.update(_open_hand(offset_x=0.30))
    assert second is None
    third = ctrl.update(_open_hand(offset_x=0.25))
    assert third is None


def test_return_to_middle_rearms():
    """Voltar ao meio rearma — proxima cruzada dispara de novo."""
    ctrl = PresentationController(cooldown_s=0.0)
    ctrl.update(_open_hand(offset_x=0.0))
    assert ctrl.update(_open_hand(offset_x=0.20)) is Gesture.NEXT_SLIDE
    # Volta pro meio (zona neutra)
    assert ctrl.update(_open_hand(offset_x=0.0)) is None
    # Cruza de novo -> deve disparar
    assert ctrl.update(_open_hand(offset_x=0.20)) is Gesture.NEXT_SLIDE


def test_fist_does_not_trigger():
    ctrl = PresentationController(cooldown_s=0.0)
    assert ctrl.update(_fist(offset_x=0.0)) is None
    assert ctrl.debug.open_hand is False
    # Mao fechada na direita tambem nao dispara
    assert ctrl.update(_fist(offset_x=0.20)) is None


def test_cooldown_blocks_back_to_back():
    """Cooldown previne disparo duplo mesmo cruzando o meio rapido."""
    ctrl = PresentationController(cooldown_s=10.0)
    ctrl.update(_open_hand(offset_x=0.0))
    first = ctrl.update(_open_hand(offset_x=0.20))
    assert first is Gesture.NEXT_SLIDE
    # Tenta cruzar de novo dentro do cooldown
    ctrl.update(_open_hand(offset_x=0.0))
    second = ctrl.update(_open_hand(offset_x=0.20))
    assert second is None


def test_losing_hand_resets_zone():
    """Perder a mao reseta pra MIDDLE; ao reaparecer num lado, dispara
    porque vem da zona neutra logica."""
    ctrl = PresentationController(cooldown_s=0.0)
    ctrl.update(_open_hand(offset_x=0.0))
    ctrl.update(_open_hand(offset_x=0.20))
    ctrl.update(None)
    result = ctrl.update(_open_hand(offset_x=-0.20))
    assert result is Gesture.PREV_SLIDE


def test_closing_hand_resets_zone():
    """Fechar a mao = reset pro neutro; reabrir num lado redispara."""
    ctrl = PresentationController(cooldown_s=0.0)
    ctrl.update(_open_hand(offset_x=0.0))
    ctrl.update(_open_hand(offset_x=0.20))
    ctrl.update(_fist(offset_x=0.20))
    result = ctrl.update(_open_hand(offset_x=0.20))
    assert result is Gesture.NEXT_SLIDE
