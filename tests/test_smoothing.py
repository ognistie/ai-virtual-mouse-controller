"""Testes unitarios para smoothing (EMA + OneEuroFilter)."""

from __future__ import annotations

import math
import time

import pytest

from core.smoothing import (
    EMASmoother,
    OneEuroSmoother2D,
    make_smoother,
)


# ---------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------

class TestEMASmoother:

    def test_first_sample_returns_input(self):
        s = EMASmoother(alpha=0.3)
        x, y = s(100.0, 200.0)
        assert x == pytest.approx(100.0)
        assert y == pytest.approx(200.0)

    def test_second_sample_blends_correctly(self):
        s = EMASmoother(alpha=0.5)
        s(0.0, 0.0)
        x, y = s(100.0, 100.0)
        assert x == pytest.approx(50.0)
        assert y == pytest.approx(50.0)

    def test_alpha_one_passes_through(self):
        s = EMASmoother(alpha=1.0)
        s(10.0, 10.0)
        x, y = s(50.0, 80.0)
        assert x == pytest.approx(50.0)
        assert y == pytest.approx(80.0)

    def test_low_alpha_strong_smoothing(self):
        s = EMASmoother(alpha=0.1)
        s(0.0, 0.0)
        x, y = s(100.0, 100.0)
        # Com alpha=0.1, primeira atualizacao vai a 10% do alvo
        assert x == pytest.approx(10.0)
        assert y == pytest.approx(10.0)

    def test_invalid_alpha_raises(self):
        with pytest.raises(ValueError):
            EMASmoother(alpha=0.0)
        with pytest.raises(ValueError):
            EMASmoother(alpha=1.5)
        with pytest.raises(ValueError):
            EMASmoother(alpha=-0.1)

    def test_reset_clears_state(self):
        s = EMASmoother(alpha=0.5)
        s(100.0, 100.0)
        s(200.0, 200.0)
        s.reset()
        x, y = s(50.0, 50.0)
        # Apos reset, primeira amostra retorna ela mesma
        assert x == pytest.approx(50.0)
        assert y == pytest.approx(50.0)

    def test_convergence_to_static_value(self):
        s = EMASmoother(alpha=0.5)
        target = (100.0, 200.0)
        for _ in range(50):
            x, y = s(*target)
        assert x == pytest.approx(target[0], abs=0.01)
        assert y == pytest.approx(target[1], abs=0.01)


# ---------------------------------------------------------------------
# OneEuroFilter
# ---------------------------------------------------------------------

class TestOneEuroSmoother2D:

    def test_first_sample_returns_input(self):
        s = OneEuroSmoother2D()
        x, y = s(100.0, 200.0)
        assert x == pytest.approx(100.0, abs=0.01)
        assert y == pytest.approx(200.0, abs=0.01)

    def test_static_signal_remains_stable(self):
        s = OneEuroSmoother2D(min_cutoff=1.0, beta=0.007)
        target_x, target_y = 50.0, 50.0
        last = (0.0, 0.0)
        for _ in range(30):
            last = s(target_x, target_y)
            time.sleep(0.005)
        # Apos varias amostras, deve estar muito proximo do alvo
        assert last[0] == pytest.approx(target_x, abs=1.0)
        assert last[1] == pytest.approx(target_y, abs=1.0)

    def test_smoothing_reduces_noise(self):
        """Sinal ruidoso ao redor de 100 deve ser suavizado."""
        s = OneEuroSmoother2D(min_cutoff=0.5, beta=0.001)
        outputs = []
        for i in range(60):
            # Sinal: 100 com ruido alternado +/-5
            noise = 5.0 if i % 2 == 0 else -5.0
            out = s(100.0 + noise, 100.0)
            outputs.append(out)
            time.sleep(0.005)

        # Variancia da saida deve ser menor que do input (10 de range)
        xs = [o[0] for o in outputs[10:]]  # ignora warmup
        max_dev = max(abs(x - 100.0) for x in xs)
        assert max_dev < 5.0  # filtrou pelo menos parte do ruido

    def test_reset_works(self):
        s = OneEuroSmoother2D()
        s(100.0, 100.0)
        s(200.0, 200.0)
        s.reset()
        x, y = s(0.0, 0.0)
        # Primeira amostra apos reset = passthrough
        assert x == pytest.approx(0.0, abs=0.01)
        assert y == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------

class TestMakeSmoother:

    def test_make_ema(self):
        s = make_smoother("ema", alpha=0.3)
        assert isinstance(s, EMASmoother)

    def test_make_one_euro(self):
        s = make_smoother("one_euro")
        assert isinstance(s, OneEuroSmoother2D)

    def test_make_one_euro_aliases(self):
        for name in ("one_euro", "OneEuro", "1euro", "ONE_EURO"):
            s = make_smoother(name)
            assert isinstance(s, OneEuroSmoother2D)

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            make_smoother("kalman")
