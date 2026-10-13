"""Sensacao do cursor: fim da trava do clique sem salto e drag sem
offset permanente."""

from __future__ import annotations

from unittest import mock

import pytest

import core.cursor_controller as cc_mod
from tests.synthetic_hand import FakeClock

W, H = 1920, 1080


@pytest.fixture
def env():
    clock = FakeClock()
    gui = mock.MagicMock()
    gui.size.return_value = (W, H)
    with mock.patch.object(cc_mod, "pyautogui", gui), mock.patch.object(cc_mod, "time", clock):
        yield clock, gui


def _cursor(**kw):
    params = {
        "screen_margin_percentage": 0.0,
        "dead_zone_pixels": 0,
        "click_freeze_seconds": 0.12,
        "bottom_reach_enabled": False,
    }
    params.update(kw)
    return cc_mod.CursorController(**params)


def _pos(gui):
    args = gui.moveTo.call_args.args
    return args[0], args[1]


def test_unfreeze_catches_up_without_jump(env):
    clock, gui = env
    c = _cursor()
    c.move(0.30, 0.5)
    start_x = _pos(gui)[0]
    c.click()
    # Mao segue andando durante a trava de 120 ms (cursor parado).
    for i in range(1, 4):
        clock.advance(1 / 30)
        c.move(0.30 + 0.01 * i, 0.5)
    assert _pos(gui)[0] == start_x
    target_x = (0.30 + 0.01 * 4) * (W - 1)
    gap = target_x - start_x

    clock.advance(1 / 30)  # trava acabou
    c.move(0.34, 0.5)
    first = _pos(gui)[0]
    # Nao salta o gap inteiro de uma vez...
    assert first - start_x < 0.75 * gap
    # ...mas alcanca a mao em poucos frames.
    for _ in range(8):
        clock.advance(1 / 30)
        c.move(0.34, 0.5)
    assert abs(_pos(gui)[0] - target_x) <= cc_mod.CursorController._CATCH_UP_DONE_PX


def test_follows_one_to_one_after_catch_up(env):
    clock, gui = env
    c = _cursor()
    c.move(0.5, 0.5)
    c.click()
    clock.advance(0.2)
    for _ in range(10):
        clock.advance(1 / 30)
        c.move(0.5, 0.5)
    clock.advance(1 / 30)
    c.move(0.6, 0.5)
    assert _pos(gui)[0] == int(0.6 * (W - 1))


def test_drag_converges_to_hand_without_permanent_offset(env):
    clock, gui = env
    c = _cursor(drag_precision_factor=0.55, click_freeze_seconds=0.0)
    c.move(0.5, 0.5)
    c.drag_start()
    for _ in range(20):
        clock.advance(1 / 30)
        c.move(0.6, 0.5)
    c.drag_end()
    clock.advance(1 / 30)
    c.move(0.6, 0.5)
    assert abs(_pos(gui)[0] - int(0.6 * (W - 1))) <= 1
