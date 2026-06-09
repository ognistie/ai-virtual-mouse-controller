"""
core.keyboard.layouts
=====================

Definições de layout: ABNT2, QWERTY (US), COMPACT, FULL.

Coordenadas em grid units. (0,0) = top-left. Linha 0 = top row.
Renderer escala pra px com base em (cell_size, scale).
"""

from __future__ import annotations

from .models import Key, KeyLayout


# ───────────────────────────────────────────────────────── helpers


def _row(row: int, items: list, start_col: float = 0.0) -> list[Key]:
    """
    Constrói uma linha de teclas a partir de uma lista. Cada item pode ser:
        - str: tecla simples, width=1.0, code=label
        - tuple(code, label, label_shift, width, role, modifier, sticky, label_altgr)
    """
    out: list[Key] = []
    col = start_col
    for it in items:
        if isinstance(it, str):
            out.append(
                Key(
                    code=it.lower(),
                    label=it,
                    label_shift=it.upper(),
                    row=row,
                    col=col,
                    width=1.0,
                    role="char",
                )
            )
            col += 1.0
        else:
            code, label, label_shift, width, role, mod, sticky, label_altgr = (
                list(it) + [""] * (8 - len(it))
            )[:8]
            out.append(
                Key(
                    code=code,
                    label=label,
                    label_shift=label_shift or "",
                    label_altgr=label_altgr or "",
                    row=row,
                    col=col,
                    width=float(width or 1.0),
                    role=role or "char",
                    modifier=bool(mod),
                    sticky=bool(sticky),
                )
            )
            col += float(width or 1.0)
    return out


# ───────────────────────────────────────────────────────── ABNT2


def _build_abnt2() -> KeyLayout:
    keys: list[Key] = []

    # Row 0 — números + símbolos
    keys += _row(0, [
        ("'", "'", '"', 1.0, "char", False, False, ""),
        ("1", "1", "!", 1.0, "char", False, False, "¹"),
        ("2", "2", "@", 1.0, "char", False, False, "²"),
        ("3", "3", "#", 1.0, "char", False, False, "³"),
        ("4", "4", "$", 1.0, "char", False, False, "£"),
        ("5", "5", "%", 1.0, "char", False, False, "¢"),
        ("6", "6", "¨", 1.0, "char", False, False, "¬"),
        ("7", "7", "&", 1.0, "char", False, False, ""),
        ("8", "8", "*", 1.0, "char", False, False, ""),
        ("9", "9", "(", 1.0, "char", False, False, ""),
        ("0", "0", ")", 1.0, "char", False, False, ""),
        ("-", "-", "_", 1.0, "char", False, False, ""),
        ("=", "=", "+", 1.0, "char", False, False, "§"),
        ("backspace", "⌫", "", 2.0, "backspace", False, False, ""),
    ])

    # Row 1
    keys += _row(1, [
        ("tab", "⇥", "", 1.5, "system", False, False, ""),
        "q", "w", "e", "r", "t", "y", "u", "i", "o", "p",
        ("´", "´", "`", 1.0, "char", False, False, ""),
        ("[", "[", "{", 1.0, "char", False, False, "ª"),
        ("enter", "↵", "", 1.5, "enter", False, False, ""),
    ])

    # Row 2
    keys += _row(2, [
        ("caps", "⇪", "", 1.75, "modifier", True, True, ""),
        "a", "s", "d", "f", "g", "h", "j", "k", "l",
        ("ç", "ç", "Ç", 1.0, "char", False, False, ""),
        ("~", "~", "^", 1.0, "char", False, False, ""),
        ("]", "]", "}", 1.0, "char", False, False, "º"),
    ])

    # Row 3
    keys += _row(3, [
        ("shift", "⇧", "", 1.25, "modifier", True, True, ""),
        ("\\", "\\", "|", 1.0, "char", False, False, ""),
        "z", "x", "c", "v", "b", "n", "m",
        (",", ",", "<", 1.0, "char", False, False, ""),
        (".", ".", ">", 1.0, "char", False, False, ""),
        (";", ";", ":", 1.0, "char", False, False, ""),
        ("/", "/", "?", 1.0, "char", False, False, ""),
        ("shift_r", "⇧", "", 1.75, "modifier", True, True, ""),
    ])

    # Row 4 — bottom
    keys += _row(4, [
        ("ctrl", "Ctrl", "", 1.25, "modifier", True, True, ""),
        ("win", "⊞", "", 1.0, "modifier", True, False, ""),
        ("alt", "Alt", "", 1.25, "modifier", True, True, ""),
        ("space", " ", "", 6.5, "space", False, False, ""),
        ("altgr", "AltGr", "", 1.25, "modifier", True, True, ""),
        ("menu", "≡", "", 1.0, "system", False, False, ""),
        ("ctrl_r", "Ctrl", "", 1.25, "modifier", True, True, ""),
    ])

    return KeyLayout(name="ABNT2", keys=tuple(keys), rows=5, cols=15.0)


# ───────────────────────────────────────────────────────── QWERTY US


