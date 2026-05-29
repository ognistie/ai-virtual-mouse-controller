"""Testes pro renderer da mao holografica.

Renderer e' funcao pura: input deterministico -> output deterministico.
"""

from __future__ import annotations

import math

import pytest

from core.hand_renderer import (
    FINGER_CHAINS,
    FINGERTIPS,
    PALM_VERTICES,
    PINCH_DOUBLE,
    PINCH_LEFT,
    PINCH_RIGHT,
    CloudParticle,
    HandPrimitives,
    Point,
    Polygon,
    Tip,
    _tube_polygon,
    compute_hand_primitives,
    generate_particle_cloud,
    get_particle_cloud,
)


def _flat_hand():
    """21 landmarks em forma de mao aberta razoavel."""
    pts = [(0.5, 0.5, 0.0)] * 21
    pts[0] = (0.50, 0.85, 0.0)    # wrist
    pts[1] = (0.40, 0.78, 0.0)    # thumb CMC
    finger_xs = [-0.18, -0.07, 0.0, 0.07, 0.17]  # thumb, index, middle, ring, pinky
    # Polegar: angulado
    for j in range(2, 5):
        pts[j] = (0.40 - 0.04 * j, 0.78 - 0.05 * j, 0.0)
    pts[4] = (0.28, 0.55, 0.0)  # thumb tip
    # Outros dedos (verticais)
    for i, base_x in enumerate(finger_xs[1:], start=1):
        for j in range(4):
            idx = 1 + i * 4 + j
            pts[idx] = (
                0.50 + base_x,
                0.78 - 0.05 - 0.07 * j,
                0.0,
            )
    return pts


