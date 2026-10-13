"""Comportamento do GestureDetector com maos sinteticas e relogio falso.

Cobre a maquina de estados real (press-to-click, histerese da pinca,
drag, follow-through, mao perdida) — os testes legados de
test_gesture_detector.py estao pulados por API antiga.
"""

from __future__ import annotations

from unittest import mock

import pytest

from core import gesture_detector as gd_mod
from core.gesture_detector import Gesture, GestureDetector, HandShape, LM_ROBUST_HAND
from tests.synthetic_hand import FakeClock, make_hand

FRAME = 1.0 / 30.0


@pytest.fixture
def clock():
    c = FakeClock()
    with mock.patch.object(gd_mod, "time", c):
        yield c


def _detector(**kw) -> GestureDetector:
    params = {
        "anchor_landmark": LM_ROBUST_HAND,
        "dpi_fixed": 1.0,
        "drag_hold_seconds": 1.5,
        "click_cooldown": 0.15,
        "position_hold_frames": 2,
        "followthrough_frames": 6,
        "pinch_release_ratio": 1.25,
    }
    params.update(kw)
    return GestureDetector(**params)


def _feed(det, clock, hand, frames=1):
    events = []
    for _ in range(frames):
        clock.advance(FRAME)
        events.extend(e.gesture for e in det.update(hand))
    return events


def _count(events, gesture):
    return sum(1 for g in events if g == gesture)


def test_open_hand_moves_cursor(clock):
    det = _detector()
    events = _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    assert det.current_shape == HandShape.OPEN_HAND
    assert Gesture.MOVE in events


def test_robust_anchor_computed_once_per_frame(clock):
    """RobustHandAnchor e' stateful: mais de um compute() por frame
    duplicava o historico e anulava a histerese de confianca."""
    det = _detector()
    real = det._robust_anchor.compute
    calls = []

    def counting(lm):
        calls.append(1)
        return real(lm)

    det._robust_anchor.compute = counting
    for _ in range(6):
        calls.clear()
        _feed(det, clock, make_hand(0.5, 0.5))
        assert len(calls) == 1


def test_pinch_clicks_once_on_press_not_on_release(clock):
    det = _detector()
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    press = _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=4)
    release = _feed(det, clock, make_hand(0.5, 0.5), frames=6)
    assert _count(press, Gesture.CLICK) == 1
    assert _count(release, Gesture.CLICK) == 0


@pytest.mark.parametrize("ratio, expected_clicks", [(1.0, 2), (1.25, 1)])
def test_pinch_hysteresis_absorbs_noise_at_threshold(clock, ratio, expected_clicks):
    """Abrir so um pouco (entre o limiar de entrada e o de saida) nao
    pode virar um segundo clique — com limiar unico (ratio 1.0) vira."""
    det = _detector(pinch_release_ratio=ratio)
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    events = _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=3)
    # pinch=0.74: acima do limiar de entrada (0.075), abaixo do de saida.
    events += _feed(det, clock, make_hand(0.5, 0.5, pinch=0.74), frames=1)
    clock.advance(0.2)  # passa do cooldown
    events += _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=3)
    assert _count(events, Gesture.CLICK) == expected_clicks


def test_deliberate_double_pinch_gives_two_clicks(clock):
    det = _detector()
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    events = []
    for _ in range(2):
        for amount in (0.5, 0.8, 1.0, 1.0, 1.0, 1.0, 0.8, 0.5, 0.5, 0.5):
            events += _feed(det, clock, make_hand(0.5, 0.5, pinch=amount))
    assert _count(events, Gesture.CLICK) == 2


def test_hold_pinch_starts_and_ends_drag(clock):
    det = _detector(drag_hold_seconds=0.5)
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    held = _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=25)
    assert _count(held, Gesture.CLICK) == 1
    assert _count(held, Gesture.DRAG_START) == 1
    assert det.is_dragging
    released = _feed(det, clock, make_hand(0.5, 0.5), frames=6)
    assert _count(released, Gesture.DRAG_END) == 1
    assert not det.is_dragging


def test_drag_survives_pinch_noise_near_threshold(clock):
    det = _detector(drag_hold_seconds=0.3)
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=15)
    assert det.is_dragging
    # Relaxa os dedos ate perto do limiar de entrada: drag continua.
    events = _feed(det, clock, make_hand(0.5, 0.5, pinch=0.74), frames=10)
    assert _count(events, Gesture.DRAG_END) == 0
    assert det.is_dragging


def test_hand_lost_holds_then_follows_through_then_releases_drag(clock):
    det = _detector(drag_hold_seconds=0.3)
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=15)
    assert det.is_dragging

    hold = _feed(det, clock, None, frames=2)
    assert hold == []  # hold puro: blink da deteccao nao mexe em nada
    follow = _feed(det, clock, None, frames=6)
    assert set(follow) <= {Gesture.MOVE}
    lost = _feed(det, clock, None, frames=1)
    assert lost == [Gesture.DRAG_END]
    assert not det.is_dragging


def test_reset_mid_drag_lets_next_pinch_click(clock):
    """Camera caiu no meio do drag (service chama reset): ao voltar, a
    proxima pinca precisa clicar normalmente."""
    det = _detector(drag_hold_seconds=0.3)
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=15)
    assert det.is_dragging
    det.reset()
    assert not det.is_dragging
    clock.advance(1.0)
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    events = _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=3)
    assert _count(events, Gesture.CLICK) == 1


def test_click_cooldown_blocks_rapid_repeat_without_late_click(clock):
    """Pinca dentro do cooldown nao clica — e tambem nao gera um clique
    atrasado ao soltar (o fallback antigo era codigo morto)."""
    det = _detector(click_cooldown=1.0)
    _feed(det, clock, make_hand(0.5, 0.5), frames=5)
    events = _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=3)
    events += _feed(det, clock, make_hand(0.5, 0.5), frames=4)
    events += _feed(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=3)
    events += _feed(det, clock, make_hand(0.5, 0.5), frames=6)
    assert _count(events, Gesture.CLICK) == 1


def test_wrist_tilt_noise_does_not_jitter_dpi(clock):
    """Tamanho aparente da mao oscilando (pulso inclinando) nao pode
    fazer o DPI adaptativo — e o cursor — oscilar junto."""
    det = _detector(dpi_size_smoothing_s=0.6, dpi_adaptive_enabled=True)
    _feed(det, clock, make_hand(0.74, 0.4), frames=10)
    dpis = []
    for i in range(30):
        size = 0.22 * (0.85 if i % 2 else 1.0)
        _feed(det, clock, make_hand(0.74, 0.4, size=size))
        dpis.append(det.dpi_multiplier)
    # Depois de assentar no tamanho medio, a oscilacao frame a frame some.
    tail = dpis[-10:]
    assert max(tail) - min(tail) < 0.01


def test_raw_dpi_follows_tilt_when_smoothing_disabled(clock):
    det = _detector(dpi_size_smoothing_s=0.0, dpi_adaptive_enabled=True)
    _feed(det, clock, make_hand(0.74, 0.4), frames=10)
    dpis = []
    for i in range(10):
        size = 0.22 * (0.85 if i % 2 else 1.0)
        _feed(det, clock, make_hand(0.74, 0.4, size=size))
        dpis.append(det.dpi_multiplier)
    assert max(dpis) - min(dpis) > 0.05
