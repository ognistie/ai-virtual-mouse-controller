"""Modo duas maos: canais, cursor unico, service e holograma (sem janela)."""

from __future__ import annotations

import threading
from unittest import mock
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import cursor_controller as cc_mod
from core import gesture_detector as gd_mod
from core import hologram_gl_backend as gl_mod
from core import two_hands as th_mod
from core.cursor_controller import CursorController
from core.gesture_detector import Gesture, GestureDetector, LM_ROBUST_HAND
from core.hand_arbiter import LEFT, RIGHT
from core.hand_gate import HandGate
from core.hand_pose import HandPoseSlots
from core.hand_tracker import HandLandmarks
from core.runtime_settings import RuntimeSettings
from core.smoothing import OneEuroSmoother2D
from core.two_hands import DISPLAY_HOLD_FRAMES, HandChannel, TwoHandsController
from services.virtual_mouse_service import VirtualMouseService
from tests.synthetic_hand import FakeClock, make_hand

FRAME = 1.0 / 30.0


def _labeled(hand: HandLandmarks, label: str) -> HandLandmarks:
    return HandLandmarks(landmarks=hand.landmarks, handedness=label, score=hand.score)


def _left(cx=0.3, cy=0.5, pinch=0.0):
    return _labeled(make_hand(cx, cy, pinch=pinch), "Left")


def _right(cx=0.7, cy=0.5, pinch=0.0):
    return _labeled(make_hand(cx, cy, pinch=pinch), "Right")


def _detector() -> GestureDetector:
    return GestureDetector(
        anchor_landmark=LM_ROBUST_HAND, dpi_fixed=1.0, drag_hold_seconds=1.5,
        click_cooldown=0.15, position_hold_frames=2, followthrough_frames=6,
        pinch_release_ratio=1.25,
    )


def _channel(name: str) -> HandChannel:
    return HandChannel(
        name, _detector(),
        OneEuroSmoother2D(freq=30.0, min_cutoff=1.2, beta=5.7, d_cutoff=1.0),
        CursorController(dead_zone_pixels=0, click_freeze_seconds=0.0),
    )


@pytest.fixture
def env():
    """Relogio falso + pyautogui falso gravando as chamadas em ordem."""
    clock = FakeClock()
    gui = MagicMock()
    gui.size.return_value = (1920, 1080)
    with mock.patch.object(gd_mod, "time", clock), \
            mock.patch.object(th_mod, "time", clock), \
            mock.patch.object(cc_mod, "time", clock), \
            mock.patch.object(cc_mod, "pyautogui", gui):
        two = TwoHandsController(right=_channel(RIGHT), left=_channel(LEFT))
        gui.reset_mock()
        yield two, clock, gui


def _feed(two, clock, *hands, frames=1):
    for _ in range(frames):
        clock.advance(FRAME)
        two.apply(two.update(hands))


def _settle(two, clock):
    """Duas maos abertas em cena, direita como dona."""
    _feed(two, clock, _right(), frames=5)
    _feed(two, clock, _left(), _right(), frames=10)
    assert two.owner is two.right


# 6. Acoes simultaneas ---------------------------------------------------

def test_both_pinches_same_frame_click_at_each_position(env):
    two, clock, gui = env
    _settle(two, clock)
    gui.reset_mock()
    left_pos = two.left.cursor.last_position
    right_pos = two.right.cursor.last_position
    assert left_pos != right_pos
    # Nao deixa a posicao da pinca mexer no alvo: ponteiros ja mapeados.
    for _ in range(6):
        clock.advance(FRAME)
        two.apply(two.update((_left(pinch=1.0), _right(pinch=1.0))))
    names = [c[0] for c in gui.method_calls]
    assert names.count("click") == 2
    # A dona clica no cursor real; a outra, na coordenada do proprio ponteiro.
    calls = [c for c in gui.method_calls if c[0] == "click"]
    owner_click, other_click = calls
    assert "x" not in owner_click.kwargs
    assert other_click.kwargs["x"] == two.left.cursor.last_position[0]
    # Depois do clique da outra mao o cursor real volta pra dona.
    tail = gui.method_calls[names.index("click", names.index("click") + 1) + 1]
    assert tail[0] == "moveTo"
    assert tail.args[:2] == tuple(two.right.cursor.last_position)


