"""
core.hand_gate
==============

Filtra o que o MediaPipe acha que e' mao, antes de virar cursor ou clique:
- FANTASMA DENTRO DA MAO: com num_hands=2, surge uma "segunda mao" pequena
  sobre os dedos dobrados da real, com confianca alta. A mao contida em outra
  e' descartada.
- PISCADA: deteccao que dura poucos frames nao chega a agir: a mao precisa
  persistir `confirm_frames` pra valer. Mao ja confirmada que some por poucos
  frames volta na hora.
- RASTREAMENTO PRESO: com uma mao so, enquanto o MediaPipe segue algo ele nao
  procura mais nada; se grudou em algo parado (ate o rosto), a mao real volta e
  nao e' vista. `StaleTrackWatchdog` manda procurar de novo quando o que esta
  sendo seguido nao se mexe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .hand_tracker import LM_WRIST, HandLandmarks

LM_MIDDLE_MCP = 9

Box = Tuple[float, float, float, float]  # x0, y0, x1, y1 normalizados


class StaleTrackWatchdog:
    """Avisa quando a "mao" seguida ficou parada tempo demais.

    Uma mao de verdade parada so perde 1 ou 2 frames com a nova busca, que o
    detector ja absorve. Nao age no meio de um gesto (`busy`).
    """

    _MAX_GAP_S = 0.5

    def __init__(self, still_s: float = 1.0, still_dist: float = 0.05) -> None:
        self.still_s = float(still_s)
        self.still_dist = float(still_dist)  # fracao da imagem
        self._anchor: Optional[Tuple[float, float]] = None
        self._since = 0.0
        self._seen = 0.0

    def reset(self) -> None:
        self._anchor = None

    def update(self, hands: Sequence[HandLandmarks], busy: bool, now: float) -> bool:
        """True = recriar o rastreamento agora."""
        if busy:
            self._anchor = None
            return False
        if not hands:
            # Um falso pisca: falha curta nao zera a contagem.
            if now - self._seen > self._MAX_GAP_S:
                self._anchor = None
            return False
        self._seen = now
        hand = hands[0]
        # So a palma: os pontos de um falso tremem bastante mesmo sem sair do
        # lugar, e a mao real que navega desloca a palma.
        palm = _palm(hand)
        if self._anchor is None or (
            math.hypot(palm[0] - self._anchor[0], palm[1] - self._anchor[1])
            > self.still_dist
        ):
            self._anchor = palm
            self._since = now
            return False
        if now - self._since < self.still_s:
            return False
        self._anchor = None
        return True


def _palm(hand: HandLandmarks) -> Tuple[float, float]:
    lm = hand.landmarks[LM_MIDDLE_MCP]
    return lm[0], lm[1]


def _size(hand: HandLandmarks) -> float:
    a, b = hand.landmarks[LM_WRIST], hand.landmarks[LM_MIDDLE_MCP]
    return max(1e-3, math.hypot(a[0] - b[0], a[1] - b[1]))


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
        return self._confirmed(self._drop_nested(hands))

    def _drop_nested(self, hands: Sequence[HandLandmarks]) -> List[HandLandmarks]:
        if len(hands) < 2:
            return list(hands)
        boxes = [_bbox(h) for h in hands]
        kept = []
        for i, hand in enumerate(hands):
            nested = any(
                j != i
                and _size(other) >= _size(hand)
                and _inside_fraction(boxes[i], boxes[j]) >= self._NESTED_FRACTION
                for j, other in enumerate(hands)
            )
            if not nested:
                kept.append(hand)
        return kept

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
