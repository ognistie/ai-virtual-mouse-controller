"""
core.keyboard
=============

Smart Adaptive Holographic Keyboard — teclado virtual gesture-driven com
hover + pinch, IA adaptativa invisível e predição de texto PT-BR.

Ver `docs/smart-adaptive-holographic-keyboard-spec.md` para spec completa.
"""

from .accessibility import AccessibilitySettings
from .adaptive import AdaptiveModel
from .controller import KeyboardController
from .hover import HoverDetector, KeyRect
from .keyboard_overlay import KeyboardOverlay
from .layouts import ABNT2, COMPACT, FULL, LAYOUTS, QWERTY, get_layout
from .models import Key, KeyboardState, KeyEvent, KeyLayout, KeyState
from .output import SystemTyper
from .prediction import TextPredictor
from .renderer import KeyboardRenderer

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
    "KeyboardController",
    "KeyboardOverlay",
    "KeyboardRenderer",
    "KeyboardState",
    "SystemTyper",
    "TextPredictor",
    "get_layout",
]
