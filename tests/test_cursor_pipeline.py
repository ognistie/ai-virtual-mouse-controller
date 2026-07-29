"""Testes de integracao do pipeline de cursor.

Cobrem o que os testes unitarios de ``core/cursor_motion.py`` nao podem
cobrir sozinhos:

- a ancora STATEFUL e' computada exatamente UMA vez por mao/frame;
- a semantica dos gestos (CLICK, RIGHT_CLICK, DOUBLE_CLICK, DRAG,
  PAUSE) nao regrediu com a troca do pipeline de movimento;
- perfis e sliders propagam ate o MotionConfig;
- jitter medido em PIXEL, com o OneEuro real e o CursorController real.

PyAutoGUI e' mockado; o relogio e' injetado. Nenhum ``time.sleep``.
"""

from __future__ import annotations

import math
import random
import sys
from unittest.mock import MagicMock

import pytest

pytest.importorskip("mediapipe")

from core.gesture_detector import Gesture, GestureDetector, HandShape  # noqa: E402
from core.hand_tracker import HandLandmarks  # noqa: E402
from core.runtime_settings import PROFILES, RuntimeSettings  # noqa: E402


SCREEN_W, SCREEN_H = 1920, 1080


# ---------------------------------------------------------------------
# Relogio injetavel
# ---------------------------------------------------------------------

