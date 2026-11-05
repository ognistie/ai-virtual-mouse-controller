"""Partes do runtime que so aparecem com a visao em thread: ciclo do run(),
mao 3D em falha curta, desenho por pose nova e gestos vistos em uso real."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

import config
from core import hologram_gl_backend as gl_mod
from core.gesture_detector import Gesture, GestureDetector, HandShape, LM_ROBUST_HAND, classify_shape
from core.hand_gate import HandGate
from core.hand_pose import HandPoseSlots
from core.two_hands import DISPLAY_HOLD_FRAMES, HandChannel, TwoHandsController
from services.virtual_mouse_service import VirtualMouseService
from tests.synthetic_hand import FakeClock, make_hand


def _curl_others(hand):
    """Dedos medio, anelar e minimo fechados (como ao fechar a mao)."""
    lm = list(hand.landmarks)
    for mcp, pip, dip, tip in ((9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)):
        x, y, z = lm[mcp]
        px, py, _ = lm[pip]
        lm[dip] = ((x + px) / 2, (y + py) / 2, z)
        lm[tip] = (x, y, z)
    return type(hand)(landmarks=tuple(lm), handedness=hand.handedness, score=hand.score)


def _service():
    svc = VirtualMouseService.__new__(VirtualMouseService)
    svc._should_stop = False
    svc._vision_failed = False
    svc._latest_cond = threading.Condition()
    svc._latest = None
    svc._vision_lock = threading.RLock()
    svc._perf = MagicMock()
    svc._fps = MagicMock()
    svc.camera = MagicMock()
    svc.tracker = MagicMock()
    svc.cursor = MagicMock(is_dragging=False)
    svc.gesture_detector = MagicMock()
    svc.gesture_detector.update.return_value = []
    svc.gesture_detector.is_gesture_held.return_value = False
    svc.smoother = MagicMock()
    svc._presentation = MagicMock()
    svc._presentation_mode = False
    svc._two_hands_mode = False
    svc._two_hands = None
    svc._hand_gate = HandGate(confirm_frames=1)
    svc._last_hand = None
    svc._hand_lost_frames = 0
    svc._frame_seq = 0
    svc._camera_lost = False
    svc._last_frame_t = time.perf_counter()
    svc._pre_resize_enabled = False
    svc.enable_preview = False
    svc.hologram = None
    svc.settings_panel = None
    svc._setup_signal_handlers = MagicMock()
    return svc


def _feed_frames(svc, hands_per_frame):
    """Camera e rastreador falsos: cada read_new vira um frame da lista."""
    frames = iter(hands_per_frame)
    state = {}

    def read_new(last, timeout=0.0, mirror=True):
        try:
            state["hands"] = next(frames)
        except StopIteration:
            time.sleep(0.001)
            return None, last
        return np.zeros((4, 6, 3), np.uint8), last + 1

    svc.camera.read_new.side_effect = read_new
    svc.tracker.process_with_raw.side_effect = lambda _rgb: (state["hands"], state["hands"])


@pytest.fixture
def threaded(monkeypatch):
    monkeypatch.setattr(config, "VISION_THREAD_ENABLED", True)
    monkeypatch.setattr(config, "POWER_THROTTLING_OFF", False)
    monkeypatch.setattr(config, "OPENCV_SINGLE_THREAD", False)


# Ciclo do run() ---------------------------------------------------------

def test_run_processes_in_thread_and_stops_vision_before_cleanup(threaded):
    svc = _service()
    _feed_frames(svc, [[make_hand(0.5, 0.5)] for _ in range(5)])
    ui_threads = []
    svc._ui_step = MagicMock(side_effect=lambda _r: ui_threads.append(threading.current_thread()))
    svc._idle_tick = MagicMock()
    seen = {}

    def cleanup(vision_stopped=True):
        seen["vision_stopped"] = vision_stopped
        seen["alive"] = [t.name for t in threading.enumerate() if t.name == "VisionThread"]

    svc._cleanup = cleanup
    timer = threading.Timer(0.3, svc.stop)
    timer.start()
    assert svc.run() == 0
    timer.cancel()
    assert svc.gesture_detector.update.call_count == 5
    assert ui_threads and all(t is threading.main_thread() for t in ui_threads)
    assert seen == {"vision_stopped": True, "alive": []}


def test_vision_crash_ends_run_with_error_code(threaded):
    svc = _service()
    svc.camera.read_new.side_effect = RuntimeError("camera quebrou")
    svc._ui_tick = MagicMock(side_effect=lambda: time.sleep(0.001))
    svc._cleanup = MagicMock()
    assert svc.run() == 1
    svc._cleanup.assert_called_once_with(True)


def test_stuck_vision_thread_keeps_tracker_open(threaded):
    svc = _service()
    release = threading.Event()
    svc._VISION_JOIN_S = 0.01
    svc._vision_loop = lambda: release.wait(2.0)
    svc._ui_tick = MagicMock(side_effect=lambda: svc.stop())
    svc._cleanup = MagicMock()
    try:
        svc.run()
    finally:
        release.set()
    svc._cleanup.assert_called_once_with(False)


def test_cleanup_with_vision_running_only_releases_mouse():
    svc = _service()
    svc._cleanup(vision_stopped=False)
    svc.cursor.force_release.assert_called_once()
    svc.tracker.close.assert_not_called()
    svc.camera.close.assert_not_called()


# Mao 3D em falha curta ---------------------------------------------------

def test_hologram_keeps_hand_through_short_gap_then_lets_go():
    svc = _service()
    hand = make_hand(0.5, 0.5)
    _feed_frames(svc, [[hand]] + [[] for _ in range(DISPLAY_HOLD_FRAMES + 2)])
    results = [svc._vision_step(0.0) for _ in range(DISPLAY_HOLD_FRAMES + 3)]
    assert all(r.hand is hand for r in results[:DISPLAY_HOLD_FRAMES + 1])
    assert results[-1].hand is None
    # O detector de gestos ve a verdade: na falha ele recebe None.
    assert svc.gesture_detector.update.call_args.args[0] is None


def test_camera_loss_forgets_last_hand_and_filters():
    svc = _service()
    svc._last_hand = make_hand(0.5, 0.5)
    svc._hand_gate = MagicMock()
    svc._on_camera_lost()
    assert svc._last_hand is None
    svc._hand_gate.reset.assert_called_once()


def test_two_hands_pose_holds_through_short_gap():
    cursor = MagicMock(last_position=(100, 200))
    channel = HandChannel("right", MagicMock(), MagicMock(), cursor)
    hand = make_hand(0.5, 0.5)
    channel.last_hand = hand
    channel.lost_frames = DISPLAY_HOLD_FRAMES
    assert TwoHandsController._pose(channel)[0] is hand.landmarks
    channel.lost_frames = DISPLAY_HOLD_FRAMES + 1
    assert TwoHandsController._pose(channel) is None


# Desenho da mao 3D ------------------------------------------------------

def _backend():
    gl = gl_mod.HologramGLBackend.__new__(gl_mod.HologramGLBackend)
    gl._slots = HandPoseSlots(180, 16 / 9)
    gl._window = MagicMock()
    gl._enabled = True
    gl._dirty = False
    gl._min_paint_interval = 0.0
    gl._last_paint_t = 0.0
    return gl


def test_paints_once_per_new_pose_and_never_without_one():
    gl = _backend()
    gl._paint_if_needed()
    gl._window.paint_now.assert_not_called()
    gl.update_poses([(make_hand(0.5, 0.5).landmarks, 500, 400)])
    gl._paint_if_needed()
    gl._paint_if_needed()
    gl._window.paint_now.assert_called_once()


def test_hand_leaving_paints_to_erase_it():
    gl = _backend()
    gl.update_poses([None])
    gl._paint_if_needed()
    gl._window.paint_now.assert_called_once()


def test_paint_limit_defers_to_the_next_pump():
    gl = _backend()
    gl._min_paint_interval = 10.0
    gl._last_paint_t = time.perf_counter()
    gl.update_poses([None])
    gl._paint_if_needed()
    gl._window.paint_now.assert_not_called()
    assert gl._dirty
    gl._min_paint_interval = 0.0
    gl._paint_if_needed()
    gl._window.paint_now.assert_called_once()


def test_disabled_hologram_does_not_paint():
    gl = _backend()
    gl._enabled = False
    gl.update_poses([None])
    gl._paint_if_needed()
    gl._window.paint_now.assert_not_called()


# Gestos vistos em uso real ----------------------------------------------

def test_closed_fingers_are_not_a_pinch():
    hand = make_hand(0.5, 0.5, pinch=1.0)
    closed = _curl_others(hand)
    assert classify_shape(hand, 0.075, pinch_open_fingers_min=0.82) == HandShape.PINCH
    assert classify_shape(closed, 0.075, pinch_open_fingers_min=0.82) != HandShape.PINCH
    # 0 desliga: comportamento antigo.
    assert classify_shape(closed, 0.075) == HandShape.PINCH


@pytest.fixture
def clock(monkeypatch):
    from core import gesture_detector as gd

    c = FakeClock()
    monkeypatch.setattr(gd, "time", c)
    return c


def _detector(**kw):
    return GestureDetector(
        anchor_landmark=LM_ROBUST_HAND, dpi_fixed=1.0, drag_hold_seconds=1.5,
        click_cooldown=0.15, position_hold_frames=2, followthrough_frames=6,
        pinch_release_ratio=1.25, **kw,
    )


def _clicks(det, clock, hand, frames=1, dt=1 / 30):
    total = 0
    for _ in range(frames):
        clock.advance(dt)
        total += sum(e.gesture == Gesture.CLICK for e in det.update(hand))
    return total


def test_closing_the_hand_does_not_click(clock):
    det = _detector(pinch_open_fingers_min=0.82)
    _clicks(det, clock, make_hand(0.5, 0.5), frames=5)
    assert _clicks(det, clock, _curl_others(make_hand(0.5, 0.5, pinch=1.0)), frames=8) == 0


def test_held_pinch_survives_relaxing_other_fingers(clock):
    det = _detector(pinch_open_fingers_min=0.82)
    _clicks(det, clock, make_hand(0.5, 0.5), frames=5)
    assert _clicks(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=3) == 1
    assert _clicks(det, clock, _curl_others(make_hand(0.5, 0.5, pinch=1.0)), frames=5) == 0
    assert det.is_gesture_held()


def test_oscillating_pinch_clicks_once(clock):
    """Dedos abrem por instantes (mao na frente do rosto): um clique so."""
    det = _detector(pinch_rearm_seconds=0.12)
    _clicks(det, clock, make_hand(0.5, 0.5), frames=5)
    total = _clicks(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=4)
    clock.advance(0.2)  # ja passou do cooldown
    for _ in range(3):
        total += _clicks(det, clock, make_hand(0.5, 0.5), frames=2)
        total += _clicks(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=3)
        clock.advance(0.2)
    assert total == 1


def test_deliberate_repeat_still_clicks_with_rearm(clock):
    det = _detector(pinch_rearm_seconds=0.12)
    _clicks(det, clock, make_hand(0.5, 0.5), frames=5)
    total = _clicks(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=4)
    total += _clicks(det, clock, make_hand(0.5, 0.5), frames=6)
    total += _clicks(det, clock, make_hand(0.5, 0.5, pinch=1.0), frames=4)
    assert total == 2
