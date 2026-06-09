"""
core.keyboard
=============

Smart Adaptive Holographic Keyboard — teclado virtual gesture-driven com
hover + pinch, IA adaptativa invisível e predição de texto PT-BR.

Ver `docs/smart-adaptive-holographic-keyboard-spec.md` para spec completa.
"""

from .accessibility import AccessibilitySettings
from .adaptive import AdaptiveModel
from .hover import HoverDetector, KeyRect
from .layouts import ABNT2, COMPACT, FULL, LAYOUTS, QWERTY, get_layout
from .models import Key, KeyboardState, KeyEvent, KeyLayout, KeyState
from .output import SystemTyper
from .prediction import TextPredictor

__all__ = [
    "ABNT2",
    "COMPACT",
    "FULL",
    "QWERTY",
    "LAYOUTS",
    "AccessibilitySettings",
    "AdaptiveModel",
    "HoverDetector",
    "Key",
    "KeyEvent",
    "KeyLayout",
    "KeyRect",
    "KeyState",
    "KeyboardState",
    "SystemTyper",
    "TextPredictor",
    "get_layout",
]
