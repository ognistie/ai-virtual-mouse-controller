"""
core.smoothing
==============

Estrategias de suavizacao de coordenadas para o cursor virtual.

Disponiveis:
- EMASmoother:    Exponential Moving Average. Simples, determinista.
- OneEuroFilter:  Filtro adaptativo (Casiez et al., 2012). Padrao da
                  industria para tracking de cursor — suaviza muito quando
                  parado, pouco em movimento rapido. Reduz tremedeira sem
                  introduzir lag perceptivel.

Ambos implementam o protocolo Smoother (metodo `__call__` recebendo (x, y)
e retornando (x, y) suavizado, e metodo `reset`).

Para usar coordenadas 2D com OneEuroFilter, instanciamos um filtro por eixo
em OneEuroSmoother2D.
"""

from __future__ import annotations

import math
import time
from typing import Protocol, Tuple


# ---------------------------------------------------------------------
# Protocolo
# ---------------------------------------------------------------------

class Smoother(Protocol):
    """Interface comum para todas as estrategias de suavizacao."""

    def __call__(self, x: float, y: float) -> Tuple[float, float]:
        """Recebe coordenada bruta e retorna suavizada."""
        ...

    def reset(self) -> None:
        """Reinicia o estado interno (ex: quando mao sai do frame)."""
        ...


# ---------------------------------------------------------------------
# Exponential Moving Average
# ---------------------------------------------------------------------

class EMASmoother:
    """
    Exponential Moving Average.

    Formula: smoothed = alpha * raw + (1 - alpha) * previous

    Args:
        alpha: Fator de suavizacao (0-1).
            - 0.0 = nunca atualiza (travado)
            - 1.0 = sem suavizacao (passa direto)
            - 0.3-0.6 = bom range pratico

    Examples:
        >>> s = EMASmoother(alpha=0.5)
        >>> s(100, 100)
        (100.0, 100.0)
        >>> s(200, 200)
        (150.0, 150.0)
    """

    def __init__(self, alpha: float = 0.5) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError(f"alpha deve estar em (0, 1], got {alpha}")
        self.alpha = alpha
        self._prev_x: float | None = None
        self._prev_y: float | None = None

    def __call__(self, x: float, y: float) -> Tuple[float, float]:
        if self._prev_x is None:
            self._prev_x = float(x)
            self._prev_y = float(y)
            return self._prev_x, self._prev_y

        sx = self.alpha * x + (1.0 - self.alpha) * self._prev_x
        sy = self.alpha * y + (1.0 - self.alpha) * self._prev_y
        self._prev_x = sx
        self._prev_y = sy
        return sx, sy

    def reset(self) -> None:
        self._prev_x = None
        self._prev_y = None


# ---------------------------------------------------------------------
# OneEuroFilter (Casiez, Roussel, Vogel — 2012)
# ---------------------------------------------------------------------
# Referencia: https://gery.casiez.net/1euro/
# Aplicacao classica em tracking de cursor / interfaces gestuais.

def _smoothing_factor(t_e: float, cutoff: float) -> float:
    """Calcula alpha do low-pass dado timestep e cutoff frequency."""
    r = 2.0 * math.pi * cutoff * t_e
    return r / (r + 1.0)


def _exponential_smoothing(alpha: float, x: float, x_prev: float) -> float:
    return alpha * x + (1.0 - alpha) * x_prev


class _OneEuroAxis:
    """
    Filtro 1D OneEuro. Use OneEuroSmoother2D para X+Y.
    """

    def __init__(
        self,
        freq: float = 60.0,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ) -> None:
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev: float | None = None
        self._dx_prev: float = 0.0
        self._t_prev: float | None = None

    def __call__(self, x: float, t: float | None = None) -> float:
        if t is None:
            t = time.perf_counter()

        # Primeira amostra
        if self._x_prev is None or self._t_prev is None:
            self._x_prev = x
            self._t_prev = t
            self._dx_prev = 0.0
            return x

        t_e = t - self._t_prev
        if t_e <= 0:
            t_e = 1.0 / self.freq  # fallback se timestamps iguais

        # Suaviza a derivada
        a_d = _smoothing_factor(t_e, self.d_cutoff)
        dx = (x - self._x_prev) / t_e
        dx_hat = _exponential_smoothing(a_d, dx, self._dx_prev)

        # Cutoff adaptativo: maior em movimento rapido
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = _smoothing_factor(t_e, cutoff)
        x_hat = _exponential_smoothing(a, x, self._x_prev)

        self._x_prev = x_hat
        self._dx_prev = dx_hat
        self._t_prev = t

        return x_hat

    def reset(self) -> None:
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None


class OneEuroSmoother2D:
    """
    Wrapper 2D usando dois filtros OneEuro independentes (X e Y).

    Args:
        freq:       Taxa de amostragem esperada (Hz). Aproximacao serve.
        min_cutoff: Cutoff base. Menor = mais suave em repouso.
        beta:       Quanto a velocidade afeta o cutoff. Maior = menos lag.
        d_cutoff:   Cutoff da derivada (geralmente 1.0).

    Examples:
        >>> s = OneEuroSmoother2D(freq=60, min_cutoff=1.0, beta=0.007)
        >>> a, b = s(100.0, 100.0)
        >>> abs(a - 100.0) < 0.01 and abs(b - 100.0) < 0.01
        True
    """

    def __init__(
        self,
        freq: float = 60.0,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ) -> None:
        self._fx = _OneEuroAxis(freq, min_cutoff, beta, d_cutoff)
        self._fy = _OneEuroAxis(freq, min_cutoff, beta, d_cutoff)

    def __call__(self, x: float, y: float) -> Tuple[float, float]:
        t = time.perf_counter()
        return self._fx(x, t), self._fy(y, t)

    def reset(self) -> None:
        self._fx.reset()
        self._fy.reset()


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------

def make_smoother(
    strategy: str,
    *,
    alpha: float = 0.5,
    freq: float = 60.0,
    min_cutoff: float = 1.0,
    beta: float = 0.007,
    d_cutoff: float = 1.0,
) -> Smoother:
    """
    Factory para instanciar a estrategia correta a partir de string.

    Args:
        strategy: "ema" ou "one_euro".

    Returns:
        Instancia que satisfaz o protocolo Smoother.
    """
    s = strategy.lower().strip()
    if s == "ema":
        return EMASmoother(alpha=alpha)
    if s in ("one_euro", "oneeuro", "1euro"):
        return OneEuroSmoother2D(
            freq=freq,
            min_cutoff=min_cutoff,
            beta=beta,
            d_cutoff=d_cutoff,
        )
    raise ValueError(f"Estrategia desconhecida: {strategy!r}")
