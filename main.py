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
    """Verifica o mediapipe ANTES de qualquer import do projeto.

    O projeto usa a Tasks API (HandLandmarker). Se o import falhar,
    mostramos mensagem clara com comando de fix em vez de erro obscuro.
    """
    try:
        import mediapipe as mp
    except ImportError:
        sys.stderr.write(
            "\nERRO: mediapipe nao esta instalado.\n"
            "Solucao: pip install ai-virtual-mouse-controller\n\n"
        )
        sys.exit(1)

    try:
        from mediapipe.tasks.python.vision import HandLandmarker  # noqa: F401
    except ImportError as e:
        version = getattr(mp, "__version__", "desconhecida")
        sys.stderr.write(
            "\n" + "=" * 70 + "\n"
            f"ERRO: mediapipe {version} sem HandLandmarker ({e}).\n"
            "Esse projeto requer mediapipe 0.10.18 ou mais novo.\n\n"
            "Solucao rapida (copia e cola no terminal):\n\n"
            '  pip install --upgrade "mediapipe>=0.10.18"\n\n'
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
