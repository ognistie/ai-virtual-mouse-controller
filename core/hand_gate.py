"""
core.hand_gate
==============

Filtra o que o MediaPipe acha que e' mao, antes de virar cursor ou clique:
- MAO REPETIDA: com num_hands=2, a mesma mao pode vir duas vezes, ou surgir
  uma "segunda mao" pequena sobre os dedos dobrados da real, com confianca
  alta. Deteccao colada em outra ou contida nela e' descartada.
- PISCADA: deteccao que dura poucos frames nao chega a agir: a mao precisa
  persistir `confirm_frames` pra valer. Mao ja confirmada que some por poucos
  frames volta na hora.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .hand_tracker import HandLandmarks
from .hand_tracker import palm_point as _palm
from .hand_tracker import palm_size as _size

Box = Tuple[float, float, float, float]  # x0, y0, x1, y1 normalizados


def _bbox(hand: HandLandmarks) -> Box:
    xs = [p[0] for p in hand.landmarks]
    ys = [p[1] for p in hand.landmarks]
    return min(xs), min(ys), max(xs), max(ys)


def _inside_fraction(inner: Box, outer: Box) -> float:
    """Fracao da area de `inner` que esta dentro de `outer`."""
    w = min(inner[2], outer[2]) - max(inner[0], outer[0])
    h = min(inner[3], outer[3]) - max(inner[1], outer[1])
    if w <= 0.0 or h <= 0.0:
        return 0.0
    area = max(1e-9, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return w * h / area


@dataclass
class _Track:
    pos: Tuple[float, float]
    frames: int = 1
    missed: int = 0
    seen: bool = True


class HandGate:
    """So deixa passar mao que nao e' fantasma e que persiste."""

    # Palma mais perto que isso (em tamanhos de mao) = mesma mao.
    _DUPLICATE_DIST = 0.6
    # Mao contida em outra (fracao da area) = fantasma.
    _NESTED_FRACTION = 0.6
    _MAX_JUMP = 2.5  # tamanhos de mao entre frames pra ser a mesma mao
    # Frames sem ver a mao antes de exigir nova confirmacao (piscada do
    # MediaPipe nao atrasa a volta).
    _MAX_MISSED = 5

    def __init__(self, confirm_frames: int = 3) -> None:
        self.confirm_frames = max(1, int(confirm_frames))
        self._tracks: List[_Track] = []

    def reset(self) -> None:
        self._tracks = []

    def filter(self, hands: Sequence[HandLandmarks]) -> List[HandLandmarks]:
        """Maos validas, na mesma ordem da entrada."""
        return self._confirmed(self._drop_repeated(hands))

    def _drop_repeated(self, hands: Sequence[HandLandmarks]) -> List[HandLandmarks]:
        if len(hands) < 2:
            return list(hands)
        # Maior primeiro (fantasma e' menor, mesmo com confianca alta);
        # no empate, a mais confiavel.
        ordered = sorted(hands, key=lambda h: (-round(_size(h), 3), -h.score))
        kept: List[HandLandmarks] = []
        for hand in ordered:
            if not any(self._repeats(hand, other) for other in kept):
                kept.append(hand)
        return [h for h in hands if any(h is k for k in kept)]

    def _repeats(self, a: HandLandmarks, b: HandLandmarks) -> bool:
        pa, pb = _palm(a), _palm(b)
        limit = self._DUPLICATE_DIST * max(_size(a), _size(b))
        if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) <= limit:
            return True
        small, big = (a, b) if _size(a) <= _size(b) else (b, a)
        return _inside_fraction(_bbox(small), _bbox(big)) >= self._NESTED_FRACTION

    def _confirmed(self, hands: List[HandLandmarks]) -> List[HandLandmarks]:
        for existing in self._tracks:
            existing.seen = False
        out = []
        for hand in hands:
            track = self._match(hand)
            if track is None:
                track = _Track(pos=_palm(hand))
                self._tracks.append(track)
            else:
                track.pos = _palm(hand)
                track.frames += 1
                track.missed = 0
                track.seen = True
            if track.frames >= self.confirm_frames:
                out.append(hand)
        for existing in self._tracks:
            if not existing.seen:
                existing.missed += 1
        self._tracks = [t for t in self._tracks if t.missed <= self._MAX_MISSED]
        return out

    def _match(self, hand: HandLandmarks) -> Optional[_Track]:
        pos = _palm(hand)
        limit = self._MAX_JUMP * _size(hand)
        best, best_d = None, math.inf
        for track in self._tracks:
            if track.seen:
                continue
            d = math.hypot(pos[0] - track.pos[0], pos[1] - track.pos[1])
            if d <= limit and d < best_d:
                best, best_d = track, d
        return best
