"""Gate de regressao do "feel" do cursor (pipeline real, maos sinteticas).

Usa scripts/eval_motion.py. Limites com folga sobre o medido na v1.3
(media de 10 seeds) e bem abaixo do baseline v1.2.0, que tinha:
    move_lag_px ~133, move_settle_ms ~670, flicker_clicks ~1.3
Pra investigar uma falha: `python scripts/eval_motion.py`.
"""

from __future__ import annotations

import importlib.util
import logging
import statistics
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "eval_motion.py"
_spec = importlib.util.spec_from_file_location("eval_motion", _PATH)
em = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(em)

SEEDS = range(4)
# Ruido na pinca e' raro por seed — mais seeds pra o gate pegar regressao
# (no baseline v1.2.0, 9 de 16 seeds geravam clique extra).
FLICKER_SEEDS = range(16)


@pytest.fixture(autouse=True)
def _quiet():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def _mean(scenario, key):
    return statistics.fmean(scenario(s, 1)[key] for s in SEEDS)


def test_cursor_rest_is_steady():
    assert _mean(em.scenario_rest, "rest_jitter_px") < 1.5


def test_cursor_keeps_up_with_fast_moves():
    # v1.3: ~41 px / ~150 ms. v1.2.0: ~133 px / ~670 ms.
    assert _mean(em.scenario_move, "move_lag_px") < 60
    assert _mean(em.scenario_move, "move_settle_ms") < 300


def test_noisy_pinch_is_a_single_click():
    for seed in FLICKER_SEEDS:
        r = em.scenario_flicker(seed, 1)
        assert r["flicker_clicks"] == 1
        assert r["flicker_drag_drops"] == 0


def test_deliberate_double_pinch_is_two_clicks():
    for seed in SEEDS:
        assert em.scenario_repeat(seed, 1)["repeat_clicks"] == 2


def test_click_lands_where_hand_aimed():
    assert _mean(em.scenario_click, "click_drift_px") < 6


def test_no_jump_after_click_freeze():
    r = [em.scenario_click_move(s, 1) for s in SEEDS]
    worst = statistics.fmean(x["click_move_max_step_px"] for x in r)
    typical = statistics.fmean(x["click_move_typical_step_px"] for x in r)
    assert worst < 1.4 * typical


def test_cursor_reaches_taskbar():
    assert _mean(em.scenario_bottom, "bottom_reach_px") < 40


def test_tilting_wrist_does_not_move_cursor():
    # Mao parada, so inclinando: v1.2.0 ~153 px de trajeto em 2 s;
    # sem suavizar o tamanho da mao ~200 px; agora ~42 px.
    assert _mean(em.scenario_tilt, "tilt_path_px") < 80


def test_side_edge_reached_without_overshoot():
    # v1.2.0: faltavam ~42 px pra borda; depois do fix da ancora sem
    # votacao: alcancava mas passava ~40 px e voltava.
    assert _mean(em.scenario_side_edge, "edge_overshoot_px") < 8
    assert _mean(em.scenario_side_edge, "edge_reach_px") < 15
