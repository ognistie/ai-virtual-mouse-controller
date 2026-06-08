"""
main.py
=======

Entry point do AI Virtual Mouse Controller.

Uso:
    python main.py

Ou como modulo:
    python -m main

Sai com codigo:
    0 = ok
    1 = falha de webcam
    2 = erro inesperado
"""

from __future__ import annotations

import logging
import sys


def _check_environment() -> None:
    """Verifica versoes criticas ANTES de qualquer import do projeto.

    Mediapipe 0.11+ removeu mp.solutions.hands que esse projeto usa.
    Se o usuario tem 0.11+ instalado globalmente, mostramos mensagem
    clara com comando de fix em vez de AttributeError obscuro.
    """
    try:
        import mediapipe as mp
    except ImportError:
        sys.stderr.write(
            "\nERRO: mediapipe nao esta instalado.\n"
            "Solucao: pip install ai-virtual-mouse-controller\n\n"
        )
        sys.exit(1)

    if not hasattr(mp, "solutions") or not hasattr(mp.solutions, "hands"):
        version = getattr(mp, "__version__", "desconhecida")
        sys.stderr.write(
            "\n" + "=" * 70 + "\n"
            f"ERRO: mediapipe {version} nao tem mp.solutions.hands.\n"
            "Esse projeto requer mediapipe 0.10.18 a 0.10.21 — o modulo\n"
            "'solutions' foi removido a partir da 0.10.30.\n\n"
            "Solucao rapida (copia e cola no terminal):\n\n"
            '  pip install "mediapipe>=0.10.18,<0.10.30" --force-reinstall\n\n'
            "Depois roda 'avmc' de novo.\n"
            + "=" * 70 + "\n\n"
        )
        sys.exit(1)


def _setup_logging() -> None:
    import config
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    # Check ANTES de importar service (que ja tocaria em mediapipe)
    _check_environment()

    _setup_logging()
    logger = logging.getLogger("main")
    logger.info("AI Virtual Mouse Controller — iniciando...")

    from services.virtual_mouse_service import VirtualMouseService
    service = VirtualMouseService.from_config()
    return service.run()


if __name__ == "__main__":
    sys.exit(main())
