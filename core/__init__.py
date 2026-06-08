"""Core components: camera, hand tracking, smoothing, gestures, cursor.

Esta camada concentra a logica de dominio do projeto. Modulos foram
escritos para serem importaveis isoladamente — quem precisa de
`RobustHandAnchor` nao precisa instanciar `HologramOverlay`.

Versao do pacote vive em `__version__` e e' a fonte unica de verdade
(consumida por `pyproject.toml` se quisermos no futuro via dynamic).
"""

from __future__ import annotations

__version__ = "1.0.5"
__all__ = ["__version__"]
