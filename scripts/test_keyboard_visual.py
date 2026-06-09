"""
scripts/test_keyboard_visual.py
===============================

Teste visual STANDALONE do Smart Adaptive Holographic Keyboard.

Abre o teclado holografico SEM camera/MediaPipe e simula hover varrendo
as teclas + dispara ripples. Serve pra confirmar que a janela PySide6
aparece na sua maquina, isolada do loop principal (camera/gestos).

Uso:
    python scripts/test_keyboard_visual.py
    python scripts/test_keyboard_visual.py --layout QWERTY
    python scripts/test_keyboard_visual.py --seconds 20

Sai sozinho apos --seconds (default 15) ou com Ctrl+C.
Tambem salva docs/keyboard_preview.png.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

# Permite rodar de qualquer lugar (adiciona raiz do projeto ao path)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.keyboard import KeyboardOverlay  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="ABNT2",
                    help="ABNT2 | QWERTY | COMPACT | FULL")
    ap.add_argument("--seconds", type=float, default=15.0)
    args = ap.parse_args()

    kb = KeyboardOverlay(
        layout_name=args.layout,
        typer_dry_run=True,  # NAO escreve no SO durante o teste
        dict_path=os.path.join(_ROOT, "data", "keyboard", "dict_pt_br.txt"),
    )
    if not kb.available:
        print("[TEST] KeyboardOverlay indisponivel — PySide6 nao instalado?")
        print("       Instale com: pip install PySide6")
        return 1

    kb.state.suggestions = ("inteligencia", "interface", "integrado")
    kb.set_enabled(True)
    print(f"[TEST] Teclado '{args.layout}' aberto. available={kb.available}")
    print(f"[TEST] Janela deve aparecer na parte INFERIOR da tela.")
    print(f"[TEST] Fecha sozinho em {args.seconds:.0f}s (ou Ctrl+C).")

    # Salva preview estatico
    try:
        img = kb.renderer.render_to_image()
        out = os.path.join(_ROOT, "docs", "keyboard_preview.png")
        img.save(out)
        print(f"[TEST] Preview salvo em {out}")
    except Exception as e:
        print(f"[TEST] Falha ao salvar preview: {e}")

    # Loop: simula um "dedo" varrendo as teclas em circulo + ripples
    rects = kb.renderer._rects
    t0 = time.perf_counter()
    last_press = 0.0
    press_i = 0
    try:
        while time.perf_counter() - t0 < args.seconds:
            t = time.perf_counter() - t0
            if rects:
                # Varre indices ao longo do tempo
                idx = int((t * 4) % len(rects))
                target = rects[idx]
                fx, fy = target.cx, target.cy
                # Atualiza hover via controller (sem pinch)
                kb.controller.on_frame((fx, fy), pinch_now=False)
                # Dispara um "press" simulado a cada 0.8s pra ver ripple
                if t - last_press > 0.8:
                    last_press = t
                    ks = kb.state.keys.get(target.key.code)
                    if ks:
                        ks.ripple_t = time.perf_counter()
                    press_i += 1
            kb.pump()
            time.sleep(1.0 / 60.0)
    except KeyboardInterrupt:
        pass
    finally:
        kb.close()
        print("[TEST] Fechado. OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