def test_only_owner_moves_real_cursor(env):
    two, clock, gui = env
    _settle(two, clock)
    gui.reset_mock()
    for i in range(10):
        _feed(two, clock, _left(cx=0.3, cy=0.5 + i * 0.001), _right(cx=0.7 + i * 0.005))
    for call in gui.moveTo.call_args_list:
        assert call.args[0] > 960  # direita da tela: ponteiro da dona


def test_left_only_works_like_right(env):
    two, clock, gui = env
    _feed(two, clock, _left(), frames=5)
    assert two.owner is two.left
    gui.reset_mock()
    _feed(two, clock, _left(pinch=1.0), frames=6)
    assert gui.click.call_count == 1
    assert "x" not in gui.click.call_args.kwargs


# 7. Arrasto -------------------------------------------------------------

def _start_drag(two, clock, hold_left=False):
    left, right = (_left(pinch=1.0), _right()) if hold_left else (_left(), _right(pinch=1.0))
    _feed(two, clock, left, right, frames=60)


def test_other_hand_cannot_touch_drag(env):
    two, clock, gui = env
    _settle(two, clock)
    _start_drag(two, clock)
    assert two.right.cursor.is_dragging
    gui.reset_mock()
    # Mao esquerda pinca e move: nem clique, nem mouseUp, nem salto do cursor.
    _feed(two, clock, _left(cx=0.2, pinch=1.0), _right(pinch=1.0), frames=10)
    _feed(two, clock, _left(cx=0.2), _right(pinch=1.0), frames=5)
    assert gui.click.call_count == 0
    assert gui.mouseUp.call_count == 0
    assert two.right.cursor.is_dragging
    for call in gui.moveTo.call_args_list:
        assert call.args[0] > 960


def test_non_owner_starting_drag_becomes_owner(env):
    two, clock, gui = env
    _settle(two, clock)
    gui.reset_mock()
    _start_drag(two, clock, hold_left=True)
    assert two.owner is two.left
    assert two.left.cursor.is_dragging and not two.right.cursor.is_dragging
    assert gui.mouseDown.call_count == 1
    # mouseDown aconteceu no ponteiro da esquerda, nao no da direita.
    names = [c[0] for c in gui.method_calls]
    down = names.index("mouseDown")
    before = [c for c in gui.method_calls[:down] if c[0] == "moveTo"]
    assert before[-1].args[0] < 960


# 8. Mao perdida ---------------------------------------------------------

def test_lost_hand_releases_drag_without_resetting_other(env):
    two, clock, gui = env
    _settle(two, clock)
    _start_drag(two, clock)
    left_detector_reset = mock.patch.object(
        two.left.detector, "reset", wraps=two.left.detector.reset,
    )
    with left_detector_reset as spy:
        _feed(two, clock, _left(), frames=12)
        spy.assert_not_called()
    assert gui.mouseUp.call_count == 1
    assert not two.right.cursor.is_dragging
    # So depois do arrasto encerrado o cursor passa pra quem ficou.
    assert two.owner is two.left


def test_reset_releases_button_and_returns_to_right(env):
    two, clock, gui = env
    _feed(two, clock, _left(pinch=1.0), frames=60)
    assert two.left.cursor.is_dragging
    two.reset()
    assert gui.mouseUp.call_count == 1
    assert two.owner is two.right
    assert two.right.cursor.drives_os_cursor
    assert not two.left.cursor.drives_os_cursor


