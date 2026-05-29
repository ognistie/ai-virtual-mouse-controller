"""
core/click_burst.py
===================

Sistema de bursts (animacao de clique) para o overlay holografico.

Cada vez que um clique acontece (esquerdo, direito, duplo, drag), o servico
chama BurstManager.fire(...) com a posicao do pinch. O manager mantem uma
lista de bursts ativos. O overlay chama .snapshot() pra obter o estado
visual atual de cada burst (raio, alpha, cor) e desenha como circulos.

Bursts sao puros — sem GUI, sem Tkinter aqui. Faceis de testar.

Visual:
- CLICK:       um anel vermelho que cresce e some
- RIGHT_CLICK: igual, mas em ciano
- DOUBLE:      dois aneis vermelhos staggered
- DRAG_START:  anel grande que pulsa (mais lento)
- DRAG_END:    anel que contrai (inverse)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# Tipos de burst suportados
KIND_CLICK = "click"
KIND_RIGHT_CLICK = "right"
KIND_DOUBLE_CLICK = "double"
KIND_DRAG_START = "drag_start"
KIND_DRAG_END = "drag_end"


@dataclass(frozen=True)
class BurstSnapshot:
    """Estado visual de um burst em um instante."""
    x: float
    y: float
    radius: float
    alpha: float          # 0.0 (invisivel) a 1.0 (cheio)
    width: float          # espessura do anel
    color_kind: str       # "red" ou "cyan" — overlay traduz pra cor
    # Pra DOUBLE_CLICK: 2 aneis. O 2o vem ~100ms depois do 1o.
    secondary_radius: float = 0.0
    secondary_alpha: float = 0.0


@dataclass
class _Burst:
    """Burst interno do manager — privado, nao exponhe direto."""
    x: float
    y: float
    kind: str
    born_at: float
    duration: float
    max_radius: float

    def alive(self, now: float) -> bool:
        return now - self.born_at < self.duration

    def progress(self, now: float) -> float:
        return max(0.0, min(1.0, (now - self.born_at) / self.duration))

    def snapshot(self, now: float) -> BurstSnapshot:
        p = self.progress(now)

        # Cor por tipo
        if self.kind in (KIND_RIGHT_CLICK,):
            color = "cyan"
        else:
            color = "red"

        # DRAG_END: anel contrai (raio decresce com p)
        if self.kind == KIND_DRAG_END:
            radius = self.max_radius * (1.0 - p)
            alpha = 1.0 - p
            return BurstSnapshot(
                x=self.x, y=self.y,
                radius=radius, alpha=alpha,
                width=3.0,
                color_kind=color,
            )

        # DRAG_START: anel grande "pulsa" no inicio depois aguenta brilhante
        if self.kind == KIND_DRAG_START:
            # Cresce rapido (primeira metade), depois sustenta
            if p < 0.4:
                grow = p / 0.4
                radius = self.max_radius * grow
                alpha = grow
            else:
                radius = self.max_radius
                # alpha pulsa de leve
                alpha = 0.7 + 0.3 * ((p - 0.4) * 6) % 1.0
            return BurstSnapshot(
                x=self.x, y=self.y,
                radius=radius, alpha=alpha,
                width=4.0,
                color_kind=color,
            )

        # CLICK e RIGHT_CLICK: anel cresce e desvanece em paralelo
        # Easing: cubic-out pra crescer rapido no inicio e devagar no fim
        eased = 1.0 - (1.0 - p) ** 3
        radius = self.max_radius * eased
        alpha = 1.0 - p

        # DOUBLE_CLICK: segundo anel staggered (atrasado 35%)
        secondary_radius = 0.0
        secondary_alpha = 0.0
        if self.kind == KIND_DOUBLE_CLICK:
            p2 = max(0.0, (p - 0.35) / 0.65) if p >= 0.35 else 0.0
            if p2 > 0.0:
                eased2 = 1.0 - (1.0 - p2) ** 3
                secondary_radius = self.max_radius * 0.9 * eased2
                secondary_alpha = 1.0 - p2

        return BurstSnapshot(
            x=self.x, y=self.y,
            radius=radius, alpha=alpha,
            width=3.0,
            color_kind=color,
            secondary_radius=secondary_radius,
            secondary_alpha=secondary_alpha,
        )


# Duracoes padrao por tipo (em segundos)
_DEFAULT_DURATION = {
    KIND_CLICK: 0.45,
    KIND_RIGHT_CLICK: 0.45,
    KIND_DOUBLE_CLICK: 0.65,
    KIND_DRAG_START: 0.55,
    KIND_DRAG_END: 0.35,
}

# Raio maximo padrao por tipo
_DEFAULT_MAX_RADIUS = {
    KIND_CLICK: 70.0,
    KIND_RIGHT_CLICK: 70.0,
    KIND_DOUBLE_CLICK: 85.0,
    KIND_DRAG_START: 90.0,
    KIND_DRAG_END: 60.0,
}


class BurstManager:
    """
    Gerencia bursts ativos. Estado puro (sem Tk).

    Uso:
        bm = BurstManager()
        bm.fire(x=400, y=300, kind="click")
        ...
        # em cada frame do redraw:
        bm.cleanup()
        for snap in bm.snapshots():
            # desenha snap.x, snap.y, snap.radius, snap.alpha...
    """

    def __init__(self) -> None:
        self._bursts: List[_Burst] = []

    def fire(
        self,
        x: float,
        y: float,
        kind: str = KIND_CLICK,
        duration: Optional[float] = None,
        max_radius: Optional[float] = None,
    ) -> None:
        """Dispara um novo burst no ponto (x, y)."""
        if duration is None:
            duration = _DEFAULT_DURATION.get(kind, 0.4)
        if max_radius is None:
            max_radius = _DEFAULT_MAX_RADIUS.get(kind, 70.0)
        now = time.perf_counter()
        self._bursts.append(_Burst(
            x=x, y=y, kind=kind,
            born_at=now,
            duration=duration,
            max_radius=max_radius,
        ))

    def cleanup(self, now: Optional[float] = None) -> int:
        """Remove bursts que ja terminaram. Retorna quantos foram removidos."""
        if now is None:
            now = time.perf_counter()
        before = len(self._bursts)
        self._bursts = [b for b in self._bursts if b.alive(now)]
        return before - len(self._bursts)

    def snapshots(self, now: Optional[float] = None) -> Tuple[BurstSnapshot, ...]:
        """Estado visual atual de cada burst ativo."""
        if now is None:
            now = time.perf_counter()
        return tuple(b.snapshot(now) for b in self._bursts if b.alive(now))

    def count(self) -> int:
        return len(self._bursts)

    def clear(self) -> None:
        self._bursts.clear()
