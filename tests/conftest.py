"""Pytest config global da suite.

Adiciona flag `--gpu` pra controlar testes marcados com `@pytest.mark.gpu`
(overlay PySide6/ModernGL). Sem a flag, esses testes sao pulados — eles
podem crashar com access violation em ambientes sem display/driver real.

Uso:
    pytest                # roda tudo exceto gpu (default)
    pytest --gpu          # inclui gpu
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--gpu",
        action="store_true",
        default=False,
        help="Roda testes marcados com @pytest.mark.gpu (precisam de display/driver real).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--gpu"):
        return
    skip_gpu = pytest.mark.skip(reason="precisa --gpu (display/driver real)")
    for item in items:
        if "gpu" in item.keywords:
            item.add_marker(skip_gpu)