def test_poses_follow_channels(env):
    two, clock, _ = env
    _settle(two, clock)
    right, left = two.poses()
    assert right[1] > left[1]
    # Falha curta: a mao 3D segura a ultima pose; depois, some.
    _feed(two, clock, _left(), frames=1)
    assert two.poses()[0] is not None
    _feed(two, clock, _left(), frames=DISPLAY_HOLD_FRAMES + 1)
    right, left = two.poses()
    assert right is None and left is not None


# Service ----------------------------------------------------------------

def _service():
    svc = VirtualMouseService.__new__(VirtualMouseService)
    svc.tracker = MagicMock()
    svc.gesture_detector = _detector()
    svc.smoother = OneEuroSmoother2D(freq=30.0, min_cutoff=1.2, beta=5.7, d_cutoff=1.0)
    svc.cursor = CursorController(dead_zone_pixels=0)
    svc.runtime_settings = RuntimeSettings(initial_profile="smooth")
    svc.ui = MagicMock()
    svc.settings_panel = None
    svc.hologram = None
    svc._perf = MagicMock()
    svc._presentation = MagicMock()
    svc._presentation_mode = False
    svc._presentation_next_key = "right"
    svc._presentation_prev_key = "left"
    svc._two_hands_mode = False
    svc._two_hands = None
    svc._vision_lock = threading.RLock()
    svc._hand_gate = HandGate(confirm_frames=1)
    svc._last_hand = None
    return svc


# 1. Modo desligado ------------------------------------------------------

def test_off_mode_does_not_create_second_channel():
    svc = _service()
    assert svc._two_hands is None
    svc.tracker.set_max_hands.assert_not_called()
    assert svc._pipelines() == [(svc.gesture_detector, svc.smoother, svc.cursor)]


def test_off_mode_tick_matches_plain_detector():
    """O _tick desligado entrega hands[0] ao detector, como antes do modo."""
    clock = FakeClock()
    gui = MagicMock()
    gui.size.return_value = (1920, 1080)
    with mock.patch.object(gd_mod, "time", clock), \
            mock.patch.object(cc_mod, "pyautogui", gui):
        svc = _service()
        svc.camera = MagicMock()
        svc.camera.read_new.return_value = (np.zeros((4, 6, 3), np.uint8), 1)
        svc._fps = MagicMock()
        svc._frame_seq = 0
        svc._camera_lost = False
        svc._frame_shape = None
        svc._hand_lost_frames = 0
        svc._pre_resize_enabled = False
        svc.enable_preview = False
        svc.tracker.process_with_raw.side_effect = lambda _rgb: (hands, hands)
        reference = _detector()
        seen = []
        seq = [0.0] * 5 + [1.0] * 6 + [0.0] * 5
        for pinch in seq:
            # Segunda mao na lista nao influencia nada.
            hand = make_hand(0.5, 0.5, pinch=pinch)
            hands = [hand, _left(cx=0.1)]
            clock.advance(FRAME)
            svc._tick()
            seen.extend(e.gesture for e in reference.update(hand))
    assert seen.count(Gesture.CLICK) == 1
    assert gui.click.call_count == 1
    assert svc._two_hands is None


# 2. Rastreador ----------------------------------------------------------

def test_toggle_recreates_tracker():
    svc = _service()
    svc._set_two_hands_mode(True)
    svc.tracker.set_max_hands.assert_called_with(2)
    assert svc._two_hands_mode and svc._two_hands is not None
    second = svc._two_hands.left
    svc._set_two_hands_mode(False)
    svc.tracker.set_max_hands.assert_called_with(1)
    assert not svc._two_hands_mode
    # Reativar reaproveita o canal ja criado.
    svc._set_two_hands_mode(True)
    assert svc._two_hands.left is second


def test_tracker_failure_stays_with_one_hand():
    svc = _service()
    svc.tracker.set_max_hands.side_effect = RuntimeError("sem memoria")
    svc._set_two_hands_mode(True)
    assert not svc._two_hands_mode
    assert svc._two_hands is None


