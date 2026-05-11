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

import config
from services.virtual_mouse_service import VirtualMouseService


def _setup_logging() -> None:
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    _setup_logging()
    logger = logging.getLogger("main")
    logger.info("AI Virtual Mouse Controller — iniciando...")

    service = VirtualMouseService.from_config()
    return service.run()


if __name__ == "__main__":
    sys.exit(main())
