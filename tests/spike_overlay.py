"""
Spike de viabilidade — janela transparente click-through.

Roda standalone. Abre uma janela fullscreen transparente, sempre no topo,
sem capturar cliques (voce continua clicando nas janelas debaixo).

Desenha um circulo vermelho seguindo o cursor do sistema.

Uso:
    python tests/spike_overlay.py

Pressione ESC pra sair (ou Ctrl+C no terminal).
"""

from __future__ import annotations

import sys
import tkinter as tk

# Cor "magica" que vira transparente. Tem que ser uma cor que NAO aparece
# em mais nada na tela. #010203 e' praticamente preto, quase nunca colide.
TRANSPARENT_COLOR = "#010203"


def _setup_click_through(root: tk.Tk) -> bool:
    """
    Aplica WS_EX_LAYERED | WS_EX_TRANSPARENT na janela do Tk.
    So funciona em Windows. Retorna True se aplicou.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020

        user32 = ctypes.windll.user32
        # winfo_id retorna a child window; precisamos do HWND parent
        hwnd = user32.GetParent(root.winfo_id())
        styles = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, styles | WS_EX_LAYERED | WS_EX_TRANSPARENT
        )
        return True
    except Exception as e:  # pragma: no cover
        print(f"[spike] click-through falhou: {e}")
        return False


def main() -> None:
    root = tk.Tk()
    root.title("AVM Hologram Spike")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", TRANSPARENT_COLOR)
    root.configure(bg=TRANSPARENT_COLOR)
    root.overrideredirect(True)

    canvas = tk.Canvas(
        root,
        bg=TRANSPARENT_COLOR,
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(fill="both", expand=True)

    # Aplica click-through depois que a janela existe de fato
    root.update_idletasks()
    ok = _setup_click_through(root)
    print(f"[spike] click-through ativo: {ok}")

    # ESC fecha
    root.bind("<Escape>", lambda _e: root.destroy())

    dot_id = canvas.create_oval(0, 0, 0, 0, fill="#d92626", outline="")

    def tick() -> None:
        if not root.winfo_exists():
            return
        x = root.winfo_pointerx()
        y = root.winfo_pointery()
        r = 18
        canvas.coords(dot_id, x - r, y - r, x + r, y + r)
        root.after(16, tick)  # ~60 Hz

    tick()
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
