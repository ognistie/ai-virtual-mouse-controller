"""
core.utils
==========

Funcoes utilitarias puras (sem efeitos colaterais).

- Calculo de distancia euclidiana
- Mapeamento de range
- Clamp
- Calculo de FPS
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Tuple


# ---------------------------------------------------------------------
# Geometria
# ---------------------------------------------------------------------

def euclidean_distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """
    Distancia euclidiana entre dois pontos 2D.

    Args:
        p1: Tupla (x, y) do primeiro ponto.
        p2: Tupla (x, y) do segundo ponto.

    Returns:
        Distancia escalar.

    Examples:
        >>> euclidean_distance((0, 0), (3, 4))
        5.0
    """
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    return math.sqrt(dx * dx + dy * dy)


def map_range(
    value: float,
    in_min: float,
    in_max: float,
    out_min: float,
    out_max: float,
) -> float:
    """
    Mapeia 'value' do intervalo [in_min, in_max] para [out_min, out_max].

    Examples:
        >>> map_range(0.5, 0.0, 1.0, 0, 100)
        50.0
        >>> map_range(0.0, 0.1, 0.9, 0, 1920)
        -240.0
    """
    in_range = in_max - in_min
    if in_range == 0:
        return out_min
    proportion = (value - in_min) / in_range
    return out_min + proportion * (out_max - out_min)


def clamp(value: float, low: float, high: float) -> float:
    """
    Limita 'value' ao intervalo [low, high].

    Examples:
        >>> clamp(5, 0, 10)
        5
        >>> clamp(15, 0, 10)
        10
        >>> clamp(-3, 0, 10)
        0
    """
    if value < low:
        return low
    if value > high:
        return high
    return value


# ---------------------------------------------------------------------
# FPS
# ---------------------------------------------------------------------

class FPSCounter:
    """
    Contador de FPS via janela deslizante.

    Mantem N timestamps recentes e calcula FPS instantaneo.
    Mais estavel que usar so a diferenca do ultimo frame.

    Examples:
        >>> fps = FPSCounter(window=30)
        >>> for _ in range(30):
        ...     fps.tick()
        >>> isinstance(fps.value, float)
        True
    """

    def __init__(self, window: int = 30) -> None:
        self._timestamps: deque[float] = deque(maxlen=window)
        self._window = window

    def tick(self) -> None:
        """Marca um novo frame."""
        self._timestamps.append(time.perf_counter())

    @property
    def value(self) -> float:
        """FPS atual baseado na janela. Retorna 0 se insuficientes amostras."""
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0:
            return 0.0
        return (len(self._timestamps) - 1) / elapsed

    def reset(self) -> None:
        """Limpa o historico."""
        self._timestamps.clear()