def test_turning_off_releases_second_hand_drag():
    svc = _service()
    svc._set_two_hands_mode(True)
    left_cursor = svc._two_hands.left.cursor
    left_cursor.drag_start()
    assert left_cursor.is_dragging
    svc._set_two_hands_mode(False)
    assert not left_cursor.is_dragging


# 3. Exclusividade -------------------------------------------------------

def test_modes_are_exclusive():
    svc = _service()
    svc._set_two_hands_mode(True)
    svc._set_presentation_mode(True)
    assert svc._presentation_mode and not svc._two_hands_mode
    svc._set_two_hands_mode(True)
    assert svc._two_hands_mode and not svc._presentation_mode


# 9. Ajustes -------------------------------------------------------------

def test_panel_settings_reach_both_hands():
    svc = _service()
    svc._set_two_hands_mode(True)
    left = svc._two_hands.left
    svc._apply_setting_live("pinch_distance_threshold", 0.123)
    svc._apply_setting_live("anchor_freeze_duration_ms", 250.0)
    svc._apply_setting_live("one_euro_beta", 9.0)
    assert svc.gesture_detector.pinch_threshold == left.detector.pinch_threshold == 0.123
    assert svc.cursor.click_freeze_seconds == left.cursor.click_freeze_seconds == 0.25
    assert svc.smoother._fx.beta == left.smoother._fx.beta == 9.0
    svc._on_toggle("aim", False)
    svc._on_toggle("sticky", False)
    assert not left.detector.aim_assist_enabled
    assert not left.detector.sticky_targeting_enabled
    svc.runtime_settings.apply_profile("precise")
    svc._sync_detector_from_settings()
    expected = svc.runtime_settings.get("pinch_distance_threshold")
    assert left.detector.pinch_threshold == expected


def test_panel_toggle_routes_to_mode():
    svc = _service()
    svc._on_toggle("two_hands", True)
    assert svc._two_hands_mode


# 10. Holograma ----------------------------------------------------------

@pytest.fixture
def backend():
    gl = gl_mod.HologramGLBackend.__new__(gl_mod.HologramGLBackend)
    gl._slots = HandPoseSlots(180, 16 / 9)
    gl._meshes = {}
    gl._renderer = MagicMock()
    gl._enabled = True
    gl._window = None
    gl._screen_w, gl._screen_h = 1920, 1080
    with mock.patch.object(gl_mod, "build_hand_mesh", wraps=gl_mod.build_hand_mesh) as build:
        yield gl, build


def _poses():
    return [(make_hand(0.7, 0.5).landmarks, 1200, 500),
            (make_hand(0.3, 0.5).landmarks, 600, 500)]


def test_update_poses_two_one_none(backend):
    gl, _ = backend
    gl.update_poses(_poses())
    gl._render()
    assert gl._renderer.render.call_count == 2
    gl._renderer.reset_mock()

    gl.update_poses([_poses()[0], None])
    gl._render()
    assert gl._renderer.render.call_count == 1
    gl._renderer.reset_mock()

    gl.update_poses([None, None])
    gl._render()
    gl._renderer.render.assert_not_called()


def test_mesh_rebuilt_only_for_new_pose(backend):
    gl, build = backend
    gl.update_poses(_poses())
    gl._render()
    gl._render()
    gl._render()
    assert build.call_count == 2
    gl.update_poses([_poses()[0], None])
    gl._render()
    assert build.call_count == 3


def test_update_pose_hides_second_hand(backend):
    gl, _ = backend
    gl.update_poses(_poses())
    gl.update_pose(_poses()[0][0], 1200, 500)
    gl._render()
    assert gl._renderer.render.call_count == 1


def test_first_slot_is_single_until_used():
    slots = HandPoseSlots(180, 16 / 9)
    assert len(slots.posed) == 1
    slots.update(_poses())
    assert len(slots.posed) == 2 and slots.any_visible
    slots.clear()
    assert not slots.any_visible
