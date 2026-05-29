"""Testes do sistema de click bursts.

Sem GUI. Manager e' puro: time.perf_counter + listas. Manipula tempo
controladamente via parametro 'now' nos metodos publicos.
"""

from __future__ import annotations

import time

import pytest

from core.click_burst import (
    KIND_CLICK,
    KIND_DOUBLE_CLICK,
    KIND_DRAG_END,
    KIND_DRAG_START,
    KIND_RIGHT_CLICK,
    BurstManager,
    BurstSnapshot,
)


class TestManagerBasics:
    def test_starts_empty(self):
        bm = BurstManager()
        assert bm.count() == 0
        assert bm.snapshots() == ()

    def test_fire_adds_burst(self):
        bm = BurstManager()
        bm.fire(100, 200, KIND_CLICK)
        assert bm.count() == 1

    def test_fire_multiple_appends(self):
        bm = BurstManager()
        bm.fire(0, 0, KIND_CLICK)
        bm.fire(100, 100, KIND_RIGHT_CLICK)
        bm.fire(200, 200, KIND_DOUBLE_CLICK)
        assert bm.count() == 3

    def test_clear_removes_all(self):
        bm = BurstManager()
        bm.fire(0, 0)
        bm.fire(0, 0)
        bm.clear()
        assert bm.count() == 0


class TestLifecycle:
    def test_cleanup_removes_expired(self):
        bm = BurstManager()
        bm.fire(0, 0, KIND_CLICK)
        # Mock now to 100 seconds later — burst should be expired
        future = time.perf_counter() + 100
        removed = bm.cleanup(now=future)
        assert removed == 1
        assert bm.count() == 0

    def test_cleanup_keeps_alive(self):
        bm = BurstManager()
        bm.fire(0, 0, KIND_CLICK)
        # Cleanup imediato — burst ainda vivo
        bm.cleanup()
        assert bm.count() == 1

    def test_snapshots_excludes_expired(self):
        bm = BurstManager()
        bm.fire(0, 0, KIND_CLICK)
        future = time.perf_counter() + 100
        snaps = bm.snapshots(now=future)
        assert snaps == ()


class TestSnapshotMechanics:
    def test_snapshot_has_position(self):
        bm = BurstManager()
        bm.fire(123, 456, KIND_CLICK)
        snaps = bm.snapshots()
        assert len(snaps) == 1
        assert snaps[0].x == 123
        assert snaps[0].y == 456

    def test_snapshot_is_immutable(self):
        s = BurstSnapshot(x=0, y=0, radius=10, alpha=0.5, width=2, color_kind="red")
        with pytest.raises(Exception):
            s.x = 99  # type: ignore[misc]

    def test_alpha_starts_full_decreases(self):
        bm = BurstManager()
        bm.fire(100, 100, KIND_CLICK, duration=1.0)
        snaps0 = bm.snapshots(now=time.perf_counter())
        a0 = snaps0[0].alpha
        # 0.5s depois (metade da duracao)
        future = time.perf_counter() + 0.5
        snaps_mid = bm.snapshots(now=future)
        a_mid = snaps_mid[0].alpha
        assert a0 > a_mid
        assert a_mid > 0

    def test_radius_grows(self):
        bm = BurstManager()
        bm.fire(100, 100, KIND_CLICK, duration=1.0, max_radius=100)
        snaps0 = bm.snapshots(now=time.perf_counter())
        r0 = snaps0[0].radius
        future = time.perf_counter() + 0.5
        snaps_mid = bm.snapshots(now=future)
        r_mid = snaps_mid[0].radius
        assert r_mid > r0


class TestColorByKind:
    def test_click_is_red(self):
        bm = BurstManager()
        bm.fire(0, 0, KIND_CLICK)
        assert bm.snapshots()[0].color_kind == "red"

    def test_right_click_is_cyan(self):
        bm = BurstManager()
        bm.fire(0, 0, KIND_RIGHT_CLICK)
        assert bm.snapshots()[0].color_kind == "cyan"

    def test_double_is_red(self):
        bm = BurstManager()
        bm.fire(0, 0, KIND_DOUBLE_CLICK)
        assert bm.snapshots()[0].color_kind == "red"


class TestDoubleClick:
    def test_secondary_zero_at_start(self):
        bm = BurstManager()
        bm.fire(100, 100, KIND_DOUBLE_CLICK, duration=1.0)
        snap = bm.snapshots(now=time.perf_counter())[0]
        # Secondary so comeca depois de p>=0.35
        assert snap.secondary_radius == 0.0

    def test_secondary_active_in_middle(self):
        bm = BurstManager()
        bm.fire(100, 100, KIND_DOUBLE_CLICK, duration=1.0, max_radius=100)
        # 60% da duracao — secondary deve ter raio > 0
        future = time.perf_counter() + 0.6
        snap = bm.snapshots(now=future)[0]
        assert snap.secondary_radius > 0
        assert snap.secondary_alpha > 0


class TestDragKinds:
    def test_drag_end_contracts(self):
        bm = BurstManager()
        bm.fire(100, 100, KIND_DRAG_END, duration=1.0, max_radius=60)
        snap0 = bm.snapshots(now=time.perf_counter())[0]
        future = time.perf_counter() + 0.7
        snap_late = bm.snapshots(now=future)[0]
        # Drag end vai do max pra 0 (contrai)
        assert snap0.radius > snap_late.radius

    def test_drag_start_radius_settles_at_max(self):
        bm = BurstManager()
        bm.fire(100, 100, KIND_DRAG_START, duration=1.0, max_radius=80)
        # Apos a fase de "grow" (40%), o raio deve estar em max
        future = time.perf_counter() + 0.5
        snap = bm.snapshots(now=future)[0]
        assert abs(snap.radius - 80) < 1.0
