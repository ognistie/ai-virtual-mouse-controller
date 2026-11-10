"""Issue #6: no Python 3.15 o MediaPipe nao iniciava depois do PySide6.

"TypeError: 'lazy_import' object is not callable" em
concurrent.futures.ThreadPoolExecutor. Os testes de integracao rodam em
processo separado porque o filtro de lazy imports vale pro processo inteiro.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import core

ROOT = Path(__file__).resolve().parent.parent
PY315 = hasattr(sys, "set_lazy_imports_filter")


def _run(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=120,
    )


# Unidade: funciona em qualquer versao ----------------------------------

def test_sets_eager_filter_when_none_is_set(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "set_lazy_imports_filter", calls.append, raising=False)
    monkeypatch.setattr(sys, "get_lazy_imports_filter", lambda: None, raising=False)
    core._keep_imports_eager()
    assert len(calls) == 1
    assert calls[0]("concurrent.futures", "concurrent.futures.thread", ("ThreadPoolExecutor",)) is False


def test_keeps_a_filter_someone_already_set(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "set_lazy_imports_filter", calls.append, raising=False)
    monkeypatch.setattr(sys, "get_lazy_imports_filter", lambda: (lambda *a: True), raising=False)
    core._keep_imports_eager()
    assert calls == []


def test_resolves_executor_left_lazy(monkeypatch):
    from concurrent.futures.thread import ThreadPoolExecutor

    fake = types.ModuleType("concurrent.futures")
    fake.ThreadPoolExecutor = object()  # como o objeto lazy: nao e' chamavel
    monkeypatch.setitem(sys.modules, "concurrent.futures", fake)
    monkeypatch.setattr(sys, "set_lazy_imports_filter", lambda f: None, raising=False)
    monkeypatch.setattr(sys, "get_lazy_imports_filter", lambda: None, raising=False)
    core._keep_imports_eager()
    assert fake.ThreadPoolExecutor is ThreadPoolExecutor


def test_noop_before_python_315(monkeypatch):
    monkeypatch.delattr(sys, "set_lazy_imports_filter", raising=False)
    core._keep_imports_eager()  # nao levanta


# Integracao: o cenario da issue, em processo novo ----------------------

pyside = pytest.importorskip("PySide6")


def test_executor_is_callable_after_pyside6():
    proc = _run(
        "import core, PySide6.QtWidgets, concurrent.futures as f;"
        "assert callable(f.ThreadPoolExecutor), type(f.ThreadPoolExecutor)"
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


def test_mediapipe_starts_after_pyside6():
    pytest.importorskip("mediapipe")
    proc = _run(
        "import core, PySide6.QtWidgets\n"
        "import numpy as np\n"
        "from core.hand_tracker import HandTracker\n"
        "t = HandTracker()\n"
        "t.process(np.zeros((90, 160, 3), np.uint8))\n"
        "t.close()\n"
        "print('ok')\n"
    )
    assert proc.returncode == 0 and "ok" in proc.stdout, proc.stderr[-2000:]


@pytest.mark.skipif(not PY315, reason="so o Python 3.15+ tem lazy imports")
def test_bug_reproduces_without_the_fix():
    """Controle: sem importar `core` antes, o ambiente mostra o bug da issue.
    Se um dia o PySide6 ou o Python corrigirem, este teste so avisa."""
    proc = _run(
        "import PySide6.QtWidgets, concurrent.futures as f;"
        "print(callable(f.ThreadPoolExecutor))"
    )
    if proc.stdout.strip() == "True":
        pytest.skip("PySide6/Python ja nao reproduzem o bug da issue #6")
    assert proc.stdout.strip() == "False", proc.stderr[-2000:]
