"""Testes pros novos metodos click_at do CursorController.

Mocka pyautogui pra nao mexer no cursor real do sistema durante o teste.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def _install_pyautogui_mock(monkeypatch):
    """Instala pyautogui mockado antes do import do cursor_controller."""
    mock = MagicMock()
    mock.size = MagicMock(return_value=(1920, 1080))
    monkeypatch.setitem(sys.modules, "pyautogui", mock)
    return mock


class TestClickAt:
    def test_click_at_calls_pyautogui_with_coords(self, monkeypatch):
        mock = _install_pyautogui_mock(monkeypatch)
        # Re-import depois do mock
        from importlib import reload
        import core.cursor_controller as cc
        reload(cc)
        cursor = cc.CursorController()

        cursor.click_at(500, 300)

        mock.click.assert_called_once()
        kwargs = mock.click.call_args.kwargs
        assert kwargs["x"] == 500
        assert kwargs["y"] == 300

    def test_click_at_clamps_to_screen(self, monkeypatch):
        mock = _install_pyautogui_mock(monkeypatch)
        from importlib import reload
        import core.cursor_controller as cc
        reload(cc)
        cursor = cc.CursorController()

        cursor.click_at(99999, -50)
        kwargs = mock.click.call_args.kwargs
        # Clampado
        assert 0 <= kwargs["x"] <= 1919
        assert 0 <= kwargs["y"] <= 1079

    def test_click_at_updates_last_position(self, monkeypatch):
        _install_pyautogui_mock(monkeypatch)
        from importlib import reload
        import core.cursor_controller as cc
        reload(cc)
        cursor = cc.CursorController()

        cursor.click_at(400, 250)
        assert cursor._last_x == 400
        assert cursor._last_y == 250


class TestRightClickAt:
    def test_right_click_at_uses_right_click(self, monkeypatch):
        mock = _install_pyautogui_mock(monkeypatch)
        from importlib import reload
        import core.cursor_controller as cc
        reload(cc)
        cursor = cc.CursorController()

        cursor.right_click_at(800, 600)
        mock.rightClick.assert_called_once()
        kwargs = mock.rightClick.call_args.kwargs
        assert kwargs["x"] == 800
        assert kwargs["y"] == 600


class TestDoubleClickAt:
    def test_double_click_at_calls_click_twice(self, monkeypatch):
        mock = _install_pyautogui_mock(monkeypatch)
        from importlib import reload
        import core.cursor_controller as cc
        reload(cc)
        cursor = cc.CursorController()

        cursor.double_click_at(500, 300)
        # 1 moveTo + 2 click
        assert mock.click.call_count == 2
        mock.moveTo.assert_called_once_with(500, 300, _pause=False)


class TestDragStartAt:
    def test_drag_start_at_sets_dragging(self, monkeypatch):
        mock = _install_pyautogui_mock(monkeypatch)
        from importlib import reload
        import core.cursor_controller as cc
        reload(cc)
        cursor = cc.CursorController()

        assert cursor.is_dragging is False
        cursor.drag_start_at(400, 200)
        assert cursor.is_dragging is True
        mock.mouseDown.assert_called_once()
        kwargs = mock.mouseDown.call_args.kwargs
        assert kwargs["x"] == 400
        assert kwargs["y"] == 200

    def test_drag_start_at_noop_when_already_dragging(self, monkeypatch):
        mock = _install_pyautogui_mock(monkeypatch)
        from importlib import reload
        import core.cursor_controller as cc
        reload(cc)
        cursor = cc.CursorController()

        cursor.drag_start_at(100, 100)
        cursor.drag_start_at(200, 200)
        # So uma chamada
        assert mock.mouseDown.call_count == 1
