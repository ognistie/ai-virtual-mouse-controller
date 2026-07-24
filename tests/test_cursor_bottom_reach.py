"""Testes dos mecanismos de alcance da borda inferior (taskbar).

Cobrem os tres estagios: Y-boost (mapeamento), edge snap (gap terminal)
e border creep (homing parado). Mocka pyautogui pra nao mexer no cursor
real do sistema.
"""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock

SCREEN_W, SCREEN_H = 1920, 1080


def _make_cursor(monkeypatch, **kwargs):
    mock = MagicMock()
    mock.size = MagicMock(return_value=(SCREEN_W, SCREEN_H))
    monkeypatch.setitem(sys.modules, "pyautogui", mock)
    from importlib import reload
    import core.cursor_controller as cc
    reload(cc)
    defaults = dict(
        screen_margin_x=0.08,
        screen_margin_top=0.04,
        screen_margin_bottom=0.08,
        dead_zone_pixels=0,
    )
    defaults.update(kwargs)
    return cc.CursorController(**defaults), mock


# ---------------------------------------------------------------------
# S1 — Y bottom boost
# ---------------------------------------------------------------------

class TestYBottomBoost:
    def test_disabled_keeps_linear_mapping(self, monkeypatch):
        cursor, _ = _make_cursor(monkeypatch, y_bottom_boost_enabled=False)
        # Meio exato das margens -> meio da tela
        _, y = cursor.map_to_screen(0.5, (0.04 + 0.92) / 2)
        assert abs(y - (SCREEN_H - 1) / 2) <= 1

    def test_above_knee_stays_linear(self, monkeypatch):
        on, _ = _make_cursor(monkeypatch, y_bottom_boost_enabled=True,
                             y_bottom_boost_knee=0.72)
        off, _ = _make_cursor(monkeypatch, y_bottom_boost_enabled=False)
        # ny que resulta em ty < knee: identico com e sem boost
        ny_mid = 0.04 + 0.5 * (0.92 - 0.04)
        assert on.map_to_screen(0.5, ny_mid)[1] == off.map_to_screen(0.5, ny_mid)[1]

    def test_below_knee_reaches_further_down(self, monkeypatch):
        on, _ = _make_cursor(monkeypatch, y_bottom_boost_enabled=True,
                             y_bottom_boost_knee=0.72,
                             y_bottom_boost_power=1.6)
        off, _ = _make_cursor(monkeypatch, y_bottom_boost_enabled=False)
        # ty ~0.86 (dentro do trecho boosted): boost desce MAIS que linear
        ny = 0.04 + 0.86 * (0.92 - 0.04)
        assert on.map_to_screen(0.5, ny)[1] > off.map_to_screen(0.5, ny)[1]

    def test_extremes_are_preserved(self, monkeypatch):
        cursor, _ = _make_cursor(monkeypatch, y_bottom_boost_enabled=True)
        assert cursor.map_to_screen(0.5, 0.04)[1] == 0
        assert cursor.map_to_screen(0.5, 0.92)[1] == SCREEN_H - 1

    def test_curve_is_monotonic(self, monkeypatch):
        cursor, _ = _make_cursor(monkeypatch, y_bottom_boost_enabled=True)
        ys = [cursor.map_to_screen(0.5, 0.04 + t * 0.88)[1]
              for t in (0.0, 0.2, 0.4, 0.6, 0.72, 0.8, 0.9, 1.0)]
        assert ys == sorted(ys)


# ---------------------------------------------------------------------
# S2 — Edge snap
# ---------------------------------------------------------------------

class TestEdgeSnap:
    def test_snaps_to_bottom_when_moving_down_in_zone(self, monkeypatch):
        cursor, mock = _make_cursor(monkeypatch, edge_snap_px=36,
                                    y_bottom_boost_enabled=False)
        # Posiciona acima da zona, depois desce pra dentro dela
        cursor.move(0.5, 0.80)
        y_before = mock.moveTo.call_args.args[1]
        assert y_before < SCREEN_H - 1 - 36
        cursor.move(0.5, 0.90)  # mapeia pra dentro dos 36px finais
        y_after = mock.moveTo.call_args.args[1]
        assert y_after == SCREEN_H - 1

    def test_no_snap_when_moving_up(self, monkeypatch):
        cursor, mock = _make_cursor(monkeypatch, edge_snap_px=36,
                                    y_bottom_boost_enabled=False)
        cursor.move(0.5, 0.92)   # fundo (snap)
        cursor.move(0.5, 0.895)  # sobe um pouco — sem snap
        y = mock.moveTo.call_args.args[1]
        assert y < SCREEN_H - 1

    def test_snap_disabled_with_zero_px(self, monkeypatch):
        cursor, mock = _make_cursor(monkeypatch, edge_snap_px=0,
                                    y_bottom_boost_enabled=False)
        cursor.move(0.5, 0.916)  # ~5px acima do fundo
        y = mock.moveTo.call_args.args[1]
        assert y < SCREEN_H - 1


# ---------------------------------------------------------------------
# S3 — Border creep
# ---------------------------------------------------------------------

class TestBorderCreep:
    def test_creep_pushes_down_after_delay(self, monkeypatch):
        cursor, mock = _make_cursor(
            monkeypatch,
            edge_snap_px=0,
            edge_creep_enabled=True,
            edge_creep_band_px=120,
            edge_creep_delay_s=0.05,
            edge_creep_rate_px_s=400.0,
            y_bottom_boost_enabled=False,
        )
        ny = 0.905  # dentro da banda de 120px, acima do fundo
        cursor.move(0.5, ny)
        y0 = mock.moveTo.call_args.args[1]
        time.sleep(0.08)  # passa o delay
        cursor.move(0.5, ny)
        time.sleep(0.05)  # acumula creep
        cursor.move(0.5, ny)
        y1 = mock.moveTo.call_args.args[1]
        assert y1 > y0

    def test_creep_resets_on_move_up(self, monkeypatch):
        cursor, mock = _make_cursor(
            monkeypatch,
            edge_snap_px=0,
            edge_creep_enabled=True,
            edge_creep_band_px=120,
            edge_creep_delay_s=0.01,
            edge_creep_rate_px_s=400.0,
            y_bottom_boost_enabled=False,
        )
        ny = 0.905
        cursor.move(0.5, ny)
        time.sleep(0.05)
        cursor.move(0.5, ny)  # creep ativo
        cursor.move(0.5, 0.60)  # sobe — reset
        assert cursor._creep_entered_at is None
        assert cursor._creep_offset == 0.0

    def test_creep_never_passes_bottom(self, monkeypatch):
        cursor, mock = _make_cursor(
            monkeypatch,
            edge_snap_px=0,
            edge_creep_enabled=True,
            edge_creep_band_px=120,
            edge_creep_delay_s=0.0,
            edge_creep_rate_px_s=100000.0,  # exagerado de proposito
            y_bottom_boost_enabled=False,
        )
        ny = 0.905
        for _ in range(4):
            cursor.move(0.5, ny)
            time.sleep(0.02)
        y = mock.moveTo.call_args.args[1]
        assert y == SCREEN_H - 1

    def test_creep_disabled_is_inert(self, monkeypatch):
        cursor, mock = _make_cursor(
            monkeypatch,
            edge_snap_px=0,
            edge_creep_enabled=False,
            y_bottom_boost_enabled=False,
        )
        ny = 0.905
        cursor.move(0.5, ny)
        y0 = mock.moveTo.call_args.args[1]
        time.sleep(0.2)
        cursor.move(0.5, ny)
        y1 = mock.moveTo.call_args.args[1]
        assert y1 == y0
