"""Testes pro renderer da mao holografica.

Renderer e' funcao pura: input deterministico -> output deterministico.
Nada de GUI aqui.
"""

from __future__ import annotations

import math

import pytest

from core.hand_renderer import (
    HAND_BONES,
    Bone,
    HandPrimitives,
    Point,
    compute_hand_primitives,
)


# Mao "neutra": 21 landmarks numa cruzinha simetrica em torno do landmark 9
# que esta em (0.5, 0.5). Cada landmark tem coords distintas.
def _flat_hand():
    pts = [(0.5, 0.5, 0.0)] * 21
    # Espalhar pontos em uma grade simples (5 dedos x 4 juntas + wrist)
    pts[0] = (0.50, 0.70, 0.0)   # wrist
    # dedos com MCP, PIP, DIP, TIP em diagonal
    finger_offsets = [
        (-0.15, 0.0),   # polegar lateral
        (-0.08, -0.05),  # indicador
        (0.0, -0.05),    # medio
        (0.08, -0.05),   # anelar
        (0.15, -0.05),   # mindinho
    ]
    for finger_idx, (dx_base, dy_base) in enumerate(finger_offsets):
        for joint in range(4):
            i = 1 + finger_idx * 4 + joint
            pts[i] = (
                0.50 + dx_base + (joint + 1) * (dx_base * 0.1),
                0.60 + dy_base * (joint + 1) * 0.6,
                0.0,
            )
    return pts


class TestCompute:
    def test_returns_handprimitives(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert isinstance(out, HandPrimitives)

    def test_empty_input_returns_empty(self):
        out = compute_hand_primitives([], 100, 100, 200)
        assert out.bones == ()
        assert out.points == ()

    def test_short_input_returns_empty(self):
        # menos que 21 landmarks
        partial = [(0.5, 0.5, 0.0)] * 10
        out = compute_hand_primitives(partial, 100, 100, 200)
        assert out.bones == ()
        assert out.points == ()

    def test_produces_21_points(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert len(out.points) == 21

    def test_produces_expected_bone_count(self):
        # 5 dedos com 4 segmentos cada = 20, + palma com 3 segmentos = 23
        expected = sum(len(chain) - 1 for chain in HAND_BONES)
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert len(out.bones) == expected

    def test_anchor_lands_at_center(self):
        # Landmark 9 deve cair exatamente em (cx, cy)
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        p9 = out.points[9]
        assert math.isclose(p9.x, 500.0, abs_tol=0.5)
        assert math.isclose(p9.y, 300.0, abs_tol=0.5)

    def test_scales_to_fit_size(self):
        # Bounding box dos pontos deve ter no maximo size_px no lado maior
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        xs = [p.x for p in out.points]
        ys = [p.y for p in out.points]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        bigger = max(width, height)
        # Tolerancia: pode ter pequena imprecisao no max(bbox_w, bbox_h)
        assert bigger <= 200.0 + 1.0

    def test_radius_positive(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        for p in out.points:
            assert p.radius > 0

    def test_bone_width_positive(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        for b in out.bones:
            assert b.width > 0

    def test_deeper_landmark_gives_larger_point(self):
        # z mais negativo = mais perto da camera = ponto maior
        pts_near = list(_flat_hand())
        pts_far = list(_flat_hand())
        pts_near[8] = (pts_near[8][0], pts_near[8][1], -0.2)  # indicador perto
        pts_far[8] = (pts_far[8][0], pts_far[8][1], 0.2)      # indicador longe

        near = compute_hand_primitives(pts_near, 100, 100, 200)
        far = compute_hand_primitives(pts_far, 100, 100, 200)
        assert near.points[8].radius > far.points[8].radius

    def test_size_scaling_linear(self):
        small = compute_hand_primitives(_flat_hand(), 500, 300, 100)
        big = compute_hand_primitives(_flat_hand(), 500, 300, 400)

        def bbox(out):
            xs = [p.x for p in out.points]
            ys = [p.y for p in out.points]
            return max(xs) - min(xs), max(ys) - min(ys)

        bw_small = max(bbox(small))
        bw_big = max(bbox(big))
        # 4x size_px -> 4x bbox
        assert math.isclose(bw_big / bw_small, 4.0, rel_tol=0.05)


class TestBone:
    def test_bone_is_immutable(self):
        b = Bone(x1=0, y1=0, x2=10, y2=10, width=1)
        with pytest.raises(Exception):  # frozen dataclass
            b.x1 = 99  # type: ignore[misc]


class TestPoint:
    def test_point_is_immutable(self):
        p = Point(x=0, y=0, radius=1)
        with pytest.raises(Exception):
            p.x = 99  # type: ignore[misc]
