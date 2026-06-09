"""
core.keyboard.hover
===================

HoverDetector — encontra tecla mais próxima do indicador e calcula
proximity score normalizada [0,1].

Performance:
- Spatial grid (cell ~80px) → nearest_key em O(1) amortizado.
- Hover_score por-tecla suavizado com EMA (não OneEuro — overkill aqui).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import Key, KeyboardState


@dataclass
class KeyRect:
    """Bounding box em screen coords de uma tecla renderizada."""

    key: Key
    cx: float        # centro x
    cy: float        # centro y
    half_w: float    # meia-largura (px)
    half_h: float    # meia-altura (px)


class HoverDetector:
    """
    Recebe (x, y) do indicador em screen px + lista de KeyRect e mantém
    hover_score por tecla.

    EMA com alpha adaptativo:
    - alpha alto (0.6) na tecla atualmente hovered → expansão fluida
    - alpha baixo (0.15) nas demais → decay suave (evita flicker)
    """

    def __init__(self, *, ema_fast: float = 0.55, ema_slow: float = 0.18,
                 max_distance_factor: float = 1.55) -> None:
        # max_distance_factor 1.55 = hitbox virtual 55% maior que raio
        # nominal da tecla. Garante zero zona morta entre teclas.
        # Adaptive AI ajusta por tecla individual baseado em padroes do user.
        self._ema_fast = ema_fast
        self._ema_slow = ema_slow
        self._max_dist_factor = max_distance_factor
        self._rects: List[KeyRect] = []
        self._scores: Dict[str, float] = {}
        # Spatial grid (lazy-built no set_rects)
        self._cell_size: float = 100.0
        self._grid: Dict[Tuple[int, int], List[int]] = {}
        self._screen_w: float = 1920.0
        self._screen_h: float = 1080.0

    def set_rects(self, rects: List[KeyRect], screen_w: float, screen_h: float) -> None:
        """Atualiza geometria (chamar em resize/layout change)."""
        self._rects = rects
        self._screen_w = screen_w
        self._screen_h = screen_h
        # Cell size = ~1.5x média das half_w (boa cobertura)
        if rects:
            avg = sum(r.half_w for r in rects) / len(rects)
            self._cell_size = max(40.0, avg * 1.8)
        # Rebuild grid
        self._grid.clear()
        for idx, r in enumerate(rects):
            cx = int(r.cx // self._cell_size)
            cy = int(r.cy // self._cell_size)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    self._grid.setdefault((cx + dx, cy + dy), []).append(idx)
        # Inicializa scores ausentes
        for r in rects:
            self._scores.setdefault(r.key.code, 0.0)

    def update(self, finger_x: float, finger_y: float, state: KeyboardState
               ) -> Optional[str]:
        """
        Atualiza hover_score de todas as teclas. Retorna code da tecla
        hovered (com maior score acima do threshold) ou None.
        """
        if not self._rects:
            return None

        nearest_idx, nearest_dist = self._nearest(finger_x, finger_y)
        if nearest_idx < 0:
            self._decay_all(state)
            state.hovered_code = None
            return None

        nearest_rect = self._rects[nearest_idx]
        # hit_radius efetivo (com bias/scale do adaptive)
        ks = state.keys.get(nearest_rect.key.code)
        scale = ks.hit_scale if ks else 1.0
        bias_x = ks.bias_x if ks else 0.0
        bias_y = ks.bias_y if ks else 0.0

        # Distância ao centro AJUSTADO pelo bias adaptativo
        adj_cx = nearest_rect.cx + bias_x
        adj_cy = nearest_rect.cy + bias_y
        dx = finger_x - adj_cx
        dy = finger_y - adj_cy
        dist = math.hypot(dx, dy)

        # Raio de referência = diagonal da meia-célula * scale adaptativo
        ref_radius = math.hypot(nearest_rect.half_w, nearest_rect.half_h) * scale
        # Distância normalizada [0..max_dist_factor]
        norm = min(self._max_dist_factor, dist / max(1.0, ref_radius))
        # Score: 1.0 no centro, 0.0 no limite. Curva suave (cosine).
        if norm >= self._max_dist_factor:
            score_raw = 0.0
        else:
            t = norm / self._max_dist_factor   # [0..1]
            score_raw = 0.5 * (1.0 + math.cos(math.pi * t))

        # Atualiza scores: fast EMA na nearest, slow EMA decay nas demais
        for idx, r in enumerate(self._rects):
            code = r.key.code
            ks2 = state.keys.get(code)
            if ks2 is None:
                continue
            if idx == nearest_idx:
                ks2.hover_score = (
                    self._ema_fast * score_raw
                    + (1.0 - self._ema_fast) * ks2.hover_score
                )
            else:
                ks2.hover_score = (
                    (1.0 - self._ema_slow) * ks2.hover_score
                )

            # Expansion visual: lerp em direção a 1.18 com hover, 1.0 sem
            target_exp = 1.0 + 0.18 * ks2.hover_score
            ks2.expansion += (target_exp - ks2.expansion) * 0.35

        # Hovered code = nearest se score acima de threshold
        hovered = nearest_rect.key.code if score_raw > 0.15 else None
        state.hovered_code = hovered
        return hovered

    def _nearest(self, x: float, y: float) -> Tuple[int, float]:
        """Busca grid + linear nos vizinhos. O(1) amortizado."""
        cx = int(x // self._cell_size)
        cy = int(y // self._cell_size)
        candidates = self._grid.get((cx, cy), [])
        if not candidates:
            # Fallback global (raro — quando fora dos rects)
            candidates = range(len(self._rects))
        best = -1
        best_d = float("inf")
        for idx in candidates:
            r = self._rects[idx]
            # Distância ao bounding rect (0 se dentro)
            ddx = max(abs(x - r.cx) - r.half_w, 0.0)
            ddy = max(abs(y - r.cy) - r.half_h, 0.0)
            d = ddx * ddx + ddy * ddy
            if d < best_d:
                best_d = d
                best = idx
        return best, math.sqrt(best_d)

    def _decay_all(self, state: KeyboardState) -> None:
        for ks in state.keys.values():
            ks.hover_score *= 1.0 - self._ema_slow
            target_exp = 1.0
            ks.expansion += (target_exp - ks.expansion) * 0.25
