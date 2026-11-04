"""Escolha da webcam: --camera > AVMC_CAMERA > salvo > config,
e fallback pra camera padrao quando a escolhida nao abre."""

from __future__ import annotations

import pytest

import main
from core import camera_select
from core.camera import CameraOpenError
from core.camera_select import (
    CAMERA_ENV,
    load_saved_camera,
    prefs_path,
    resolve_camera_index,
    save_camera,
)


@pytest.fixture
def prefs(tmp_path):
    return tmp_path / "avmc" / "settings.json"


def test_default_when_nothing_set(prefs):
    assert resolve_camera_index(None, 0, env={}, path=prefs) == 0


def test_saved_choice_is_used(prefs):
    save_camera(1, prefs)
    assert load_saved_camera(prefs) == 1
    assert resolve_camera_index(None, 0, env={}, path=prefs) == 1


def test_priority_cli_over_env_over_saved(prefs):
    save_camera(1, prefs)
    env = {CAMERA_ENV: "2"}
    assert resolve_camera_index(None, 0, env=env, path=prefs) == 2
    assert resolve_camera_index(3, 0, env=env, path=prefs) == 3


def test_invalid_env_is_ignored(prefs):
    save_camera(1, prefs)
    assert resolve_camera_index(None, 0, env={CAMERA_ENV: "usb"}, path=prefs) == 1


def test_corrupt_prefs_file_falls_back(prefs):
    prefs.parent.mkdir(parents=True)
    prefs.write_text("{nao e json", encoding="utf-8")
    assert load_saved_camera(prefs) is None
    # Salvar por cima de arquivo quebrado funciona.
    save_camera(1, prefs)
    assert load_saved_camera(prefs) == 1


def test_save_keeps_other_keys(prefs):
    prefs.parent.mkdir(parents=True)
    prefs.write_text('{"outra": true}', encoding="utf-8")
    save_camera(1, prefs)
    assert '"outra": true' in prefs.read_text(encoding="utf-8")


def test_prefs_path_on_windows_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(camera_select.sys, "platform", "win32")
    path = prefs_path({"APPDATA": str(tmp_path)})
    assert path == tmp_path / "avmc" / "settings.json"


def test_cli_camera_is_saved(monkeypatch, prefs):
    monkeypatch.setattr(camera_select, "prefs_path", lambda env=None: prefs)
    monkeypatch.delenv(CAMERA_ENV, raising=False)
    assert main._choose_camera(1) == 1
    assert main._choose_camera(None) == 1


def test_parse_args_rejects_negative_camera():
    with pytest.raises(SystemExit):
        main._parse_args(["--camera", "-1"])
    assert main._parse_args(["--camera", "1"]).camera == 1


class _Cam:
    """ThreadedCamera falsa: so abrem os indices em `working`."""

    def __init__(self, index, working):
        self.index = index
        self.working = working
        self.closed = False

    def open(self):
        if self.index not in self.working:
            raise CameraOpenError(f"index={self.index}")
        return self

    def close(self):
        self.closed = True


def _open_like_service(cam, default):
    # Mesmo trecho de VirtualMouseService.from_config.
    from services import virtual_mouse_service as svc
    return svc._open_camera(cam, default)


def test_unplugged_choice_falls_back_to_default():
    cam = _Cam(1, working={0})
    _open_like_service(cam, default=0)
    assert cam.index == 0


def test_default_camera_failure_still_raises():
    with pytest.raises(CameraOpenError):
        _open_like_service(_Cam(0, working=set()), default=0)