class TestBasicCompute:
    def test_returns_handprimitives(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert isinstance(out, HandPrimitives)

    def test_empty_input_returns_empty(self):
        out = compute_hand_primitives([], 100, 100, 200)
        assert out.palm.points == ()
        assert out.fingers == ()
        assert out.joints == ()
        assert out.tips == ()

    def test_short_input_returns_empty(self):
        partial = [(0.5, 0.5, 0.0)] * 10
        out = compute_hand_primitives(partial, 100, 100, 200)
        assert out.fingers == ()


class TestPalm:
    def test_palm_has_six_vertices(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert len(out.palm.points) == len(PALM_VERTICES)
        assert len(out.palm.points) == 6

    def test_palm_outline_width_positive(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert out.palm.outline_width > 0

    def test_palm_outline_scales_with_size(self):
        small = compute_hand_primitives(_flat_hand(), 100, 100, 100)
        big = compute_hand_primitives(_flat_hand(), 100, 100, 400)
        assert big.palm.outline_width > small.palm.outline_width


class TestFingers:
    def test_five_fingers(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert len(out.fingers) == 5
        assert len(out.fingers) == len(FINGER_CHAINS)

    def test_each_finger_is_closed_polygon(self):
        # Cada tube tem N joints x 2 lados = 2N vertices
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        for finger, chain in zip(out.fingers, FINGER_CHAINS):
            expected_vertices = 2 * len(chain)
            assert len(finger.points) == expected_vertices

    def test_finger_outline_width_positive(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        for f in out.fingers:
            assert f.outline_width > 0


class TestJoints:
    def test_21_joints(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert len(out.joints) == 21

    def test_joint_radius_positive(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        for j in out.joints:
            assert j.radius > 0


class TestTips:
    def test_five_tips(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        assert len(out.tips) == len(FINGERTIPS)

    def test_tip_halo_larger_than_core(self):
        out = compute_hand_primitives(_flat_hand(), 100, 100, 200)
        for t in out.tips:
            assert t.halo_radius > t.core_radius


class TestClickPoints:
    def test_pinch_left_is_thumb_index_midpoint(self):
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        a, b = PINCH_LEFT  # (4, 8)
        thumb = out.joints[a]
        index = out.joints[b]
        expected_x = (thumb.x + index.x) / 2.0
        expected_y = (thumb.y + index.y) / 2.0
        assert math.isclose(out.pinch_left[0], expected_x, abs_tol=0.5)
        assert math.isclose(out.pinch_left[1], expected_y, abs_tol=0.5)

    def test_pinch_right_is_thumb_middle_midpoint(self):
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        a, b = PINCH_RIGHT  # (4, 12)
        thumb = out.joints[a]
        middle = out.joints[b]
        expected = ((thumb.x + middle.x) / 2.0, (thumb.y + middle.y) / 2.0)
        assert math.isclose(out.pinch_right[0], expected[0], abs_tol=0.5)
        assert math.isclose(out.pinch_right[1], expected[1], abs_tol=0.5)

    def test_pinch_double_is_index_middle_midpoint(self):
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        a, b = PINCH_DOUBLE  # (8, 12)
        index = out.joints[a]
        middle = out.joints[b]
        expected = ((index.x + middle.x) / 2.0, (index.y + middle.y) / 2.0)
        assert math.isclose(out.pinch_double[0], expected[0], abs_tol=0.5)
        assert math.isclose(out.pinch_double[1], expected[1], abs_tol=0.5)

    def test_three_pinch_points_differ(self):
        # Os 3 pinch points devem ser distintos pra uma mao normal
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        assert out.pinch_left != out.pinch_right
        assert out.pinch_left != out.pinch_double
        assert out.pinch_right != out.pinch_double


class TestPositioning:
    def test_pinch_left_lands_at_center(self):
        # O anchor da renderizacao e' o pinch midpoint (polegar+indicador).
        # Logo, pinch_left deve cair em (center_x, center_y).
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        assert math.isclose(out.pinch_left[0], 500.0, abs_tol=0.5)
        assert math.isclose(out.pinch_left[1], 300.0, abs_tol=0.5)

    def test_cursor_marker_at_center(self):
        # Cursor marker = pinch_left = (center_x, center_y)
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        assert math.isclose(out.cursor_marker[0], 500.0, abs_tol=0.5)
        assert math.isclose(out.cursor_marker[1], 300.0, abs_tol=0.5)

    def test_scales_to_fit_size(self):
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        xs = [j.x for j in out.joints]
        ys = [j.y for j in out.joints]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        assert max(width, height) <= 200.0 + 1.0


class TestTubePolygonHelper:
    def test_two_joints_makes_4_vertices(self):
        joints = [(0.0, 0.0), (10.0, 0.0)]
        poly = _tube_polygon(joints, base_width=4.0)
        # 2 joints x 2 lados = 4 vertices
        assert len(poly) == 4

    def test_three_joints_makes_6_vertices(self):
        joints = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
        poly = _tube_polygon(joints, base_width=4.0)
        assert len(poly) == 6

    def test_tip_narrower_than_base(self):
        # Tube horizontal de 4 joints
        joints = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
        poly = _tube_polygon(joints, base_width=10.0)
        # Primeiro vertice e' lado L do primeiro joint (base)
        # Ultimo de left e' o tip
        base_l = poly[0]
        tip_l = poly[3]
        base_width = abs(base_l[1])
        tip_width = abs(tip_l[1])
        assert tip_width < base_width

    def test_empty_joints_returns_empty(self):
        assert _tube_polygon([], 5.0) == ()
        assert _tube_polygon([(0, 0)], 5.0) == ()


class TestCursorMarker:
    def test_cursor_marker_at_pinch_anchor(self):
        # Cursor marker e' o pinch anchor (= polegar+indicador midpoint)
        out = compute_hand_primitives(_flat_hand(), 500, 300, 200)
        assert math.isclose(out.cursor_marker[0], 500.0, abs_tol=0.5)
        assert math.isclose(out.cursor_marker[1], 300.0, abs_tol=0.5)
        # E deve coincidir com pinch_left
        assert out.cursor_marker == out.pinch_left


class TestParticleCloud:
    def test_default_count(self):
        cloud = generate_particle_cloud()
        assert len(cloud) == 180

    def test_custom_count(self):
        cloud = generate_particle_cloud(count=50)
        assert len(cloud) == 50

    def test_zero_count_empty(self):
        assert generate_particle_cloud(count=0) == ()

    def test_negative_count_empty(self):
        assert generate_particle_cloud(count=-1) == ()

    def test_deterministic_same_seed(self):
        a = generate_particle_cloud(count=100, seed=7)
        b = generate_particle_cloud(count=100, seed=7)
        assert a == b

    def test_different_seed_different(self):
        a = generate_particle_cloud(count=100, seed=1)
        b = generate_particle_cloud(count=100, seed=2)
        assert a != b

    def test_host_idx_in_valid_range(self):
        cloud = generate_particle_cloud(count=300)
        for p in cloud:
            assert 0 <= p.host_idx <= 20

    def test_alpha_in_valid_range(self):
        cloud = generate_particle_cloud(count=200)
        for p in cloud:
            assert 0.0 <= p.base_alpha <= 1.0

    def test_phase_in_valid_range(self):
        cloud = generate_particle_cloud(count=200)
        for p in cloud:
            assert 0.0 <= p.phase <= 6.284

    def test_distribution_palm_majority(self):
        # Palm cluster = 50%, palm hosts = {0, 5, 9, 13, 17}
        cloud = generate_particle_cloud(count=1000)
        palm_hosts = {0, 5, 9, 13, 17}
        palm_count = sum(1 for p in cloud if p.host_idx in palm_hosts)
        # At least 40% in palm hosts (50% palm + some random from edge bloom)
        assert palm_count / len(cloud) >= 0.40

    def test_cache_returns_same_object(self):
        a = get_particle_cloud(count=100, seed=42)
        b = get_particle_cloud(count=100, seed=42)
        assert a is b  # cached

    def test_cache_invalidates_on_count(self):
        a = get_particle_cloud(count=100, seed=42)
        b = get_particle_cloud(count=200, seed=42)
        assert a is not b

    def test_cloud_particle_immutable(self):
        p = CloudParticle(host_idx=0, offset_x=0, offset_y=0,
                          phase=0, base_alpha=0.5)
        with pytest.raises(Exception):
            p.host_idx = 1  # type: ignore[misc]


class TestImmutability:
    def test_polygon_immutable(self):
        p = Polygon(points=((0, 0), (1, 1)), outline_width=2)
        with pytest.raises(Exception):
            p.outline_width = 99  # type: ignore[misc]

    def test_tip_immutable(self):
        t = Tip(x=0, y=0, core_radius=1, halo_radius=2)
        with pytest.raises(Exception):
            t.x = 99  # type: ignore[misc]

    def test_point_immutable(self):
        p = Point(x=0, y=0, radius=1)
        with pytest.raises(Exception):
            p.x = 99  # type: ignore[misc]

    def test_particle_immutable(self):
        # CloudParticle imutabilidade ja testada acima
        pass
