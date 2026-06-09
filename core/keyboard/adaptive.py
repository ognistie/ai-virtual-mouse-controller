"""
core.keyboard.adaptive
======================

AdaptiveModel — IA adaptativa invisível.

Aprende padrões de erro do usuário (ex.: mira "T" mas erra em "R") e
ajusta INTERNAMENTE a área de ativação da tecla T sem alterar o visual.

Algoritmo (leve, < 1 ms por frame):

1. Quando o usuário pressiona uma tecla, registra (target_key, finger_xy).
2. Se o usuário pressiona BACKSPACE logo em seguida e depois acerta uma
   tecla vizinha, classifica como "miss" da tecla original.
3. Para cada (key, neighbor_que_o_user_realmente_queria) acumula um
   contador exponencial. Quando miss_rate > MISS_THRESHOLD, expande o
   hit_radius da tecla alvo em direção àquela vizinha.

Persistência: JSON em data/keyboard/adaptive_profile.json.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional, Tuple

from .models import Key, KeyboardState, KeyEvent

logger = logging.getLogger(__name__)


# Janela curta de eventos recentes pra detectar correção (BACKSPACE → tecla
# diferente). Após `CORRECTION_WINDOW_SECS` o evento é descartado.
CORRECTION_WINDOW_SECS = 2.0

# Quando miss_rate de uma tecla excede isso, expande hit_radius.
MISS_THRESHOLD = 0.15

# Cap máximo de expansão (25% além do padrão).
HIT_SCALE_MAX = 1.25

# Cap de deslocamento do centro lógico (% do half_w).
BIAS_MAX_RATIO = 0.20

# EWMA factor pro miss tracking.
EWMA_ALPHA = 0.10

# Decay diário (memória fica menos rígida com o tempo).
DAILY_DECAY = 0.99


@dataclass
class _KeyStats:
    hits: float = 0.0
    misses: float = 0.0
    # miss_vector_sum: soma dos offsets (finger - center) das vezes que
    # o usuário "errou" perto desta tecla. Média = direção do bias.
    miss_dx_sum: float = 0.0
    miss_dy_sum: float = 0.0
    last_t: float = 0.0


class AdaptiveModel:
    """
    Aprende invisívelmente. Aplica via `apply_to_state` antes do hit-test.
    """

    def __init__(self, profile_path: Optional[str] = None) -> None:
        self._stats: Dict[str, _KeyStats] = {}
        self._recent: list[Tuple[float, KeyEvent, Tuple[float, float]]] = []
        # ↑ (t, event, finger_xy)
        self._profile_path = profile_path
        if profile_path:
            self.load(profile_path)

    # ───────────────────────────────────────────────────── learn

    def record_press(self, event: KeyEvent, finger_xy: Tuple[float, float],
                     state: KeyboardState) -> None:
        """Chamar a cada KeyEvent. Detecta padrões e atualiza stats."""
        s = self._stats.setdefault(event.code, _KeyStats())
        s.hits += 1.0
        s.last_t = event.timestamp

        # Detecta correção: se evento anterior foi BACKSPACE precedido por
        # outra tecla X (≠ event.code) dentro da janela, classifica X como miss.
        if event.code == "backspace":
            return
        if len(self._recent) >= 2:
            prev = self._recent[-1]
            prev_prev = self._recent[-2]
            t_now = event.timestamp
            if (
                prev[1].code == "backspace"
                and prev_prev[1].code != event.code
                and (t_now - prev_prev[0]) < CORRECTION_WINDOW_SECS
            ):
                wrong_code = prev_prev[1].code
                wrong_finger = prev_prev[2]
                # Atualiza miss de wrong_code, com offset apontando pra
                # tecla que o usuário REALMENTE queria (event.code).
                wstats = self._stats.setdefault(wrong_code, _KeyStats())
                wstats.misses = wstats.misses * (1 - EWMA_ALPHA) + EWMA_ALPHA
                # Direção do bias: do centro da wrong_key em direção ao
                # finger_xy do tap original (= onde o usuário PENSOU que estava
                # clicando). Procura rect da tecla:
                rect_cx, rect_cy = self._key_center(wrong_code, state)
                if rect_cx is not None:
                    wstats.miss_dx_sum += (wrong_finger[0] - rect_cx) * EWMA_ALPHA
                    wstats.miss_dy_sum += (wrong_finger[1] - rect_cy) * EWMA_ALPHA

        self._recent.append((event.timestamp, event, finger_xy))
        # Mantém só os últimos 8 eventos
        if len(self._recent) > 8:
            self._recent.pop(0)

    # ───────────────────────────────────────────────────── apply

    def apply_to_state(self, state: KeyboardState,
                       half_w_avg: float, half_h_avg: float) -> None:
        """
        Lê stats e ajusta KeyState.hit_scale + bias_xy de cada tecla.
        Chamar a cada N frames (~30) — não precisa ser por frame.
        """
        for code, ks in state.keys.items():
            s = self._stats.get(code)
            if s is None or s.hits + s.misses < 5:
                ks.hit_scale = 1.0
                ks.bias_x = 0.0
                ks.bias_y = 0.0
                continue
            total = max(1.0, s.hits + s.misses)
            miss_rate = s.misses / total
            if miss_rate < MISS_THRESHOLD:
                ks.hit_scale = 1.0
                ks.bias_x = 0.0
                ks.bias_y = 0.0
                continue
            # Escala cresce com miss_rate, clampada
            excess = (miss_rate - MISS_THRESHOLD) / (1.0 - MISS_THRESHOLD)
            ks.hit_scale = 1.0 + (HIT_SCALE_MAX - 1.0) * min(1.0, excess)
            # Bias = média dos offsets de miss (clampado)
            if s.misses > 0.01:
                bx = s.miss_dx_sum / s.misses
                by = s.miss_dy_sum / s.misses
                bx = max(-half_w_avg * BIAS_MAX_RATIO,
                         min(half_w_avg * BIAS_MAX_RATIO, bx))
                by = max(-half_h_avg * BIAS_MAX_RATIO,
                         min(half_h_avg * BIAS_MAX_RATIO, by))
                ks.bias_x = bx
                ks.bias_y = by
            else:
                ks.bias_x = 0.0
                ks.bias_y = 0.0

    # ───────────────────────────────────────────────────── decay

    def daily_decay(self) -> None:
        """Decai contadores 1% — chamar no startup se passou um dia."""
        for s in self._stats.values():
            s.hits *= DAILY_DECAY
            s.misses *= DAILY_DECAY
            s.miss_dx_sum *= DAILY_DECAY
            s.miss_dy_sum *= DAILY_DECAY

    # ───────────────────────────────────────────────────── persist

    def save(self, path: Optional[str] = None) -> None:
        p = path or self._profile_path
        if not p:
            return
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            data = {
                "version": 1,
                "saved_at": time.time(),
                "stats": {k: asdict(v) for k, v in self._stats.items()},
            }
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("AdaptiveModel.save falhou: %s", e)

    def load(self, path: str) -> None:
        try:
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.get("stats", {}).items():
                self._stats[k] = _KeyStats(**v)
            # Aplica decay se faz mais de 1 dia desde save
            saved_at = data.get("saved_at", 0.0)
            days = max(0, (time.time() - saved_at) / 86400.0)
            for _ in range(int(days)):
                self.daily_decay()
        except Exception as e:
            logger.warning("AdaptiveModel.load falhou: %s", e)

    # ───────────────────────────────────────────────────── helpers

    def _key_center(self, code: str, state: KeyboardState
                    ) -> Tuple[Optional[float], Optional[float]]:
        """Aproximação: posição grid-units da tecla (sem px). Renderer
        injeta cx/cy reais quando disponível — aqui é fallback simbólico."""
        k = state.layout.by_code(code)
        if k is None:
            return None, None
        return float(k.col + k.width / 2.0), float(k.row + k.height / 2.0)
