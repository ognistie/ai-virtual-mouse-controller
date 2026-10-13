"""HandPoseSolver: a mao virtual tem que se mexer como a mao real."""

from __future__ import annotations

import numpy as np
import pytest

from core.hand_pose import HAND_LENGTH_PALMS, HandPoseSolver
from tests.synthetic_hand import make_hand

ASPECT = 16 / 9


def _solve(solver, hand, cx=500.0, cy=400.0, t=0.0):
    return solver.solve(hand.landmarks, cx, cy, now=t)


def _open(size=0.22, x=0.5, y=0.5, pinch=0.0):
    return make_hand(x, y, pinch=pinch, size=size)


def test_none_resets_and_returns_none():
    s = HandPoseSolver(frame_aspect=ASPECT)
    assert s.solve(None, 0, 0) is None
    assert s.solve([(0.5, 0.5, 0.0)] * 5, 0, 0) is None


def test_aspect_is_isotropic():
    """Um quadrado em PIXELS de camera continua quadrado na tela: x
    normalizado pela largura e y pela altura nao podem ter a mesma escala."""
    s = HandPoseSolver(frame_aspect=ASPECT)
    lm = list(_open().landmarks)
    # Ponto 20 a 0.1 da ALTURA de distancia do 0 na horizontal e vertical
    # (em pixels: dx = 0.1*H, dy = 0.1*H).
    lm[0] = (0.5, 0.5, 0.0)
    lm[20] = (0.5 + 0.1 / ASPECT, 0.5, 0.0)
    lm[4] = (0.5, 0.6, 0.0)
    posed = s.solve(lm, 500, 400, now=0.0)
    dx = abs(posed.points[20][0] - posed.points[0][0])
    dy = abs(posed.points[4][1] - posed.points[0][1])
    assert dx == pytest.approx(dy, rel=1e-3)


def test_hand_size_matches_config_and_ignores_fist():
    """Escala pela palma: fechar a mao nao "infla" a mao virtual."""
    s1 = HandPoseSolver(hand_size_px=190, frame_aspect=ASPECT)
    s2 = HandPoseSolver(hand_size_px=190, frame_aspect=ASPECT)
    open_ = _solve(s1, _open())
    pinch = _solve(s2, _open(pinch=1.0))
    assert open_.palm_px == pytest.approx(190 / HAND_LENGTH_PALMS, rel=0.1)
    assert pinch.palm_px == pytest.approx(open_.palm_px, rel=0.02)


def test_closer_hand_same_screen_size():
    """Mao mais perto da camera (maior no frame) nao vira mao gigante."""
    near = _solve(HandPoseSolver(frame_aspect=ASPECT), _open(size=0.32))
    far = _solve(HandPoseSolver(frame_aspect=ASPECT), _open(size=0.16))
    assert near.palm_px == pytest.approx(far.palm_px, rel=0.02)


def test_pinch_point_lands_on_cursor():
    s = HandPoseSolver(frame_aspect=ASPECT)
    posed = _solve(s, _open(), cx=700, cy=300)
    assert posed.pinch_point == pytest.approx((700, 300), abs=0.5)


def test_hand_translates_one_to_one_with_cursor():
    s = HandPoseSolver(frame_aspect=ASPECT)
    a = _solve(s, _open(), cx=500, cy=400, t=0.0)
    b = _solve(s, _open(), cx=560, cy=380, t=1 / 30)
    delta = b.points - a.points
    assert np.allclose(delta[:, 0], 60, atol=0.5)
    assert np.allclose(delta[:, 1], -20, atol=0.5)


def test_closing_fingers_does_not_jerk_the_palm():
    """Articulacao dos dedos nao arrasta a mao inteira: com o cursor
    parado, a palma quase nao se move no frame em que a pinca fecha."""
    s = HandPoseSolver(frame_aspect=ASPECT)
    for i in range(10):
        a = _solve(s, _open(), t=i / 30)
    b = _solve(s, _open(pinch=1.0), t=10 / 30)
    palm = [0, 5, 9, 13, 17]
    shift = np.linalg.norm(b.points[palm, :2] - a.points[palm, :2], axis=1).max()
    tip_move = np.linalg.norm(b.points[4, :2] - a.points[4, :2])
    assert shift < 0.25 * tip_move


def test_pinch_strength():
    s1 = HandPoseSolver(frame_aspect=ASPECT)
    s2 = HandPoseSolver(frame_aspect=ASPECT)
    assert _solve(s1, _open()).pinch == pytest.approx(0.0)
    assert _solve(s2, _open(pinch=1.0)).pinch == pytest.approx(1.0)


def test_depth_seen_from_behind_the_hand():
    """Ponto mais LONGE da camera (z maior no MediaPipe) fica mais PERTO
    de quem olha a tela (vista por tras da mao, como em VR)."""
    s = HandPoseSolver(frame_aspect=ASPECT)
    lm = [list(p) for p in _open().landmarks]
    lm[8][2] = 0.05  # ponta do indicador mais longe da camera que o pulso
    posed = s.solve([tuple(p) for p in lm], 500, 400, now=0.0)
    assert posed.points[8][2] > posed.points[0][2]


def test_aspect_change_resets_state():
    s = HandPoseSolver(frame_aspect=ASPECT)
    _solve(s, _open())
    s.set_frame_aspect(4 / 3)
    assert s.frame_aspect == pytest.approx(4 / 3)
    assert s._filters is None
