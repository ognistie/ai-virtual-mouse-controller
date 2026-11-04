"""
core.hand_arbiter
=================

Modo duas maos: separa as maos detectadas em dois canais estaveis e decide
qual delas e' a dona do cursor do sistema (o Windows tem um cursor so).

- Identidade por proximidade, nao pela ordem da lista do MediaPipe (que
  muda entre frames) nem pelo rotulo Left/Right (que oscila). O rotulo so
  escolhe o canal quando a mao aparece.
- A outra mao vira dona quando a atual some ou quando ela se move de
  verdade (por um tempo minimo) com a atual parada.
- Nunca troca no meio de um gesto (`locked`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .hand_tracker import LM_WRIST, HandLandmarks

LM_MIDDLE_MCP = 9

LEFT = "left"
RIGHT = "right"

Point = Tuple[float, float]


def _palm(hand: HandLandmarks) -> Point:
    lm = hand.landmarks[LM_MIDDLE_MCP]
    return lm[0], lm[1]


def _size(hand: HandLandmarks) -> float:
    a, b = hand.landmarks[LM_WRIST], hand.landmarks[LM_MIDDLE_MCP]
    return max(1e-3, math.hypot(a[0] - b[0], a[1] - b[1]))


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _preferred(hand: HandLandmarks) -> str:
    return LEFT if hand.handedness.lower().startswith("l") else RIGHT


@dataclass(frozen=True)
class HandAssignment:
    left: Optional[HandLandmarks]
    right: Optional[HandLandmarks]
    owner: Optional[str]


class _Slot:
    """Estado de um canal entre frames."""

    __slots__ = ("fast_since", "hand", "pos", "seen_t", "speed")

    def __init__(self) -> None:
        self.free()

    def free(self) -> None:
        self.pos: Optional[Point] = None
        self.speed = 0.0
        self.seen_t = 0.0
        self.fast_since: Optional[float] = None
        self.hand: Optional[HandLandmarks] = None


class HandArbiter:
    """Velocidades em tamanhos de mao por segundo: independem da distancia
    da camera."""

    # Duas deteccoes mais proximas que isso (em tamanhos de mao) sao a
    # mesma mao: o MediaPipe as vezes duplica uma mao com num_hands=2.
    _DUPLICATE_DIST = 0.6
    # Salto maximo (tamanhos de mao) pra considerar a mesma mao entre frames.
    _MAX_JUMP = 2.5
    _SPEED_TAU_S = 0.08

    def __init__(
        self,
        *,
        takeover_speed: float = 1.5,
        takeover_hold_s: float = 0.15,
        lost_grace_s: float = 0.2,
        cooldown_s: float = 0.4,
    ) -> None:
        self.takeover_speed = takeover_speed
        self.takeover_hold_s = takeover_hold_s
        self.lost_grace_s = lost_grace_s
        self.cooldown_s = cooldown_s
        self._slots: Dict[str, _Slot] = {LEFT: _Slot(), RIGHT: _Slot()}
        self.reset()

    def reset(self) -> None:
        for slot in self._slots.values():
            slot.free()
        self._owner: Optional[str] = None
        self._last_t: Optional[float] = None
        self._switch_t = -math.inf

    def assign(
        self,
        hands: Sequence[HandLandmarks],
        now: float,
        *,
        locked: bool = False,
    ) -> HandAssignment:
        """`locked`: a dona esta no meio de um gesto (nao perde o cursor,
        nem se sumir: o detector ainda vai encerrar o gesto dela)."""
        dt = 0.0 if self._last_t is None else max(0.0, now - self._last_t)
        self._last_t = now
        for slot in self._slots.values():
            slot.hand = None

        pending = self._match_tracked(self._unique(hands), dt, now)
        self._release_lost(now, locked)
        self._place_new(pending, now)
        self._update_owner(now, locked)
        return HandAssignment(
            left=self._slots[LEFT].hand,
            right=self._slots[RIGHT].hand,
            owner=self._owner,
        )

    def set_owner(self, name: str, now: float) -> None:
        """Passa o cursor pra uma mao que iniciou um arrasto."""
        self._owner = name
        self._switch_t = now
        for slot in self._slots.values():
            slot.fast_since = None

    def _unique(self, hands: Sequence[HandLandmarks]) -> List[HandLandmarks]:
        out: List[HandLandmarks] = []
        for hand in sorted(hands, key=lambda h: -h.score):
            limit = self._DUPLICATE_DIST * _size(hand)
            if all(_dist(_palm(hand), _palm(kept)) > limit for kept in out):
                out.append(hand)
        return out

    def _match_tracked(
        self, hands: List[HandLandmarks], dt: float, now: float,
    ) -> List[HandLandmarks]:
        """Liga cada canal ativo a mao mais proxima; devolve as que sobram."""
        pairs = []
        for i, hand in enumerate(hands):
            for name, slot in self._slots.items():
                if slot.pos is None:
                    continue
                d = _dist(_palm(hand), slot.pos)
                if d <= self._MAX_JUMP * _size(hand):
                    pairs.append((d, i, name))
        used = set()
        for _, i, name in sorted(pairs):
            slot = self._slots[name]
            if i in used or slot.hand is not None:
                continue
            used.add(i)
            self._track(slot, hands[i], dt, now)
        return [hand for i, hand in enumerate(hands) if i not in used]

    def _track(
        self, slot: _Slot, hand: HandLandmarks, dt: float, now: float,
    ) -> None:
        pos = _palm(hand)
        if dt > 0.0 and slot.pos is not None:
            sample = _dist(pos, slot.pos) / _size(hand) / dt
            alpha = 1.0 - math.exp(-dt / self._SPEED_TAU_S)
            slot.speed += alpha * (sample - slot.speed)
        slot.pos = pos
        slot.seen_t = now
        slot.hand = hand
        if slot.speed < self.takeover_speed:
            slot.fast_since = None
        elif slot.fast_since is None:
            slot.fast_since = now

    def _release_lost(self, now: float, locked: bool) -> None:
        for name, slot in self._slots.items():
            if slot.pos is None or slot.hand is not None:
                continue
            if now - slot.seen_t <= self.lost_grace_s:
                continue  # piscada: canal reservado, a mao costuma voltar
            if locked and name == self._owner:
                continue
            slot.free()

    def _place_new(self, hands: List[HandLandmarks], now: float) -> None:
        free = [name for name, slot in self._slots.items() if slot.pos is None]
        if len(hands) >= 2 and len(free) == 2 and (
            _preferred(hands[0]) == _preferred(hands[1])
        ):
            # Mesmo rotulo: decide pela posicao. No quadro espelhado a mao
            # esquerda do usuario aparece a esquerda da imagem.
            first, second = sorted(hands[:2], key=lambda h: _palm(h)[0])
            self._track(self._slots[LEFT], first, 0.0, now)
            self._track(self._slots[RIGHT], second, 0.0, now)
            return
        for hand in hands:
            name = _preferred(hand)
            if self._slots[name].pos is not None:
                name = LEFT if name == RIGHT else RIGHT
            if self._slots[name].pos is None:
                self._track(self._slots[name], hand, 0.0, now)

    def _update_owner(self, now: float, locked: bool) -> None:
        owner = self._slots[self._owner] if self._owner else None
        if owner is None or owner.pos is None:
            seen = [
                (s.hand.score, n) for n, s in self._slots.items()
                if s.hand is not None
            ]
            if not seen:
                self._owner = None
                return
            self._owner = max(seen)[1]
            self._switch_t = now
            return
        name = LEFT if self._owner == RIGHT else RIGHT
        other = self._slots[name]
        if (
            not locked
            and owner.hand is not None
            and other.hand is not None
            and other.fast_since is not None
            and now - other.fast_since >= self.takeover_hold_s
            and now - self._switch_t >= self.cooldown_s
            and owner.speed < other.speed * 0.5
        ):
            self.set_owner(name, now)
