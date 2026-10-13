"""Mao sintetica (21 landmarks MediaPipe) pra testes e pro harness
scripts/eval_motion.py — sem webcam, deterministica com seed.

Geometria em unidades de "tamanho da mao" (pulso -> MCP do medio = 1),
escalada por HAND_SIZE (= HAND_SIZE_REFERENCE do config, dpi adaptativo
neutro).
"""

from __future__ import annotations

import random
from typing import Dict, Optional, Tuple

from core.hand_tracker import HandLandmarks

HAND_SIZE = 0.22

# Ruido de landmark: independente por ponto + modo comum (mao inteira).
# Ordem de grandeza do jitter do MediaPipe numa webcam 720p comum.
NOISE_POINT = 0.0015
NOISE_COMMON = 0.0008

# fmt: off
OPEN_HAND: Tuple[Tuple[float, float], ...] = (
    (0.00, 0.00),                                                    # 0 pulso
    (-0.25, -0.15), (-0.45, -0.30), (-0.60, -0.45), (-0.72, -0.58),  # polegar
    (-0.22, -0.95), (-0.25, -1.35), (-0.27, -1.60), (-0.28, -1.82),  # indicador
    (0.00, -1.00), (0.00, -1.45), (0.00, -1.72), (0.00, -1.95),      # medio
    (0.20, -0.95), (0.23, -1.35), (0.25, -1.60), (0.27, -1.80),      # anelar
    (0.38, -0.85), (0.45, -1.15), (0.50, -1.35), (0.53, -1.52),      # minimo
)
# fmt: on
# Pinca fechada: polegar encontra o indicador curvado.
PINCH_TARGET: Dict[int, Tuple[float, float]] = {
    3: (-0.52, -1.10),
    4: (-0.40, -1.42),
    7: (-0.36, -1.52),
    8: (-0.40, -1.47),
}
# Ancora aproximada relativa ao pulso — usada pra posicionar a mao.
ANCHOR_OFFSET = (-0.02, -1.05)


def make_hand(
    cx: float,
    cy: float,
    pinch: float = 0.0,
    rng: Optional[random.Random] = None,
    *,
    noise: bool = True,
    size: Optional[float] = None,
) -> HandLandmarks:
    """Mao aberta com a ancora aproximada em (cx, cy).

    pinch: 0 = aberta, 1 = pinca totalmente fechada (interpola).
    rng: None = sem ruido.
    size: tamanho da mao no frame (pulso -> MCP do medio). Muda com a
        distancia da camera e com a mao girando/inclinando.
    """
    hs = HAND_SIZE if size is None else size
    wx = cx - ANCHOR_OFFSET[0] * hs
    wy = cy - ANCHOR_OFFSET[1] * hs
    use_noise = noise and rng is not None
    cmx = rng.gauss(0.0, NOISE_COMMON) if use_noise else 0.0
    cmy = rng.gauss(0.0, NOISE_COMMON) if use_noise else 0.0
    pts = []
    for i, (ox, oy) in enumerate(OPEN_HAND):
        if i in PINCH_TARGET and pinch > 0.0:
            tx, ty = PINCH_TARGET[i]
            ox = ox + (tx - ox) * pinch
            oy = oy + (ty - oy) * pinch
        x = wx + ox * hs + cmx
        y = wy + oy * hs + cmy
        if use_noise:
            x += rng.gauss(0.0, NOISE_POINT)
            y += rng.gauss(0.0, NOISE_POINT)
        pts.append((x, y, 0.0))
    return HandLandmarks(landmarks=tuple(pts), handedness="Right", score=0.95)


def min_jerk(t: float) -> float:
    """Perfil minimum-jerk (movimento humano de alcance), t em [0, 1]."""
    t = max(0.0, min(1.0, t))
    return 10 * t**3 - 15 * t**4 + 6 * t**5


class FakeClock:
    """Relogio controlado; substitui o modulo `time` nos modulos testados."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def perf_counter(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(0.0, s)

    def advance(self, s: float) -> None:
        self.t += s
