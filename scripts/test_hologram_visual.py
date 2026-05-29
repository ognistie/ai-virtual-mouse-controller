"""
Validacao visual STANDALONE do overlay.

Roda o overlay sozinho, sem mediapipe, sem camera, sem nada.
Liga ele e desenha:
- Badge vermelho no canto superior direito (sempre visivel)
- Anel vermelho seguindo o cursor do mouse

Se voce nao ver NADA disso, o problema esta no Tkinter/transparentcolor
do seu Windows e o overlay nao vai funcionar do jeito que esta. Nesse
caso me reporta.

Uso (na raiz do projeto):
    python scripts/test_hologram_visual.py

Sair: feche o terminal ou Ctrl+C.
"""

from __future__ import annotations

import os
import sys
import time

# Adiciona a raiz do projeto ao path pra importar core/
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from core.hologram_overlay import HologramOverlay  # noqa: E402


def main() -> int:
    print("[teste] criando overlay...", flush=True)
    h = HologramOverlay(
        hand_size_px=180,
        opacity=0.40,
        target_fps=30,
    )
    print(
        f"[teste] criado: available={h.available} "
        f"click_through={h.click_through_active} "
        f"tela={h._screen_w}x{h._screen_h}",
        flush=True,
    )

    if not h.available:
        print(
            "[teste] OVERLAY INDISPONIVEL. Provavelmente algo do Tkinter "
            "falhou. Reporta esse output.",
            flush=True,
        )
        return 1

    print("[teste] ligando overlay (set_enabled(True))...", flush=True)
    h.set_enabled(True)
    print(
        "[teste] AGORA voce DEVE estar vendo:\n"
        "  1. Um quadrado vermelho pequeno no canto superior direito da tela\n"
        "  2. Um anel vermelho seguindo o cursor do mouse\n"
        "Se ver: o overlay funciona, problema esta na integracao do servico.\n"
        "Se NAO ver: o problema esta no Tkinter/Windows do seu PC.\n"
        "Ctrl+C pra sair.",
        flush=True,
    )

    try:
        # Simula o cursor seguindo o mouse do sistema
        import ctypes
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        pt = POINT()
        user32 = ctypes.windll.user32

        while True:
            user32.GetCursorPos(ctypes.byref(pt))
            h.update_cursor(pt.x, pt.y)
            h.update_pose(None, 0, 0)  # sem mao -> mostra so o idle ring
            h.pump()
            time.sleep(0.016)  # ~60 Hz
    except KeyboardInterrupt:
        print("\n[teste] saindo...", flush=True)
    finally:
        h.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
