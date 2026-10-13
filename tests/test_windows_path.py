"""Testes do ajuste de PATH no Windows (core.windows_path).

Roda em qualquer SO: o registro do Windows e' substituido por fakes.
"""

from __future__ import annotations

import pytest

from core.windows_path import (
    OPT_OUT_ENV,
    SCRIPT_NAME,
    append_to_path,
    ensure_scripts_on_path,
    path_contains,
)


SCRIPTS = r"C:\Users\Joao\AppData\Local\Python\pythoncore-3.14-64\Scripts"


class FakeRegistry:
    def __init__(self, value: str = r"C:\Tools", kind: int = 2) -> None:
        self.value = value
        self.kind = kind
        self.writes: list[tuple[str, int]] = []

    def read(self) -> tuple[str, int]:
        return self.value, self.kind

    def write(self, value: str, kind: int) -> None:
        self.writes.append((value, kind))
        self.value = value


@pytest.fixture
def scripts_dir(tmp_path):
    (tmp_path / SCRIPT_NAME).write_bytes(b"")
    return str(tmp_path)


def _run(scripts_dir, reg, **overrides):
    kwargs = {
        "platform": "win32",
        "environ": {"PATH": r"C:\Windows\System32"},
        "in_venv": False,
        "candidates": [None, scripts_dir],
        "read_user_path": reg.read,
        "write_user_path": reg.write,
    }
    kwargs.update(overrides)
    return ensure_scripts_on_path(**kwargs)


# ---------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "path_value",
    [
        SCRIPTS,
        SCRIPTS.lower(),
        SCRIPTS + "\\",
        f'"{SCRIPTS}"',
        SCRIPTS.replace("\\", "/"),
        rf"C:\Windows;;{SCRIPTS};C:\Tools",
    ],
)
def test_path_contains_ignora_caixa_barras_e_aspas(path_value):
    assert path_contains(path_value, SCRIPTS)


def test_path_contains_nao_confunde_prefixo():
    assert not path_contains(SCRIPTS + "Old", SCRIPTS)
    assert not path_contains("", SCRIPTS)


def test_append_descarta_entradas_vazias():
    assert append_to_path(r"C:\A;;C:\B;", r"C:\S") == r"C:\A;C:\B;C:\S"
    assert append_to_path("", r"C:\S") == r"C:\S"


# ---------------------------------------------------------------------
# ensure_scripts_on_path
# ---------------------------------------------------------------------

def test_adiciona_pasta_ao_path_do_usuario(scripts_dir):
    reg = FakeRegistry(r"C:\Tools", kind=1)
    assert _run(scripts_dir, reg) == scripts_dir
    assert reg.writes == [(rf"C:\Tools;{scripts_dir}", 1)]  # preserva o tipo


def test_path_do_usuario_vazio(scripts_dir):
    reg = FakeRegistry("")
    _run(scripts_dir, reg)
    assert reg.writes == [(scripts_dir, 2)]


def test_nao_mexe_se_ja_esta_no_path_do_processo(scripts_dir):
    reg = FakeRegistry()
    environ = {"PATH": rf"C:\Windows;{scripts_dir}"}
    assert _run(scripts_dir, reg, environ=environ) is None
    assert reg.writes == []


def test_nao_duplica_se_ja_esta_no_registro(scripts_dir):
    reg = FakeRegistry(rf"C:\Tools;{scripts_dir}")
    assert _run(scripts_dir, reg) is None
    assert reg.writes == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"platform": "linux"},
        {"platform": "darwin"},
        {"in_venv": True},
        {"environ": {"PATH": "", OPT_OUT_ENV: "1"}},
    ],
)
def test_nao_faz_nada_fora_do_caso_alvo(scripts_dir, overrides):
    reg = FakeRegistry()
    assert _run(scripts_dir, reg, **overrides) is None
    assert reg.writes == []


def test_sem_avmc_exe_nao_faz_nada(tmp_path):
    reg = FakeRegistry()
    assert _run(str(tmp_path), reg) is None
    assert reg.writes == []