def _build_qwerty() -> KeyLayout:
    keys: list[Key] = []

    keys += _row(0, [
        ("`", "`", "~", 1.0, "char", False, False, ""),
        ("1", "1", "!", 1.0, "char", False, False, ""),
        ("2", "2", "@", 1.0, "char", False, False, ""),
        ("3", "3", "#", 1.0, "char", False, False, ""),
        ("4", "4", "$", 1.0, "char", False, False, ""),
        ("5", "5", "%", 1.0, "char", False, False, ""),
        ("6", "6", "^", 1.0, "char", False, False, ""),
        ("7", "7", "&", 1.0, "char", False, False, ""),
        ("8", "8", "*", 1.0, "char", False, False, ""),
        ("9", "9", "(", 1.0, "char", False, False, ""),
        ("0", "0", ")", 1.0, "char", False, False, ""),
        ("-", "-", "_", 1.0, "char", False, False, ""),
        ("=", "=", "+", 1.0, "char", False, False, ""),
        ("backspace", "⌫", "", 2.0, "backspace", False, False, ""),
    ])
    keys += _row(1, [
        ("tab", "⇥", "", 1.5, "system", False, False, ""),
        "q", "w", "e", "r", "t", "y", "u", "i", "o", "p",
        ("[", "[", "{", 1.0, "char", False, False, ""),
        ("]", "]", "}", 1.0, "char", False, False, ""),
        ("\\", "\\", "|", 1.5, "char", False, False, ""),
    ])
    keys += _row(2, [
        ("caps", "⇪", "", 1.75, "modifier", True, True, ""),
        "a", "s", "d", "f", "g", "h", "j", "k", "l",
        (";", ";", ":", 1.0, "char", False, False, ""),
        ("'", "'", '"', 1.0, "char", False, False, ""),
        ("enter", "↵", "", 2.25, "enter", False, False, ""),
    ])
    keys += _row(3, [
        ("shift", "⇧", "", 2.25, "modifier", True, True, ""),
        "z", "x", "c", "v", "b", "n", "m",
        (",", ",", "<", 1.0, "char", False, False, ""),
        (".", ".", ">", 1.0, "char", False, False, ""),
        ("/", "/", "?", 1.0, "char", False, False, ""),
        ("shift_r", "⇧", "", 2.75, "modifier", True, True, ""),
    ])
    keys += _row(4, [
        ("ctrl", "Ctrl", "", 1.25, "modifier", True, True, ""),
        ("win", "⊞", "", 1.25, "modifier", True, False, ""),
        ("alt", "Alt", "", 1.25, "modifier", True, True, ""),
        ("space", " ", "", 6.25, "space", False, False, ""),
        ("alt_r", "Alt", "", 1.25, "modifier", True, True, ""),
        ("menu", "≡", "", 1.25, "system", False, False, ""),
        ("ctrl_r", "Ctrl", "", 1.5, "modifier", True, True, ""),
    ])

    return KeyLayout(name="QWERTY", keys=tuple(keys), rows=5, cols=15.0)


# ───────────────────────────────────────────────────────── COMPACT


def _build_compact() -> KeyLayout:
    """Layout reduzido (3 linhas) pra digitação rápida em demos."""
    keys: list[Key] = []
    keys += _row(0, list("qwertyuiop") + [("backspace", "⌫", "", 1.5, "backspace", False, False, "")])
    keys += _row(1, [("shift", "⇧", "", 1.25, "modifier", True, True, "")]
                 + list("asdfghjkl")
                 + [("enter", "↵", "", 1.75, "enter", False, False, "")])
    keys += _row(2, [
        ("symbols", "?123", "", 1.5, "system", False, True, ""),
        "z", "x", "c", "v", "b", "n", "m",
        (",", ",", "<", 1.0, "char", False, False, ""),
        (".", ".", ">", 1.0, "char", False, False, ""),
        ("space", " ", "", 3.0, "space", False, False, ""),
    ])
    return KeyLayout(name="COMPACT", keys=tuple(keys), rows=3, cols=11.5)


# ───────────────────────────────────────────────────────── FULL (ABNT2 + F-keys)


def _build_full() -> KeyLayout:
    base = _build_abnt2()
    f_row = _row(-1, [
        ("esc", "Esc", "", 1.0, "system", False, False, ""),
        ("f1", "F1", "", 1.0, "system", False, False, ""),
        ("f2", "F2", "", 1.0, "system", False, False, ""),
        ("f3", "F3", "", 1.0, "system", False, False, ""),
        ("f4", "F4", "", 1.0, "system", False, False, ""),
        ("f5", "F5", "", 1.0, "system", False, False, ""),
        ("f6", "F6", "", 1.0, "system", False, False, ""),
        ("f7", "F7", "", 1.0, "system", False, False, ""),
        ("f8", "F8", "", 1.0, "system", False, False, ""),
        ("f9", "F9", "", 1.0, "system", False, False, ""),
        ("f10", "F10", "", 1.0, "system", False, False, ""),
        ("f11", "F11", "", 1.0, "system", False, False, ""),
        ("f12", "F12", "", 1.0, "system", False, False, ""),
    ])
    # Re-numera rows (F-row vira 0, demais +1)
    f_row = [Key(**{**k.__dict__, "row": 0}) for k in f_row]
    shifted = tuple(Key(**{**k.__dict__, "row": k.row + 1}) for k in base.keys)
    return KeyLayout(
        name="FULL",
        keys=tuple(f_row) + shifted,
        rows=base.rows + 1,
        cols=max(base.cols, 13.0),
    )


# ───────────────────────────────────────────────────────── Registry

ABNT2 = _build_abnt2()
QWERTY = _build_qwerty()
COMPACT = _build_compact()
FULL = _build_full()

LAYOUTS = {
    "ABNT2": ABNT2,
    "QWERTY": QWERTY,
    "COMPACT": COMPACT,
    "FULL": FULL,
}


def get_layout(name: str) -> KeyLayout:
    return LAYOUTS.get(name.upper(), ABNT2)
