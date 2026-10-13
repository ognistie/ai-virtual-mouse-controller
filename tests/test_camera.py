"""ThreadedCamera com VideoCapture falso: frames novos, timeout,
webcam caindo e reconectando sozinha."""

from __future__ import annotations

import threading
import time
from typing import ClassVar, List

import numpy as np
import pytest

from core import camera as camera_mod
from core.camera import CameraOpenError, ThreadedCamera


class _FakeCapture:
    """Imita cv2.VideoCapture. `fail` liga/desliga a entrega de frames."""

    instances: ClassVar[List["_FakeCapture"]] = []
    available: ClassVar[bool] = True

    def __init__(self, *args, **kwargs) -> None:
        self.opened = _FakeCapture.available
        self.released = False
        self.n = 0
        self.fail = threading.Event()
        _FakeCapture.instances.append(self)

    def isOpened(self) -> bool:
        return self.opened and not self.released

    def set(self, *args) -> bool:
        return True

    def read(self):
        time.sleep(0.005)
        if self.fail.is_set() or self.released:
            return False, None
        self.n += 1
        return True, np.full((4, 6, 3), self.n % 255, dtype=np.uint8)

    def release(self) -> None:
        self.released = True


@pytest.fixture
def fake_cv(monkeypatch):
    _FakeCapture.instances = []
    _FakeCapture.available = True
    monkeypatch.setattr(camera_mod.cv2, "VideoCapture", _FakeCapture)
    monkeypatch.setattr(ThreadedCamera, "_REOPEN_INTERVAL_S", 0.02)
    monkeypatch.setattr(ThreadedCamera, "_MAX_FAILURES", 3)
    return _FakeCapture


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_open_raises_when_no_device(fake_cv):
    fake_cv.available = False
    with pytest.raises(CameraOpenError):
        ThreadedCamera(warmup_seconds=0).open()


def test_read_new_returns_each_frame_once(fake_cv):
    with ThreadedCamera(warmup_seconds=0) as cam:
        frame, seq = cam.read_new(0, timeout=1.0)
        assert frame is not None and seq > 0
        frame2, seq2 = cam.read_new(seq, timeout=1.0)
        assert frame2 is not None and seq2 > seq


def test_read_new_times_out_without_new_frame(fake_cv, monkeypatch):
    # Falhas curtas (sem chegar a reconectar): so falta frame novo.
    monkeypatch.setattr(ThreadedCamera, "_MAX_FAILURES", 10_000)
    with ThreadedCamera(warmup_seconds=0) as cam:
        _, seq = cam.read_new(0, timeout=1.0)
        fake_cv.instances[-1].fail.set()
        time.sleep(0.02)  # frame em voo termina
        _, seq = cam.read_new(seq, timeout=0.05)
        t0 = time.perf_counter()
        frame, same = cam.read_new(seq, timeout=0.05)
        assert frame is None and same == seq
        # Esperou (nao girou em falso). Folga pra granularidade de timer.
        assert time.perf_counter() - t0 >= 0.03


def test_stalled_camera_drops_frame_and_reconnects(fake_cv):
    with ThreadedCamera(warmup_seconds=0) as cam:
        assert cam.read_new(0, timeout=1.0)[0] is not None
        first = fake_cv.instances[-1]
        first.fail.set()
        assert _wait(lambda: cam.is_stalled)
        # Sem imagem congelada se passando por ao vivo.
        assert cam.read(mirror=False) is None
        assert first.released
        # Thread reabre um novo dispositivo sozinho.
        assert _wait(lambda: not cam.is_stalled)
        assert len(fake_cv.instances) >= 2
        assert cam.read_new(0, timeout=1.0)[0] is not None


def test_close_stops_reconnect_loop(fake_cv):
    cam = ThreadedCamera(warmup_seconds=0).open()
    fake_cv.available = False
    fake_cv.instances[-1].fail.set()
    assert _wait(lambda: cam.is_stalled)
    t0 = time.perf_counter()
    cam.close()
    assert time.perf_counter() - t0 < 1.5
    assert cam._thread is None