class FakeClock:
    """Relogio monotonico controlado pelo teste."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:  # usado por double_click
        self.now += seconds

    def tick(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@pytest.fixture
def clock(monkeypatch):
    import core.gesture_detector as gd
    fake = FakeClock()
    monkeypatch.setattr(gd, "time", fake)
    return fake


# ---------------------------------------------------------------------
# Builder de mao
# ---------------------------------------------------------------------

# Proporcoes da palma coerentes com core.cursor_motion._SCALE_SEGMENTS.
_MCP_OFFSETS = {5: -0.24, 9: 0.0, 13: 0.22, 17: 0.48}
_FINGERS = {
    5: (5, 6, 7, 8),
    9: (9, 10, 11, 12),
    13: (13, 14, 15, 16),
    17: (17, 18, 19, 20),
}


def make_hand(
    *,
    shape: str = "open",
    cx: float = 0.5,
    cy: float = 0.5,
    scale: float = 0.22,
) -> HandLandmarks:
    """Constroi 21 landmarks para uma das poses reconhecidas.

    shape: "open", "pinch", "pinch_middle", "peace", "fist".
    scale: comprimento da palma (pulso -> MCP do medio). Cresce quando a
    mao se aproxima da webcam.
    """
    s = scale
    pts = [(cx, cy, 0.0)] * 21
    top = cy - 0.5 * s
    pts[0] = (cx, cy + 0.5 * s, 0.0)
    for mcp, off in _MCP_OFFSETS.items():
        pts[mcp] = (cx + off * s, top, 0.0)

    up = {
        "open": (True, True, True, True),
        "pinch": (True, True, True, True),
        "pinch_middle": (True, False, True, True),
        "peace": (True, True, False, False),
        "fist": (False, False, False, False),
    }[shape]

    for (mcp, chain), extended in zip(_FINGERS.items(), up):
        mx, my, _ = pts[mcp]
        _m, pip, dip, tip = chain
        if extended:
            pts[pip] = (mx, my - 0.23 * s, 0.0)
            pts[dip] = (mx, my - 0.41 * s, 0.0)
            pts[tip] = (mx, my - 0.59 * s, 0.0)
        else:
            pts[pip] = (mx, my - 0.23 * s, 0.0)
            pts[dip] = (mx + 0.09 * s, my - 0.09 * s, 0.0)
            pts[tip] = (mx, my + 0.05 * s, 0.0)

    # Polegar: cadeia (1, 2, 3, 4). A ponta (4) e' o que decide PINCH.
    pts[1] = (cx - 0.42 * s, cy + 0.30 * s, 0.0)
    pts[2] = (cx - 0.55 * s, cy + 0.09 * s, 0.0)
    pts[3] = (cx - 0.62 * s, cy - 0.09 * s, 0.0)
    if shape == "pinch":
        ix, iy, _ = pts[8]
        pts[4] = (ix - 0.10 * s, iy + 0.06 * s, 0.0)
    elif shape == "pinch_middle":
        mx2, my2, _ = pts[12]
        pts[4] = (mx2 - 0.10 * s, my2 + 0.06 * s, 0.0)
    else:
        pts[4] = (cx - 0.70 * s, cy - 0.27 * s, 0.0)

    return HandLandmarks(landmarks=tuple(pts), handedness="Right", score=0.95)


def _detector(**kwargs) -> GestureDetector:
    defaults = dict(
        anchor_landmark=-2,           # ancora robusta (default do projeto)
        pinch_threshold=0.075,
        drag_hold_seconds=0.30,
        click_cooldown=0.15,
        debounce_frames=2,
        exit_frames=3,
        position_hold_frames=2,
        bottom_assist_edge=0.86,
    )
    defaults.update(kwargs)
    return GestureDetector(**defaults)


def _feed(detector, clock, hand, frames: int, dt: float = 1 / 60):
    """Roda N frames com a mesma pose e devolve todos os eventos."""
    events = []
    for _ in range(frames):
        clock.tick(dt)
        events.extend(detector.update(hand))
    return events


def _gestures(events):
    return [e.gesture for e in events]


# =====================================================================
# 13 — Ancora stateful computada UMA vez por mao/frame
# =====================================================================

class TestSingleAnchorCall:
    def test_anchor_is_computed_exactly_once_per_frame(self, clock):
        detector = _detector()
        real = detector._robust_anchor.compute
        calls = {"n": 0}

        def counting(landmarks):
            calls["n"] += 1
            return real(landmarks)

        detector._robust_anchor.compute = counting  # type: ignore[method-assign]

        hand = make_hand(shape="open")
        for i in range(30):
            before = calls["n"]
            clock.tick(1 / 60)
            detector.update(hand)
            assert calls["n"] - before == 1, f"frame {i}"

    def test_anchor_is_computed_once_during_click_frames(self, clock):
        """O frame do CLICK usava a ancora em tres lugares (movimento,
        evento de press e _process_action) — e cada chamada empurrava o
        historico da ancora stateful."""
        detector = _detector()
        real = detector._robust_anchor.compute
        calls = {"n": 0}

        def counting(landmarks):
            calls["n"] += 1
            return real(landmarks)

        detector._robust_anchor.compute = counting  # type: ignore[method-assign]

        _feed(detector, clock, make_hand(shape="open"), 5)
        n_before = calls["n"]
        _feed(detector, clock, make_hand(shape="pinch"), 4)
        assert calls["n"] - n_before == 4

    def test_no_anchor_call_when_hand_is_absent(self, clock):
        detector = _detector()
        calls = {"n": 0}
        real = detector._robust_anchor.compute

        def counting(landmarks):
            calls["n"] += 1
            return real(landmarks)

        detector._robust_anchor.compute = counting  # type: ignore[method-assign]
        for _ in range(5):
            clock.tick(1 / 60)
            detector.update(None)
        assert calls["n"] == 0


# =====================================================================
# 12 — Semantica dos gestos preservada
# =====================================================================

class TestGestureSemantics:
    def test_open_hand_emits_move(self, clock):
        detector = _detector()
        events = _feed(detector, clock, make_hand(shape="open"), 6)
        assert Gesture.MOVE in _gestures(events)
        assert detector.current_shape == HandShape.OPEN_HAND

    def test_pinch_emits_click_on_press(self, clock):
        detector = _detector()
        _feed(detector, clock, make_hand(shape="open"), 5)
        events = _feed(detector, clock, make_hand(shape="pinch"), 3)
        assert Gesture.CLICK in _gestures(events)

    def test_click_fires_once_per_pinch(self, clock):
        detector = _detector()
        _feed(detector, clock, make_hand(shape="open"), 5)
        events = _feed(detector, clock, make_hand(shape="pinch"), 8)
        events += _feed(detector, clock, make_hand(shape="open"), 8)
        assert _gestures(events).count(Gesture.CLICK) == 1

    def test_pinch_middle_emits_right_click(self, clock):
        detector = _detector()
        _feed(detector, clock, make_hand(shape="open"), 5)
        events = _feed(detector, clock, make_hand(shape="pinch_middle"), 3)
        assert Gesture.RIGHT_CLICK in _gestures(events)

    def test_peace_release_emits_double_click(self, clock):
        detector = _detector()
        _feed(detector, clock, make_hand(shape="open"), 5)
        _feed(detector, clock, make_hand(shape="peace"), 8)
        events = _feed(detector, clock, make_hand(shape="open"), 8)
        assert Gesture.DOUBLE_CLICK in _gestures(events)

    def test_sustained_pinch_starts_and_ends_drag(self, clock):
        detector = _detector(drag_hold_seconds=0.20)
        _feed(detector, clock, make_hand(shape="open"), 5)
        events = _feed(detector, clock, make_hand(shape="pinch"), 30)
        assert Gesture.DRAG_START in _gestures(events)
        assert detector.is_dragging

        events = _feed(detector, clock, make_hand(shape="open"), 8)
        assert Gesture.DRAG_END in _gestures(events)
        assert not detector.is_dragging

    def test_fist_freezes_the_cursor(self, clock):
        detector = _detector()
        _feed(detector, clock, make_hand(shape="open"), 6)
        # Deixa a histerese confirmar o punho antes de medir (os frames
        # de debounce ainda contam como OPEN_HAND, por design).
        _feed(detector, clock, make_hand(shape="fist"), 6)
        assert detector.current_shape == HandShape.FIST
        frozen = detector._last_smoothed_pos

        # Mao anda MUITO em punho: o cursor nao pode segui-la.
        for i in range(1, 21):
            clock.tick(1 / 60)
            detector.update(make_hand(shape="fist", cx=0.5 + 0.01 * i))
        assert detector._last_smoothed_pos == frozen

    def test_hand_removal_emits_pause_and_keeps_position(self, clock):
        detector = _detector()
        _feed(detector, clock, make_hand(shape="open"), 8)
        before = detector._last_smoothed_pos
        for _ in range(40):
            clock.tick(1 / 60)
            detector.update(None)
        assert detector.current_gesture == Gesture.PAUSE
        assert detector._last_smoothed_pos == before

    def test_drag_is_released_when_hand_disappears(self, clock):
        detector = _detector(drag_hold_seconds=0.20)
        _feed(detector, clock, make_hand(shape="open"), 5)
        _feed(detector, clock, make_hand(shape="pinch"), 30)
        assert detector.is_dragging
        events = []
        for _ in range(40):
            clock.tick(1 / 60)
            events.extend(detector.update(None))
        assert Gesture.DRAG_END in _gestures(events)

    def test_cursor_does_not_follow_the_hand_travelled_while_frozen(self, clock):
        """OPEN_HAND -> FIST (mao viaja 0.20) -> OPEN_HAND.

        E' o equivalente a levantar o mouse e reposicionar: ao voltar, o
        cursor NAO pode reproduzir o percurso feito congelado. O modelo
        anterior (posicao absoluta) teleportava o cursor pro novo ponto
        da mao.
        """
        detector = _detector()
        _feed(detector, clock, make_hand(shape="open"), 8)
        _feed(detector, clock, make_hand(shape="fist"), 6)
        frozen = detector._last_smoothed_pos

        travel = 0.20
        for i in range(1, 21):
            clock.tick(1 / 60)
            detector.update(make_hand(shape="fist", cx=0.5 + travel * i / 20))
        _feed(detector, clock, make_hand(shape="open", cx=0.5 + travel), 6)

        after = detector._last_smoothed_pos
        assert frozen is not None and after is not None
        moved = math.hypot(after[0] - frozen[0], after[1] - frozen[1])
        # O residual vem da mudanca de POSE (dedos abrindo movem a ancora
        # robusta), nao do percurso da mao — tem que ser uma fracao dele.
        assert moved < travel / 4.0


# =====================================================================
# 14 — Perfis e sliders propagam ate o MotionConfig
# =====================================================================

class TestSettingsPropagation:
    def test_aim_slider_reaches_motion_config(self, clock):
        detector = _detector()
        settings = RuntimeSettings("smooth")
        updates = settings.set_slider("aim_assist", 1.0)
        value = updates["aim_assist_slowdown_factor"]
        detector.aim_assist_slowdown_factor = value
        assert detector.motion.config.aim_slowdown_factor == pytest.approx(value)

    def test_sticky_slider_reaches_motion_config(self, clock):
        detector = _detector()
        settings = RuntimeSettings("smooth")
        updates = settings.set_slider("sticky", 0.25)
        value = updates["sticky_friction_factor"]
        detector.sticky_friction_factor = value
        assert detector.motion.config.sticky_friction_factor == pytest.approx(value)

    def test_sensitivity_slider_is_the_base_gain(self, clock):
        detector = _detector()
        settings = RuntimeSettings("smooth")
        updates = settings.set_slider("sensitivity", 1.0)
        detector.dpi_fixed = updates["dpi_fixed_multiplier"]

        _feed(detector, clock, make_hand(shape="open"), 3)
        clock.tick(1 / 60)
        detector.update(make_hand(shape="open", cx=0.53))
        # base 1.5 x distancia 1.0 x precisao 1.0
        assert detector.motion.total_gain == pytest.approx(1.5, rel=0.01)

    @pytest.mark.parametrize("profile", sorted(PROFILES))
    def test_every_profile_propagates(self, clock, profile):
        detector = _detector()
        settings = RuntimeSettings(profile)
        detector.aim_assist_slowdown_factor = settings.get(
            "aim_assist_slowdown_factor"
        )
        detector.sticky_friction_factor = settings.get("sticky_friction_factor")
        cfg = detector.motion.config
        assert cfg.aim_slowdown_factor == pytest.approx(
            settings.get("aim_assist_slowdown_factor")
        )
        assert cfg.sticky_friction_factor == pytest.approx(
            settings.get("sticky_friction_factor")
        )

    def test_sticky_toggle_propagates(self, clock):
        detector = _detector()
        detector.sticky_targeting_enabled = False
        assert detector.motion.config.sticky_enabled is False
        detector.sticky_targeting_enabled = True
        assert detector.motion.config.sticky_enabled is True

    def test_changing_a_setting_does_not_move_the_cursor(self, clock):
        detector = _detector()
        _feed(detector, clock, make_hand(shape="open"), 8)
        before = detector._last_smoothed_pos

        detector.aim_assist_slowdown_factor = 0.2
        detector.sticky_friction_factor = 0.5
        detector.dpi_fixed = 1.5

        # Mesma pose exata: a saida nao pode se mexer.
        clock.tick(1 / 60)
        detector.update(make_hand(shape="open"))
        assert detector._last_smoothed_pos == before


# =====================================================================
# 6 — Jitter em PIXEL, com OneEuro e CursorController reais
# =====================================================================

class TestJitterInPixels:
    def _pipeline(self, monkeypatch):
        mock = MagicMock()
        mock.size = MagicMock(return_value=(SCREEN_W, SCREEN_H))
        monkeypatch.setitem(sys.modules, "pyautogui", mock)
        from importlib import reload
        import core.cursor_controller as cc
        import core.smoothing as sm
        reload(cc)

        fake = FakeClock()
        monkeypatch.setattr(sm, "time", fake)
        smoother = sm.OneEuroSmoother2D(freq=60.0, min_cutoff=1.2, beta=0.020)
        cursor = cc.CursorController(
            screen_margin_x=0.08,
            screen_margin_top=0.04,
            screen_margin_bottom=0.14,
            dead_zone_pixels=1,
        )
        return cursor, smoother, mock, fake

    def test_p95_step_stays_under_three_pixels(self, monkeypatch, clock):
        cursor, smoother, mock, sm_clock = self._pipeline(monkeypatch)
        detector = _detector()
        rng = random.Random(20260729)

        positions = []
        for i in range(420):
            clock.tick(1 / 60)
            sm_clock.tick(1 / 60)
            hand = make_hand(
                shape="open",
                cx=0.5 + rng.uniform(-0.0015, 0.0015),
                cy=0.5 + rng.uniform(-0.0015, 0.0015),
            )
            for ev in detector.update(hand):
                if ev.gesture == Gesture.MOVE and ev.position is not None:
                    x, y = smoother(*ev.position)
                    cursor.move(x, y)
            if i >= 120:  # warm-up
                positions.append(cursor.last_position)

        steps = [
            math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(positions, positions[1:])
            if a[0] is not None and b[0] is not None
        ]
        steps.sort()
        p95 = steps[int(0.95 * (len(steps) - 1))]
        assert p95 <= 3.0

    def test_static_hand_produces_no_cursor_travel(self, monkeypatch, clock):
        cursor, smoother, mock, sm_clock = self._pipeline(monkeypatch)
        detector = _detector()
        hand = make_hand(shape="open")

        for _ in range(120):
            clock.tick(1 / 60)
            sm_clock.tick(1 / 60)
            for ev in detector.update(hand):
                if ev.gesture == Gesture.MOVE and ev.position is not None:
                    cursor.move(*smoother(*ev.position))
        first = cursor.last_position

        for _ in range(240):
            clock.tick(1 / 60)
            sm_clock.tick(1 / 60)
            for ev in detector.update(hand):
                if ev.gesture == Gesture.MOVE and ev.position is not None:
                    cursor.move(*smoother(*ev.position))
        assert cursor.last_position == first
