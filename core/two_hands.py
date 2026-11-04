"""
core.two_hands
==============

Modo duas maos: cada mao tem o proprio canal (detector, suavizacao e
ponteiro) e executa os mesmos gestos de mouse.

O sistema tem UM cursor e UM botao esquerdo, entao:
- so a mao dona move o cursor real (ver core.hand_arbiter);
- a outra clica no proprio ponteiro e o cursor volta pra dona no mesmo tick;
- acoes do mesmo frame saem em sequencia, cada uma na sua posicao;
- so uma mao arrasta por vez: quem arrasta vira dona, e as acoes da outra
  ficam ignoradas ate o arrasto terminar.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from .cursor_controller import CursorController
from .gesture_detector import Gesture, GestureDetector, GestureEvent
from .hand_arbiter import LEFT, RIGHT, HandArbiter
from .hand_tracker import HandLandmarks

logger = logging.getLogger(__name__)

# Mesma janela do fluxo de uma mao: mao sumida ha mais que isso zera o filtro.
_SMOOTHER_RESET_FRAMES = 5

Pose = Tuple[Sequence[Tuple[float, float, float]], float, float]


@dataclass
class HandChannel:
    name: str
    detector: GestureDetector
    smoother: Any
    cursor: CursorController
    hand: Optional[HandLandmarks] = None
    lost_frames: int = 0

    def clear(self) -> None:
        self.cursor.force_release()
        self.detector.reset()
        self.smoother.reset()
        self.hand = None
        self.lost_frames = 0


class TwoHandsController:
    """Um canal por mao + a regra de quem manda no cursor."""

    def __init__(
        self,
        right: HandChannel,
        left: HandChannel,
        arbiter: Optional[HandArbiter] = None,
    ) -> None:
        self.right = right
        self.left = left
        self._arbiter = arbiter or HandArbiter()
        self._owner_name = RIGHT
        self.reset()

    @property
    def owner(self) -> HandChannel:
        return self.left if self._owner_name == LEFT else self.right

    def reset(self) -> None:
        """Solta o botao e zera os dois canais; a direita volta a ser dona."""
        self.right.clear()
        self.left.clear()
        self._arbiter.reset()
        self._owner_name = RIGHT
        self.right.cursor.drives_os_cursor = True
        self.left.cursor.drives_os_cursor = False

    def update(
        self, hands: Sequence[HandLandmarks],
    ) -> List[Tuple[HandChannel, List[GestureEvent]]]:
        """Atribui as maos e roda o detector de cada canal (dona primeiro)."""
        owner = self.owner
        assignment = self._arbiter.assign(
            hands, time.perf_counter(),
            locked=owner.detector.is_gesture_held() or owner.cursor.is_dragging,
        )
        self.right.hand = assignment.right
        self.left.hand = assignment.left
        if assignment.owner is not None:
            self._set_owner(assignment.owner)

        owner = self.owner
        other = self.left if owner is self.right else self.right
        out = []
        for channel in (owner, other):
            if channel.hand is None:
                channel.lost_frames += 1
                if channel.lost_frames == _SMOOTHER_RESET_FRAMES:
                    channel.smoother.reset()
            else:
                channel.lost_frames = 0
            out.append((channel, channel.detector.update(channel.hand)))
        return out

    def apply(self, results: List[Tuple[HandChannel, List[GestureEvent]]]) -> None:
        """Move os ponteiros e executa as acoes retornadas por update()."""
        for channel, events in results:
            for ev in events:
                if ev.gesture == Gesture.MOVE and ev.position is not None:
                    x, y = channel.smoother(*ev.position)
                    channel.cursor.move(x, y)
            for ev in events:
                self._apply_action(channel, ev.gesture)

    def poses(self) -> List[Optional[Pose]]:
        """Pose de cada canal pro holograma: [direita, esquerda]."""
        return [self._pose(self.right), self._pose(self.left)]

    @property
    def owner_index(self) -> int:
        return 1 if self._owner_name == LEFT else 0

    @staticmethod
    def _pose(channel: HandChannel) -> Optional[Pose]:
        x, y = channel.cursor.last_position
        if channel.hand is None or x is None or y is None:
            return None
        return channel.hand.landmarks, x, y

    def _set_owner(self, name: str) -> None:
        if name == self._owner_name:
            return
        self.owner.cursor.drives_os_cursor = False
        self._owner_name = name
        self.owner.cursor.drives_os_cursor = True
        self.owner.cursor.sync_os_cursor()

    def _drag_holder(self) -> Optional[HandChannel]:
        for channel in (self.right, self.left):
            if channel.cursor.is_dragging:
                return channel
        return None

    def _apply_action(self, channel: HandChannel, gesture: Gesture) -> None:
        cursor = channel.cursor
        if gesture == Gesture.DRAG_END:
            cursor.drag_end()  # no-op se este canal nao segura o botao
            return
        if gesture not in (
            Gesture.CLICK, Gesture.DOUBLE_CLICK,
            Gesture.RIGHT_CLICK, Gesture.DRAG_START,
        ):
            return

        holder = self._drag_holder()
        if holder is not None and holder is not channel:
            logger.debug(
                "%s ignorado: mao %s esta arrastando", gesture.name, holder.name,
            )
            return

        if gesture == Gesture.DRAG_START and channel is not self.owner:
            self._arbiter.set_owner(channel.name, time.perf_counter())
            self._set_owner(channel.name)

        if channel is self.owner:
            if gesture == Gesture.CLICK:
                cursor.click()
            elif gesture == Gesture.DOUBLE_CLICK:
                cursor.double_click()
            elif gesture == Gesture.RIGHT_CLICK:
                cursor.right_click()
            else:
                cursor.drag_start()
            return

        x, y = cursor.last_position
        if x is None or y is None:
            logger.debug("%s ignorado: mao %s sem ponteiro", gesture.name, channel.name)
            return
        cursor.freeze(cursor.click_freeze_seconds)
        if gesture == Gesture.CLICK:
            cursor.click_at(x, y)
        elif gesture == Gesture.DOUBLE_CLICK:
            cursor.double_click_at(x, y)
        else:
            cursor.right_click_at(x, y)
        self.owner.cursor.sync_os_cursor()
