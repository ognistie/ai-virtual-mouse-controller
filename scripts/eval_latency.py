"""
scripts/eval_latency.py
=======================

Latencia real do pipeline completo: quadro da camera -> movimento do cursor.

Roda o VirtualMouseService de verdade (janela de preview e holograma reais)
alimentado por um video a 30 fps. So a camera e o mouse sao falsos: o cursor
do sistema nao se move e nada e' clicado.

    python scripts/eval_latency.py --seconds 15
    python scripts/eval_latency.py --hologram --two-hands --json saida.json

Relata: quadros processados por segundo, quadros perdidos e latencia
camera -> cursor. O resultado varia com a carga da maquina: compare rodadas
feitas em sequencia, nao numeros de dias diferentes.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

DEFAULT_VIDEO = ROOT / "docs" / "assets" / "video" / "avmc-demo.mp4"


def load_hand_frames(video: Path, count: int = 150, cache: Optional[Path] = None) -> list:
    """Quadros do video em que ha mao (espelhados, como a camera entrega)."""
    if cache is not None and cache.is_file():
        return list(np.load(cache))
    from core.hand_tracker import HandTracker

    tracker = HandTracker(max_num_hands=1, min_detection_confidence=0.6,
                          min_tracking_confidence=0.4)
    cap = cv2.VideoCapture(str(video))
    frames: list = []
    while len(frames) < count:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.resize(frame, (960, 540))[:, ::-1].copy()
        if tracker.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)):
            frames.append(frame)
    cap.release()
    tracker.close()
    if not frames:
        raise SystemExit(f"Nenhuma mao encontrada em {video}")
    if cache is not None:
        np.save(cache, np.stack(frames))
    return frames


class ReplayCamera:
    """Imita ThreadedCamera: entrega o quadro "mais novo" a 30 fps."""

    is_stalled = False
    FPS = 30.0

    def __init__(self, frames: list) -> None:
        self.frames = frames
        self.index = 0
        self.t0 = 0.0
        self.stamps: dict = {}
        self.taken = 0

    def open(self) -> "ReplayCamera":
        self.t0 = time.perf_counter()
        return self

    def close(self) -> None:
        pass

    def read(self, mirror: bool = True):
        return self.read_new(-1, 0.0, mirror)[0]

    def read_new(self, last_seq: int, timeout: float = 0.1, mirror: bool = True):
        deadline = time.perf_counter() + timeout
        while True:
            now = time.perf_counter()
            seq = int((now - self.t0) * self.FPS)
            if seq != last_seq:
                self.taken += 1
                self.stamps[seq] = self.t0 + seq / self.FPS
                self.current_seq = seq
                return self.frames[seq % len(self.frames)].copy(), seq
            if now >= deadline:
                return None, last_seq
            time.sleep(0.001)


def run_trial(frames: list, seconds: float, hologram: bool, two_hands: bool) -> dict:
    import config
    from core import cursor_controller as cc
    from services import virtual_mouse_service as vms

    camera = ReplayCamera(frames)
    latencies: list = []
    clicks = [0]
    gui = mock.MagicMock()
    gui.size.return_value = (1920, 1200)
    gui.position.return_value = (960, 600)

    def on_move(*_a, **_k):
        stamp = camera.stamps.get(getattr(camera, "current_seq", -1))
        if stamp is not None:
            latencies.append((time.perf_counter() - stamp) * 1000.0)

    def on_click(*_a, **_k):
        clicks[0] += 1

    gui.moveTo.side_effect = on_move
    gui.click.side_effect = on_click

    with mock.patch.object(cc, "pyautogui", gui), \
            mock.patch.object(vms, "ThreadedCamera", lambda **_k: camera), \
            mock.patch.object(vms, "_open_camera", lambda cam, _d: cam.open()), \
            mock.patch.object(config, "PERF_TELEMETRY_REPORT_EVERY", 10 ** 9):
        service = vms.VirtualMouseService.from_config(camera_index=0)
        if service.hologram is not None and service.hologram.available:
            service.hologram.set_enabled(hologram)
        if two_hands:
            service._set_two_hands_mode(True)
        camera.open()
        timer = threading.Timer(seconds, service.stop)
        timer.start()
        t0 = time.perf_counter()
        code = service.run()
        elapsed = time.perf_counter() - t0
        timer.cancel()

    stages = service._perf.snapshot()
    latencies = latencies[len(latencies) // 10:] or [float("nan")]
    latencies.sort()
    return {
        "exit": code,
        "fps": round(camera.taken / elapsed, 1),
        "lost_pct": round(100.0 * (1.0 - camera.taken / (elapsed * camera.FPS)), 1),
        "lat_p50": round(statistics.median(latencies), 1),
        "lat_p95": round(latencies[int(len(latencies) * 0.95) - 1], 1),
        "clicks": clicks[0],
        "stages": {k: (round(v["p50"], 1), round(v["p99"], 1)) for k, v in stages.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--hologram", action="store_true")
    ap.add_argument("--two-hands", action="store_true")
    ap.add_argument("--sync", action="store_true", help="visao e UI na mesma thread")
    ap.add_argument("--legacy", action="store_true", help="como antes da visao em thread e dos filtros")
    ap.add_argument("--preview-fps", type=float, default=None, help="limite do preview")
    ap.add_argument("--frames-cache", type=Path, default=None, help="arquivo .npy dos quadros")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    if args.sync:
        import config

        config.VISION_THREAD_ENABLED = False
    if args.legacy or args.preview_fps is not None:
        import config

        if args.legacy:
            config.VISION_THREAD_ENABLED = False
            config.POWER_THROTTLING_OFF = False
            config.OPENCV_SINGLE_THREAD = False
            config.HAND_CONFIRM_FRAMES = 1
            config.PREVIEW_MAX_FPS = 0.0
        if args.preview_fps is not None:
            config.PREVIEW_MAX_FPS = args.preview_fps
    frames = load_hand_frames(args.video, cache=args.frames_cache)
    result = run_trial(frames, args.seconds, args.hologram, args.two_hands)
    print(json.dumps(result))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
