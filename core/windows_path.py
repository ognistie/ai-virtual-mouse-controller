"""
core.windows_path
=================

Garante que o comando `avmc` funcione no Windows depois do `pip install`.

Problema:
- O pip instala `avmc.exe` na pasta `Scripts` do Python.
- O instalador novo do Python pra Windows (Python install manager, padrao
  a partir do 3.14) nao coloca essa pasta no PATH -> "avmc nao e'
  reconhecido como nome de cmdlet".
- O pip nao executa codigo do pacote durante a instalacao, entao nao da
  pra corrigir no `pip install`.

Solucao (este modulo):
- Na primeira execucao (`py -m avmc` ou o caminho completo do avmc.exe),
  se a pasta `Scripts` que contem o `avmc.exe` nao estiver no PATH, ela e'
  adicionada ao PATH do USUARIO (HKCU\\Environment) — nunca ao do sistema,
  sem precisar de admin. Terminais abertos depois disso reconhecem `avmc`.
- Nao faz nada fora do Windows, dentro de venv (o activate ja cuida do
  PATH) ou com a variavel de ambiente AVMC_NO_PATH_SETUP=1.
"""

from __future__ import annotations

import contextlib
import ntpath
import os
import sys
import sysconfig
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

SCRIPT_NAME = "avmc.exe"
OPT_OUT_ENV = "AVMC_NO_PATH_SETUP"

# winreg.REG_EXPAND_SZ — valor default do Path do usuario no Windows.
_REG_EXPAND_SZ = 2

ReadUserPath = Callable[[], tuple[str, int]]
WriteUserPath = Callable[[str, int], None]


def _norm(entry: str) -> str:
    """Normaliza uma entrada do PATH pra comparacao (case/barras/%VAR%)."""
    expanded = ntpath.expandvars(entry.strip().strip('"'))
    return ntpath.normcase(ntpath.normpath(expanded)).rstrip("\\")


def path_contains(path_value: str, directory: str) -> bool:
    """True se `directory` ja e' uma das entradas de `path_value` (formato Windows)."""
    target = _norm(directory)
    return any(_norm(p) == target for p in path_value.split(";") if p.strip())


def append_to_path(path_value: str, directory: str) -> str:
    """Acrescenta `directory` ao fim do PATH, descartando entradas vazias."""
    parts = [p for p in path_value.split(";") if p.strip()]
    parts.append(directory)
    return ";".join(parts)


def find_scripts_dir(candidates: Iterable[str | None]) -> str | None:
    """Primeira pasta candidata que contem o avmc.exe."""
    for directory in candidates:
        if directory and (Path(directory) / SCRIPT_NAME).is_file():
            return directory
    return None


def _candidate_dirs() -> list[str | None]:
    dirs: list[str | None] = [sysconfig.get_path("scripts")]
    with contextlib.suppress(KeyError):  # pip install --user
        dirs.append(sysconfig.get_path("scripts", f"{os.name}_user"))
    return dirs


def _read_user_path() -> tuple[str, int]:
    if sys.platform != "win32":
        raise OSError("disponivel so no Windows")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return "", _REG_EXPAND_SZ
    return str(value), int(kind)


def _write_user_path(value: str, kind: int) -> None:
    if sys.platform != "win32":
        raise OSError("disponivel so no Windows")
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "Path", 0, kind, value)
    # O PATH ja foi gravado; o aviso so acelera a propagacao. Falhar aqui
    # nao deve esconder do usuario que a pasta foi adicionada.
    with contextlib.suppress(OSError, AttributeError):
        _broadcast_environment_change()


def _broadcast_environment_change() -> None:
    """Avisa o Explorer que o ambiente mudou (terminais novos herdam o PATH)."""
    if sys.platform != "win32":
        raise OSError("disponivel so no Windows")
    import ctypes
    from ctypes import wintypes

    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x001A
    smto_abortifhung = 0x0002
    send = ctypes.windll.user32.SendMessageTimeoutW
    # argtypes explicitos: sem eles o ctypes passa int de 32 bits onde a
    # API espera HWND/WPARAM de 64 bits.
    send.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    send.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    send(
        hwnd_broadcast, wm_settingchange, 0, "Environment",
        smto_abortifhung, 5000, ctypes.byref(result),
    )


def ensure_scripts_on_path(
    *,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
    in_venv: bool = sys.prefix != sys.base_prefix,
    candidates: Iterable[str | None] | None = None,
    read_user_path: ReadUserPath = _read_user_path,
    write_user_path: WriteUserPath = _write_user_path,
) -> str | None:
    """
    Adiciona a pasta do avmc.exe ao PATH do usuario, se preciso.

    Returns:
        A pasta adicionada, ou None se nada foi alterado.
    """
    if platform != "win32" or in_venv or environ.get(OPT_OUT_ENV):
        return None

    scripts_dir = find_scripts_dir(_candidate_dirs() if candidates is None else candidates)
    if scripts_dir is None:
        return None
    if path_contains(environ.get("PATH", ""), scripts_dir):
        return None

    user_path, kind = read_user_path()
    if path_contains(user_path, scripts_dir):
        # Ja configurado; so este terminal e' anterior a mudanca.
        return None

    write_user_path(append_to_path(user_path, scripts_dir), kind)
    return scripts_dir
