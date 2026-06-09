"""
core.keyboard.output
====================

SystemTyper — envia teclas ao SO via pyautogui (já dep do projeto).

Mapeia codes do nosso layout (ex.: 'space', 'enter', 'shift', 'altgr',
'backspace') pros nomes que pyautogui espera. Caracteres com acento /
símbolos ABNT2 vão por `typewrite` (mais robusto que `press`).
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

try:
    import pyautogui
    _PA_OK = True
except Exception as e:
    pyautogui = None  # type: ignore
    _PA_OK = False
    logger.warning("pyautogui indisponível (%s) — SystemTyper em modo dry-run", e)


# Mapeamento code → pyautogui key name
_CODE_MAP = {
    "space": "space",
    "enter": "enter",
    "tab": "tab",
    "backspace": "backspace",
    "esc": "esc",
    "shift": "shift",
    "shift_r": "shiftright",
    "ctrl": "ctrl",
    "ctrl_r": "ctrlright",
    "alt": "alt",
    "alt_r": "altright",
    "altgr": "altright",
    "win": "win",
    "menu": "apps",
    "caps": "capslock",
}
for i in range(1, 13):
    _CODE_MAP[f"f{i}"] = f"f{i}"


class SystemTyper:
    """
    Stateless wrapper. Aceita opcionais modifiers explícitos pra acentos
    e maiúsculas (renderer/controller já calcula o `char` final, então a
    via principal é `type_char(char)` direto).
    """

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run or not _PA_OK

    # ───────────────────────────────────────────────── API principal

    def type_char(self, ch: str) -> None:
        """Digita um caractere literal (já com case/acento resolvidos)."""
        if not ch:
            return
        if self.dry_run:
            logger.debug("dry_run type_char: %r", ch)
            return
        try:
            pyautogui.typewrite(ch)
        except Exception as e:
            logger.warning("typewrite falhou em %r: %s", ch, e)

    def press_code(self, code: str) -> None:
        """Pressiona uma tecla de controle (space/enter/backspace/...)."""
        key = _CODE_MAP.get(code, code)
        if self.dry_run:
            logger.debug("dry_run press_code: %s", key)
            return
        try:
            pyautogui.press(key)
        except Exception as e:
            logger.warning("press falhou em %s: %s", key, e)

    def hotkey(self, *codes: str) -> None:
        """Combinação tipo ctrl+c. Aceita codes do nosso mapping."""
        keys = [_CODE_MAP.get(c, c) for c in codes]
        if self.dry_run:
            logger.debug("dry_run hotkey: %s", "+".join(keys))
            return
        try:
            pyautogui.hotkey(*keys)
        except Exception as e:
            logger.warning("hotkey falhou em %s: %s", "+".join(keys), e)

    def type_word(self, word: str) -> None:
        """Envia palavra completa (usado por sugestão aceita)."""
        if self.dry_run:
            logger.debug("dry_run type_word: %r", word)
            return
        try:
            pyautogui.typewrite(word)
        except Exception as e:
            logger.warning("type_word falhou: %s", e)
