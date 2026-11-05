"""Pytest config global da suite.

Adiciona flag `--gpu` pra controlar testes marcados com `@pytest.mark.gpu`
(overlay PySide6/OpenGL). Sem a flag, esses testes sao pulados — eles
podem crashar com access violation em ambientes sem display/driver real.

Uso:
    pytest                # roda tudo exceto gpu (default)
    pytest --gpu          # inclui gpu
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# Sem tela (CI Linux headless): o pyautogui le $DISPLAY ja no import e
# quebra a coleta (KeyError: 'DISPLAY'). Os testes nunca movem o mouse de
# verdade — um stub basta.
try:
    import pyautogui  # noqa: F401
except Exception:
    _fake_pyautogui = MagicMock(name="pyautogui")
    _fake_pyautogui.size.return_value = (1920, 1080)
    _fake_pyautogui.position.return_value = (960, 540)
    sys.modules.setdefault("pyautogui", _fake_pyautogui)


@pytest.fixture(autouse=True)
def _no_real_mouse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhum teste aperta o mouse de verdade: rodar pytest nao pode clicar
    onde o ponteiro estiver. Quem precisa ver as chamadas usa o proprio mock."""
    import pyautogui as gui

    for name in (
        "moveTo", "click", "doubleClick", "rightClick",
        "mouseDown", "mouseUp", "press", "scroll",
    ):
        monkeypatch.setattr(gui, name, MagicMock(name=f"pyautogui.{name}"))


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
