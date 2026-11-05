"""O que nao e' mao nao vira cursor: fantasma e deteccao passageira."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np

from core.hand_gate import HandGate
from core.perf_telemetry import TickProfiler
from services.virtual_mouse_service import VirtualMouseService, _VisionResult
from tests.synthetic_hand import make_hand


def _at(x, y=0.5, size=0.15):
    return make_hand(x, y, size=size)


def _gate(confirm=1):
    return HandGate(confirm_frames=confirm)


# Fantasma ----------------------------------------------------------------

def test_hand_nested_in_another_is_dropped():
    real, ghost = _at(0.5, size=0.22), _at(0.5, 0.45, size=0.08)
    assert _gate().filter([real, ghost]) == [real]
    assert _gate().filter([ghost, real]) == [real]


def test_two_real_hands_stay():
    a, b = _at(0.25), _at(0.75)
    assert _gate().filter([a, b]) == [a, b]


# Confirmacao -------------------------------------------------------------

def test_new_hand_acts_only_after_confirmation():
    gate = _gate(confirm=3)
    hand = _at(0.5)
    assert gate.filter([hand]) == []
    assert gate.filter([hand]) == []
    assert gate.filter([hand]) == [hand]


def test_brief_detection_never_acts():
    gate = _gate(confirm=3)
    gate.filter([_at(0.5)])
    gate.filter([_at(0.5)])
    for _ in range(10):
        assert gate.filter([]) == []
    assert gate.filter([_at(0.5)]) == []


def test_blink_does_not_require_new_confirmation():
    gate = _gate(confirm=3)
    for _ in range(3):
        gate.filter([_at(0.5)])
    gate.filter([])
    gate.filter([])
    assert gate.filter([_at(0.5)]) != []


def test_moving_hand_keeps_confirmation():
    gate = _gate(confirm=3)
    for i in range(3):
        out = gate.filter([_at(0.3 + i * 0.03)])
    assert out
    assert gate.filter([_at(0.4)]) != []


# Thread de visao ---------------------------------------------------------

def _vision_service():
    svc = VirtualMouseService.__new__(VirtualMouseService)
    svc._should_stop = False
    svc._vision_failed = False
    svc._latest_cond = threading.Condition()
    svc._latest = None
    svc._idle_tick = MagicMock()
    svc._ui_step = MagicMock()
    return svc


def test_ui_draws_only_the_newest_result():
    svc = _vision_service()
    old = _VisionResult(np.zeros((1, 1, 3)), None, [])
    new = _VisionResult(np.zeros((1, 1, 3)), None, [])
    svc._latest = old
    svc._latest = new
    svc._ui_tick()
    svc._ui_step.assert_called_once_with(new)
    assert svc._latest is None


def test_ui_without_result_runs_idle_tick():
    svc = _vision_service()
    svc._ui_tick()
    svc._idle_tick.assert_called_once()
    svc._ui_step.assert_not_called()


def test_vision_error_stops_the_app():
    svc = _vision_service()
    svc._vision_step = MagicMock(side_effect=RuntimeError("boom"))
    svc._vision_loop()
    assert svc._vision_failed and svc._should_stop


def test_vision_loop_publishes_results_until_stop():
    svc = _vision_service()
    result = _VisionResult(np.zeros((1, 1, 3)), None, [])

    def step(_timeout):
        svc._should_stop = True
        return result

    svc._vision_step = step
    svc._vision_loop()
    assert svc._latest is result


def test_profiler_survives_concurrent_threads():
    prof = TickProfiler(window_size=50, report_every=10 ** 6)
    stop = threading.Event()
    errors = []

    def writer():
        while not stop.is_set():
            with prof.stage("x"):
                pass

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        end = time.perf_counter() + 0.2
        while time.perf_counter() < end:
            try:
                prof.snapshot()
            except RuntimeError as e:  # deque alterada durante a leitura
                errors.append(e)
    finally:
        stop.set()
        thread.join()
    assert not errors


def test_disable_power_throttling_returns_bool_on_any_platform():
    from core.utils import disable_power_throttling

    assert isinstance(disable_power_throttling(), bool)
