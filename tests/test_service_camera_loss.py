"""VirtualMouseService sem frame novo: tick ocioso e webcam perdida.

Monta o service sem __init__ (sem janela, Qt ou webcam) — so os
colaboradores que o caminho ocioso usa.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from services.virtual_mouse_service import VirtualMouseService


def _service(stalled: bool, silent_for: float = 0.0) -> VirtualMouseService:
    import time

    svc = VirtualMouseService.__new__(VirtualMouseService)
    svc.camera = MagicMock()
    svc.camera.read_new.return_value = (None, 7)
    svc.camera.is_stalled = stalled
    svc.cursor = MagicMock()
    svc.gesture_detector = MagicMock()
    svc.smoother = MagicMock()
    svc._presentation = MagicMock()
    svc.hologram = None
    svc.settings_panel = MagicMock()
    svc.enable_preview = False
    svc._perf = MagicMock()
    svc._frame_seq = 7
    svc._last_frame_t = time.perf_counter() - silent_for
    svc._camera_lost = False
    svc._lost_frame = None
    return svc


def test_waiting_for_next_frame_only_pumps_ui():
    svc = _service(stalled=False)
    svc._tick()
    svc.settings_panel.pump.assert_called_once()
    svc.cursor.force_release.assert_not_called()
    svc.gesture_detector.update.assert_not_called()
    assert not svc._camera_lost


def test_stalled_camera_releases_mouse_once():
    svc = _service(stalled=True)
    svc._tick()
    svc._tick()
    assert svc._camera_lost
    svc.cursor.force_release.assert_called_once()
    svc.gesture_detector.reset.assert_called_once()
    svc.smoother.reset.assert_called_once()


def test_silent_camera_counts_as_lost_even_without_driver_error():
    svc = _service(stalled=False, silent_for=VirtualMouseService._CAMERA_LOST_AFTER_S + 1)
    svc._tick()
    assert svc._camera_lost
    svc.cursor.force_release.assert_called_once()
