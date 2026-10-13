"""
scripts/render_hand_preview.py
==============================

Renderiza a mao 3D do holograma OFFSCREEN (sem janela, sem webcam) e
salva um PNG — pra iterar no visual e conferir fidelidade da pose.

Fontes de pose:
- maos sinteticas (aberta / pinca) — sempre;
- landmarks reais (`--landmarks arquivo.json`, gerado com
  `--extract video.mp4`): cada celula mostra o frame real da camera ao
  lado da mao 3D.

Uso:
    python scripts/render_hand_preview.py --out preview.png
    python scripts/render_hand_preview.py --extract docs/assets/video/avmc-demo.mp4 \
        --landmarks lm.json
    python scripts/render_hand_preview.py --landmarks lm.json --out preview.png

Usa o mesmo renderer OpenGL (via Qt) da janela do holograma; precisa de
OpenGL 3.3 e de uma sessao de desktop. --qpainter usa o fallback 2D.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.hand_mesh import build_hand_mesh  # noqa: E402
from core.hand_pose import HandPoseSolver  # noqa: E402
from tests.synthetic_hand import make_hand  # noqa: E402

CELL = 420
BACKGROUNDS = ((0.23, 0.23, 0.24), (0.86, 0.88, 0.91))


def extract(video: Path, out: Path) -> None:
    import cv2

    from core.gesture_detector import classify_shape
    from core.hand_tracker import HandTracker

    cap = cv2.VideoCapture(str(video))
    tracker = HandTracker()
    frames = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.flip(frame, 1)
        hands = tracker.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if hands:
            shape = classify_shape(hands[0], 0.075, 0.075, 0.09).name
            frames.append({"i": i, "shape": shape, "lm": hands[0].landmarks})
        i += 1
    tracker.close()
    out.write_text(
        json.dumps(
            {
                "video": str(video),
                "w": cap.get(cv2.CAP_PROP_FRAME_WIDTH),
                "h": cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                "frames": frames,
            }
        ),
        encoding="utf-8",
    )
    print(f"{len(frames)} frames com mao -> {out}")


def _pick_real(data: dict, per_shape: int) -> List[dict]:
    picks: List[dict] = []
    for shape in ("OPEN_HAND", "PINCH", "PINCH_MIDDLE", "OTHER", "FIST"):
        group = [f for f in data["frames"] if f["shape"] == shape]
        if not group:
            continue
        step = max(1, len(group) // per_shape)
        picks.extend(group[::step][:per_shape])
    return picks


def _camera_crop(video: str, index: int, lm, size: int) -> Optional[np.ndarray]:
    try:
        import cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    frame = cv2.flip(frame, 1)
    h, w = frame.shape[:2]
    xs = [p[0] * w for p in lm]
    ys = [p[1] * h for p in lm]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.65
    x0, y0 = int(max(0, cx - half)), int(max(0, cy - half))
    x1, y1 = int(min(w, cx + half)), int(min(h, cy + half))
    crop = cv2.resize(frame[y0:y1, x0:x1], (size, size))
    return crop[:, :, ::-1].astype(np.float32) / 255.0


def _qimage_to_rgba(img) -> np.ndarray:
    from PySide6.QtGui import QImage

    img = img.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h)
    return buf.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4).astype(np.float32) / 255.0


def render_gl(poses: List[Tuple[str, list]], aspect: float, out_size=CELL) -> List[np.ndarray]:
    """Mesmo renderer da janela do holograma (OpenGL do Qt), offscreen."""
    from PySide6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    from PySide6.QtOpenGL import (
        QOpenGLFramebufferObject,
        QOpenGLFramebufferObjectFormat,
    )

    from core.hand_gl_renderer import HandGLRenderer, clear_framebuffer

    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    ctx = QOpenGLContext()
    ctx.setFormat(fmt)
    if not ctx.create():
        raise RuntimeError("OpenGL 3.3 indisponivel")
    surface = QOffscreenSurface()
    surface.setFormat(ctx.format())
    surface.create()
    ctx.makeCurrent(surface)
    ffmt = QOpenGLFramebufferObjectFormat()
    ffmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
    ffmt.setSamples(4)
    w = h = out_size
    fbo = QOpenGLFramebufferObject(w, h, ffmt)
    renderer = HandGLRenderer()
    if not renderer.initialize():
        raise RuntimeError(f"shader falhou: {getattr(renderer, '_last_error', '')}")
    images = []
    for _, lm in poses:
        solver = HandPoseSolver(hand_size_px=out_size * 0.62, frame_aspect=aspect)
        posed = solver.solve(lm, w * 0.42, h * 0.40, now=0.0)
        mesh = build_hand_mesh(posed)
        fbo.bind()
        ctx.functions().glViewport(0, 0, w, h)
        clear_framebuffer()
        renderer.render(mesh, posed, w, h)
        fbo.release()
        images.append(_qimage_to_rgba(fbo.toImage()))
    renderer.release()
    ctx.doneCurrent()
    return images


def render_qpainter(
    poses: List[Tuple[str, list]], aspect: float, out_size=CELL
) -> List[np.ndarray]:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from core.hand_soft_renderer import SoftHandRenderer

    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841
    renderer = SoftHandRenderer()
    images = []
    for _, lm in poses:
        solver = HandPoseSolver(hand_size_px=out_size * 0.62, frame_aspect=aspect)
        posed = solver.solve(lm, out_size * 0.42, out_size * 0.40, now=0.0)
        img = QImage(out_size, out_size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        painter = QPainter(img)
        renderer.paint(painter, posed)
        painter.end()
        images.append(_qimage_to_rgba(img))
    return images


def _over(rgba: np.ndarray, bg) -> np.ndarray:
    a = rgba[:, :, 3:4]
    return rgba[:, :, :3] + np.asarray(bg, np.float32)[None, None, :] * (1.0 - a)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Preview offscreen da mao 3D")
    ap.add_argument("--out", type=Path, default=Path("hand_preview.png"))
    ap.add_argument("--landmarks", type=Path, default=None)
    ap.add_argument("--extract", type=Path, default=None)
    ap.add_argument("--per-shape", type=int, default=2)
    ap.add_argument("--qpainter", action="store_true", help="usa o renderer 2D")
    args = ap.parse_args(argv)

    if args.extract:
        extract(args.extract, args.landmarks or Path("landmarks.json"))
        return 0

    poses: List[Tuple[str, list]] = [
        ("sint aberta", make_hand(0.5, 0.5).landmarks),
        ("sint pinca", make_hand(0.5, 0.5, pinch=1.0).landmarks),
    ]
    crops: List[Optional[np.ndarray]] = [None, None]
    aspect = 16 / 9
    if args.landmarks:
        data = json.loads(args.landmarks.read_text(encoding="utf-8"))
        aspect = float(data["w"]) / float(data["h"])
        for f in _pick_real(data, args.per_shape):
            poses.append((f"{f['shape']} #{f['i']}", f["lm"]))
            crops.append(_camera_crop(data.get("video", ""), f["i"], f["lm"], CELL))

    render = render_qpainter if args.qpainter else render_gl
    images = render(poses, aspect)

    import cv2

    rows = []
    for img, crop in zip(images, crops):
        cells = [_over(img, bg) for bg in BACKGROUNDS]
        if crop is not None:
            cells.insert(0, crop)
        else:
            cells.insert(0, np.full((CELL, CELL, 3), 0.1, np.float32))
        rows.append(np.hstack(cells))
    sheet = np.vstack(rows)
    out = (np.clip(sheet, 0, 1) * 255).astype(np.uint8)[:, :, ::-1]
    cv2.imwrite(str(args.out), out)
    print(f"preview -> {args.out}  ({len(images)} poses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
