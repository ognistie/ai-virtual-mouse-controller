"""Testes do perf_telemetry.

Foca em comportamento puro: timing, window roll, enable/disable, report.
Sem GUI, sem mediapipe — funcao pura testavel.
"""

from __future__ import annotations

import logging
import time

import pytest

from core.perf_telemetry import (
    TickProfiler,
    configure,
    get_profiler,
)


class TestConstructor:
    def test_default(self):
        p = TickProfiler()
        assert p.enabled is True
        assert p.tick_count == 0

    def test_invalid_window_size(self):
        with pytest.raises(ValueError):
            TickProfiler(window_size=0)

    def test_invalid_report_every(self):
        with pytest.raises(ValueError):
            TickProfiler(report_every=0)


class TestStageTiming:
    def test_stage_records_sample(self):
        p = TickProfiler(window_size=5, report_every=999)
        with p.stage("foo"):
            pass
        snap = p.snapshot()
        assert "foo" in snap
        assert snap["foo"]["n"] == 1.0

    def test_stage_measures_time(self):
        p = TickProfiler(window_size=5, report_every=999)
        with p.stage("sleep"):
            time.sleep(0.01)
        snap = p.snapshot()
        # 10ms sleep — p50 deve estar perto disso
        assert snap["sleep"]["p50"] >= 8.0
        assert snap["sleep"]["p50"] <= 50.0  # generoso pra CI lento

    def test_multiple_stages_separated(self):
        p = TickProfiler(window_size=10, report_every=999)
        with p.stage("a"):
            pass
        with p.stage("b"):
            pass
        snap = p.snapshot()
        assert "a" in snap
        assert "b" in snap

    def test_window_rolls_over(self):
        p = TickProfiler(window_size=3, report_every=999)
        for _ in range(10):
            with p.stage("x"):
                pass
        snap = p.snapshot()
        assert snap["x"]["n"] == 3.0  # caped at window


class TestEnableDisable:
    def test_disabled_stage_no_record(self):
        p = TickProfiler(window_size=5, report_every=999)
        p.set_enabled(False)
        with p.stage("x"):
            time.sleep(0.005)
        assert p.snapshot() == {}

    def test_disabled_tick_no_count(self):
        p = TickProfiler(report_every=999)
        p.set_enabled(False)
        for _ in range(10):
            p.tick()
        assert p.tick_count == 0

    def test_can_reenable(self):
        p = TickProfiler(window_size=5, report_every=999)
        p.set_enabled(False)
        with p.stage("x"):
            pass
        p.set_enabled(True)
        with p.stage("x"):
            pass
        assert p.snapshot()["x"]["n"] == 1.0


class TestTickReport:
    def test_tick_increments(self):
        p = TickProfiler(report_every=999)
        p.tick()
        p.tick()
        assert p.tick_count == 2

    def test_report_logs_at_interval(self, caplog):
        p = TickProfiler(window_size=5, report_every=3)
        with p.stage("a"):
            pass
        with caplog.at_level(logging.INFO, logger="core.perf_telemetry"):
            p.tick()
            p.tick()
            # nada ainda
            assert "PERF" not in caplog.text
            p.tick()  # triggers
            assert "PERF" in caplog.text


class TestReset:
    def test_reset_clears(self):
        p = TickProfiler(window_size=5, report_every=999)
        with p.stage("a"):
            pass
        p.tick()
        p.reset()
        assert p.snapshot() == {}
        assert p.tick_count == 0


class TestSnapshot:
    def test_empty_snapshot(self):
        p = TickProfiler()
        assert p.snapshot() == {}

    def test_p50_p99_present(self):
        p = TickProfiler(window_size=10, report_every=999)
        for _ in range(10):
            with p.stage("x"):
                pass
        snap = p.snapshot()
        assert "p50" in snap["x"]
        assert "p99" in snap["x"]


class TestGlobalSingleton:
    def test_get_profiler_returns_same(self):
        p1 = get_profiler()
        p2 = get_profiler()
        assert p1 is p2

    def test_configure_replaces(self):
        p1 = get_profiler()
        p2 = configure(window_size=50, report_every=50, enabled=False)
        assert p1 is not p2
        assert get_profiler() is p2
        assert p2.enabled is False
