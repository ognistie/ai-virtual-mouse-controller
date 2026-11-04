"""
core.camera_select
==================

Escolha de qual webcam usar, sem editar arquivo nenhum.

Problema:
- Notebook com webcam interna + camera USB: o OpenCV numera os
  dispositivos (0, 1, ...) e o Windows nao tem um "padrao" que ele
  respeite. A interna quase sempre e' a 0.
- Quem instalou pelo pip nao tem um config.py facil de editar.

Solucao (este modulo):
- `avmc --list-cameras` mostra os indices que abrem e a resolucao de cada.
- `avmc --camera N` usa a camera N e LEMBRA a escolha (arquivo de
  preferencias do usuario). Nas proximas vezes, so `avmc`.
- Variavel de ambiente AVMC_CAMERA=N tem prioridade sobre o salvo.
- Ordem: --camera > AVMC_CAMERA > salvo > config.CAMERA_INDEX.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CAMERA_ENV = "AVMC_CAMERA"
_PREFS_KEY = "camera_index"


@dataclass(frozen=True)
class CameraInfo:
    index: int
    width: int
    height: int


def prefs_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """Arquivo de preferencias do usuario (fora do site-packages)."""
    env = os.environ if env is None else env
    if sys.platform == "win32" and env.get("APPDATA"):
        base = Path(env["APPDATA"])
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "avmc" / "settings.json"


def _read_prefs(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_saved_camera(path: Optional[Path] = None) -> Optional[int]:
    value = _read_prefs(path or prefs_path()).get(_PREFS_KEY)
    return value if isinstance(value, int) and value >= 0 else None


def save_camera(index: int, path: Optional[Path] = None) -> Path:
    path = path or prefs_path()
    data = _read_prefs(path)
    data[_PREFS_KEY] = int(index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def resolve_camera_index(
    cli_index: Optional[int],
    default: int,
    env: Optional[Mapping[str, str]] = None,
    path: Optional[Path] = None,
) -> int:
    """Indice final: --camera > AVMC_CAMERA > salvo > default."""
    if cli_index is not None:
        return cli_index
    env = os.environ if env is None else env
    raw = env.get(CAMERA_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
        logger.warning("%s=%r invalido, ignorando", CAMERA_ENV, raw)
    saved = load_saved_camera(path)
    return default if saved is None else saved


def list_cameras(max_index: int = 6) -> list[CameraInfo]:
    """Testa os indices 0..max_index-1 e devolve os que entregam imagem."""
    import cv2

    # Indices vazios geram WARN do OpenCV no terminal — ruido aqui.
    with _quiet_opencv(cv2):
        return _probe(cv2, max_index)


class _quiet_opencv:
    def __init__(self, cv2) -> None:
        self._log = getattr(getattr(cv2, "utils", None), "logging", None)
        self._prev = None

    def __enter__(self) -> None:
        if self._log is not None:
            try:
                self._prev = self._log.getLogLevel()
                self._log.setLogLevel(self._log.LOG_LEVEL_SILENT)
            except Exception:
                self._log = None

    def __exit__(self, *exc) -> None:
        if self._log is not None and self._prev is not None:
            self._log.setLogLevel(self._prev)


def _probe(cv2, max_index: int) -> list[CameraInfo]:
    found: list[CameraInfo] = []
    for index in range(max_index):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        # No Windows o DirectShow ja enxerga todas (e e' o backend que a
        # ThreadedCamera usa); o fallback la so poluiria o terminal.
        if not cap.isOpened() and sys.platform != "win32":
            cap.release()
            cap = cv2.VideoCapture(index)
        try:
            if cap.isOpened() and cap.read()[0]:
                found.append(CameraInfo(
                    index=index,
                    width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                ))
        finally:
            cap.release()
    return found
