"""Mesh 3D da mao: integridade, proporcoes e custo."""

from __future__ import annotations

import time

import numpy as np
import pytest

from core.hand_mesh import build_hand_mesh, generate_hand_mesh
from core.hand_pose import HandPoseSolver
from tests.synthetic_hand import make_hand


def _posed(pinch=0.0, landmarks=None):
    s = HandPoseSolver(hand_size_px=200, frame_aspect=16 / 9)
    lm = landmarks if landmarks is not None else make_hand(0.5, 0.5, pinch=pinch).landmarks
    return s.solve(lm, 600, 400, now=0.0)


@pytest.mark.parametrize("pinch", [0.0, 1.0])
def test_mesh_is_valid(pinch):
    m = build_hand_mesh(_posed(pinch))
    n = len(m.vertices)
    assert n > 500
    assert m.indices.size % 3 == 0
    assert int(m.indices.max()) < n
    assert np.isfinite(m.vertices).all() and np.isfinite(m.normals).all()
    assert np.allclose(np.linalg.norm(m.normals, axis=1), 1.0, atol=1e-3)
    assert m.fade.shape == (n,)
    assert m.fade.min() >= 0.0 and m.fade.max() <= 1.0
    assert (m.fade == 0.0).any()  # antebraco some


def test_fingertips_end_at_landmarks():
    """A ponta modelada termina no landmark da ponta (nem curta nem longa)."""
    posed = _posed()
    m = build_hand_mesh(posed)
    for tip in (4, 8, 12, 16, 20):
        d = np.linalg.norm(m.vertices[:, :2] - posed.points[tip, :2], axis=1).min()
        assert d < 0.05 * posed.palm_px


def test_mesh_covers_every_landmark():
    posed = _posed()
    m = build_hand_mesh(posed)
    for p in posed.points:
        d = np.linalg.norm(m.vertices[:, :2] - p[:2], axis=1).min()
        assert d < 0.25 * posed.palm_px


def test_degenerate_pose_does_not_crash():
    m = build_hand_mesh(_posed(landmarks=[(0.5, 0.5, 0.0)] * 21))
    assert np.isfinite(m.vertices).all()


def test_legacy_wrapper_still_works():
    v, n, i = generate_hand_mesh(make_hand(0.5, 0.5).landmarks, 500, 400, 180)
    assert len(v) == len(n) > 0 and i.size > 0
    v, n, i = generate_hand_mesh([(0.5, 0.5, 0.0)] * 3, 500, 400, 180)
    assert len(v) == 0


def test_mesh_build_is_cheap():
    # Melhor de 5 (o minimo ignora picos de carga da maquina/CI). ~3 ms
    # medido localmente; o limite so pega regressao grosseira (ex: loop
    # Python por triangulo).
    posed = _posed()
    build_hand_mesh(posed)
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        build_hand_mesh(posed)
        times.append(time.perf_counter() - t0)
    assert min(times) < 0.03
