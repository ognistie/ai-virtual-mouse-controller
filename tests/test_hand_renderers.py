"""Renderers da mao: fallback QPainter (offscreen) e OpenGL (gpu)."""

from __future__ import annotations

import os

import pytest

from core.hand_gl_renderer import STYLE_PRESETS, hex_to_rgb, ortho_projection
from core.hand_pose import HandPoseSolver
from core.hand_soft_renderer import paint_parts_order
from tests.synthetic_hand import make_hand

try:
    import PySide6  # noqa: F401

    _PYSIDE = True
except ImportError:  # pragma: no cover
    _PYSIDE = False


def _posed(pinch=0.0):
    s = HandPoseSolver(hand_size_px=200, frame_aspect=16 / 9)
    return s.solve(make_hand(0.5, 0.5, pinch=pinch).landmarks, 200, 160, now=0.0)


def test_styles_and_helpers():
    assert {"quest", "graphite", "neon"} <= set(STYLE_PRESETS)
    assert hex_to_rgb("#ff8000") == pytest.approx((1.0, 128 / 255, 0.0))
    m = ortho_projection(100, 50).T  # row-major
    assert m[0] @ [100, 0, 0, 1] == pytest.approx(1.0)
    assert m[1] @ [0, 50, 0, 1] == pytest.approx(-1.0)
    # mais perto de quem olha (z maior) -> menor profundidade no depth test
    assert (m[2] @ [0, 0, 10, 1]) < (m[2] @ [0, 0, -10, 1])


def test_open_hand_draws_palm_before_fingers():
    order = paint_parts_order(_posed())
    assert order[0] == "palm" or order.index("palm") < order.index("middle")


@pytest.mark.skipif(not _PYSIDE, reason="PySide6 nao instalado")
def test_soft_renderer_paints_offscreen():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from core.hand_soft_renderer import SoftHandRenderer

    app = QApplication.instance() or QApplication([])  # noqa: F841
    img = QImage(420, 420, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    SoftHandRenderer().paint(painter, _posed(pinch=1.0))
    painter.end()
    # Pinta alguma coisa perto do ponto da pinca (o cursor).
    alpha = img.pixelColor(200, 160).alpha()
    assert alpha > 30


@pytest.mark.gpu
@pytest.mark.skipif(not _PYSIDE, reason="PySide6 nao instalado")
def test_gl_renderer_draws_hand():
    """Shader compila e desenha (precisa OpenGL 3.3 real)."""
    from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
    from PySide6.QtWidgets import QApplication
    from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat

    from core.hand_gl_renderer import HandGLRenderer, clear_framebuffer
    from core.hand_mesh import build_hand_mesh
    from core.hologram_gl_backend import _surface_format

    app = QApplication.instance() or QApplication([])  # noqa: F841
    ctx = QOpenGLContext()
    ctx.setFormat(_surface_format())
    if not ctx.create():
        pytest.skip("sem OpenGL 3.3 (ou app Qt ja criado em modo offscreen)")
    surface = QOffscreenSurface()
    surface.setFormat(ctx.format())
    surface.create()
    assert ctx.makeCurrent(surface)
    fmt = QOpenGLFramebufferObjectFormat()
    fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
    fbo = QOpenGLFramebufferObject(420, 420, fmt)
    fbo.bind()
    ctx.functions().glViewport(0, 0, 420, 420)
    renderer = HandGLRenderer()
    assert renderer.initialize()
    clear_framebuffer()
    posed = _posed()
    renderer.render(build_hand_mesh(posed), posed, 420, 420)
    fbo.release()
    img = fbo.toImage()
    palm = posed.points[[0, 5, 9, 13, 17], :2].mean(axis=0)
    assert img.pixelColor(int(palm[0]), int(palm[1])).alpha() > 100
    assert img.pixelColor(5, 5).alpha() == 0
    renderer.release()
    ctx.doneCurrent()
