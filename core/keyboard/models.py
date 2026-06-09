"""
core.keyboard.models
====================

Dataclasses do Smart Adaptive Holographic Keyboard.

Imutáveis (frozen) onde faz sentido — Key/KeyLayout são compartilhados entre
threads (render + controller) e precisam ser hashable pra cache de paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


# ───────────────────────────────────────────────────────── Key / Layout


@dataclass(frozen=True)
class Key:
    """
    Definição estática de uma tecla.

    Coordenadas em "grid units" (unidades lógicas). Largura 1.0 = uma tecla
    padrão. Space = width 5.0. Renderer multiplica por cell_size_px.
    """

    code: str            # identificador SO: 'a', 'shift', 'space', 'enter', ...
    label: str           # texto desenhado quando sem modifier
    label_shift: str = ""# texto quando shift ativo (vazio → upper(label))
    label_altgr: str = ""# texto quando AltGr ativo (símbolos ABNT2)
    row: int = 0
    col: float = 0.0
    width: float = 1.0
    height: float = 1.0
    modifier: bool = False           # tecla que ativa/desativa estado (shift/ctrl/alt/altgr/caps)
    sticky: bool = False             # se True, latch on/off (caps, shift)
    role: str = "char"               # 'char' | 'modifier' | 'space' | 'enter' | 'backspace' | 'suggestion' | 'system'


@dataclass(frozen=True)
class KeyLayout:
    name: str
    keys: Tuple[Key, ...]
    rows: int
    cols: float

    def by_code(self, code: str) -> Optional[Key]:
        for k in self.keys:
            if k.code == code:
                return k
        return None


# ───────────────────────────────────────────────────────── Estado runtime


@dataclass
class KeyState:
    """Estado mutável por tecla (alimentado por hover/press/adaptive)."""

    key: Key
    hover_score: float = 0.0          # [0,1] proximity normalizada
    expansion: float = 1.0            # escala visual atual (lerp em direção a 1.18)
    pressed_t: float = 0.0            # timestamp do último press (para ripple)
    ripple_t: float = 0.0             # idem (separado pra cleanup)
    hit_count: int = 0
    miss_count: int = 0               # vezes que o usuário "errou" perto desta tecla
    # Ajuste invisível: vetor 2D no espaço local da tecla que desvia o
    # centro lógico de hit-test. Aprendido por AdaptiveModel.
    bias_x: float = 0.0
    bias_y: float = 0.0
    # Multiplicador interno do hit_radius (1.0 = sem expansão).
    hit_scale: float = 1.0


@dataclass
class KeyboardState:
    layout: KeyLayout
    keys: Dict[str, KeyState] = field(default_factory=dict)
    hovered_code: Optional[str] = None
    shift_on: bool = False
    caps_on: bool = False
    altgr_on: bool = False
    ctrl_on: bool = False
    alt_on: bool = False
    suggestions: Tuple[str, ...] = ()
    visible: bool = False
    # Tracking pra UX
    last_keypress_t: float = 0.0
    typing_speed_cps: float = 0.0     # chars per second (média móvel)


# ───────────────────────────────────────────────────────── Eventos


@dataclass(frozen=True)
class KeyEvent:
    """Disparado quando hover+pinch confirma uma tecla."""

    code: str
    char: str                          # caractere final (já com shift/altgr resolvido)
    timestamp: float
    confidence: float                  # [0,1] qualidade do hover no momento do pinch
    x: float                           # screen px (para ripple)
    y: float


@dataclass(frozen=True)
class SuggestionEvent:
    """Seleção de uma sugestão pelo usuário."""

    word: str
    index: int
    timestamp: float
