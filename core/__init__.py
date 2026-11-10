"""Core components: camera, hand tracking, smoothing, gestures, cursor.

Esta camada concentra a logica de dominio do projeto. Modulos foram
escritos para serem importaveis isoladamente — quem precisa de
`RobustHandAnchor` nao precisa instanciar `HologramOverlay`.

Versao do pacote vive em `__version__` e e' a fonte unica de verdade
(consumida por `pyproject.toml` se quisermos no futuro via dynamic).
"""

from __future__ import annotations

import sys

__version__ = "1.4.0"
__all__ = ["__version__"]


def _keep_imports_eager() -> None:
    """Python 3.15+: imports imediatos, senao o MediaPipe nao inicia com o PySide6.

    No 3.15 a biblioteca padrao usa lazy imports (PEP 810): em
    `concurrent.futures`, `ThreadPoolExecutor` e' `lazy from .thread import`.
    O PySide6 troca o `builtins.__import__`, e dai em diante ler
    `concurrent.futures.ThreadPoolExecutor` devolve o objeto lazy sem
    resolver. O MediaPipe chama esse nome ao criar o HandLandmarker:
    "TypeError: 'lazy_import' object is not callable" (issue #6).

    Este pacote e' importado antes do PySide6 e do MediaPipe em todas as
    entradas do app. Nao mexe em filtro que alguem ja tenha definido.
    """
    # getattr: as funcoes so existem a partir do 3.15.
    set_filter = getattr(sys, "set_lazy_imports_filter", None)
    get_filter = getattr(sys, "get_lazy_imports_filter", None)
    if set_filter is None or get_filter is None:  # Python < 3.15
        return
    if get_filter() is None:
        set_filter(lambda importing, imported, fromlist: False)
    # concurrent.futures ja importado com o nome ainda lazy: resolve direto
    # pelo submodulo, que funciona mesmo com o __import__ do PySide6.
    futures = sys.modules.get("concurrent.futures")
    if futures is not None and not callable(futures.__dict__.get("ThreadPoolExecutor")):
        from concurrent.futures.thread import ThreadPoolExecutor

        futures.ThreadPoolExecutor = ThreadPoolExecutor  # type: ignore[attr-defined]


_keep_imports_eager()
