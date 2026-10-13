"""
core/hologram_gl_backend.py
============================

Backend GPU 3D do holograma: janela OpenGL transparente em tela cheia
(QOpenGLWindow) desenhando a mao 3D estilo headset VR.

Pipeline (cada modulo com uma responsabilidade):
    landmarks --HandPoseSolver--> PosedHand --build_hand_mesh--> HandMesh
              (core.hand_pose)               (core.hand_mesh)
    HandMesh --HandGLRenderer--> framebuffer da janela
               (core.hand_gl_renderer, OpenGL do proprio Qt)

API PUBLICA identica ao backend QPainter (drop-in via facade):
    HologramGLBackend(...)
    .available, .click_through_active, .enabled
    .set_enabled(bool) / .toggle() -> bool
    .update_pose(landmarks, x, y) / .set_frame_aspect(aspect)
    .update_cursor(x, y) / .fire_burst(kind) / .pump() / .close()

Sem moderngl/glcontext: em Windows com Smart App Control a DLL do
glcontext pode ser bloqueada; o OpenGL do Qt usa as mesmas DLLs da
janela. `available` so fica True se um contexto OpenGL 3.3 core e'
realmente criado — senao a facade cai pro renderer QPainter.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from typing import Optional, Sequence, Tuple

from .hand_gl_renderer import STYLE_PRESETS, HandGLRenderer, HandStyle, clear_framebuffer
from .hand_mesh import HandMesh, build_hand_mesh
from .hand_pose import HandPoseSolver, PosedHand

logger = logging.getLogger(__name__)


try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat
    from PySide6.QtOpenGL import QOpenGLWindow
    from PySide6.QtWidgets import QApplication
    _PYSIDE_OK = True
    _PYSIDE_ERROR: Optional[str] = None
except ImportError as e:  # pragma: no cover - depende do ambiente
    _PYSIDE_OK = False
    _PYSIDE_ERROR = str(e)


def _surface_format() -> "QSurfaceFormat":
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setAlphaBufferSize(8)
    fmt.setDepthBufferSize(24)
    # 4x MSAA: silhueta lisa da mao (o rim/fresnel denuncia serrilhado).
    fmt.setSamples(4)
    # Sem VSync: o paint roda no MESMO thread do loop da camera; esperar o
    # retrace no swapBuffers travava o tick (p99 ~21 ms -> ver CHANGELOG).
    fmt.setSwapInterval(0)
    return fmt


def probe_opengl() -> Tuple[bool, str]:
    """Cria (e descarta) um contexto OpenGL 3.3 core offscreen.

    Precisa de QApplication ja criada. Retorna (ok, renderer/erro).
    """
    if not _PYSIDE_OK:
        return False, f"PySide6 indisponivel: {_PYSIDE_ERROR}"
    app = QApplication.instance()
    platform = app.platformName() if app is not None else ""
    if platform in ("offscreen", "minimal"):
        # Sem display real (CI/headless): nem tenta — alguns drivers
        # derrubam o processo em vez de falhar o create().
        return False, f"plataforma Qt '{platform}' sem display"
    try:
        ctx = QOpenGLContext()
        ctx.setFormat(_surface_format())
        if not ctx.create():
            return False, "QOpenGLContext.create() falhou"
        fmt = ctx.format()
        if (fmt.majorVersion(), fmt.minorVersion()) < (3, 3):
            return False, f"OpenGL {fmt.majorVersion()}.{fmt.minorVersion()} < 3.3"
        surface = QOffscreenSurface()
        surface.setFormat(fmt)
        surface.create()
        if not ctx.makeCurrent(surface):
            return False, "makeCurrent falhou"
        name = ctx.functions().glGetString(0x1F01) or "?"  # GL_RENDERER
        ctx.doneCurrent()
        surface.destroy()
        return True, str(name)
    except Exception as e:  # pragma: no cover - driver
        return False, str(e)


def _apply_click_through_win32(hwnd: int) -> bool:
    """Garante WS_EX_TRANSPARENT (mouse atravessa a janela) no Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        gwl_exstyle = -20
        ws_ex_layered = 0x00080000
        ws_ex_transparent = 0x00000020
        ws_ex_toolwindow = 0x00000080
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, gwl_exstyle)
        user32.SetWindowLongW(
            hwnd, gwl_exstyle,
            ex_style | ws_ex_layered | ws_ex_transparent | ws_ex_toolwindow,
        )
        return True
    except Exception as e:
        logger.debug("click-through win32 falhou: %s", e)
        return False


if _PYSIDE_OK:

    class _HologramGLWindow(QOpenGLWindow):
        """Janela OpenGL transparente, sempre no topo, click-through."""

        def __init__(self, overlay: "HologramGLBackend", target_fps: int) -> None:
            super().__init__()
            self._overlay = overlay
            self.setFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowTransparentForInput
                | Qt.WindowType.NoDropShadowWindowHint
            )
            self.setFormat(_surface_format())
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
            if screen is not None:
                self.setGeometry(screen.geometry())

            # Repaint adaptativo: rate ativo com mao visivel, idle sem mao.
            self._active_ms = max(8, int(1000 / max(10, target_fps)))
            self._idle_ms = 100
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.update)
            self._timer.start(self._idle_ms)
            self._timer_active = False

        def set_active_rate(self, active: bool) -> None:
            if active == self._timer_active:
                return
            self._timer_active = active
            self._timer.start(self._active_ms if active else self._idle_ms)

        def initializeGL(self) -> None:
            try:
                ok = self._overlay._renderer.initialize()
                if not ok:
                    logger.warning(
                        "Holograma: shader falhou: %s",
                        getattr(self._overlay._renderer, "_last_error", "?"),
                    )
            except Exception as e:
                logger.warning("Holograma: init OpenGL falhou: %s", e)

        def paintGL(self) -> None:
            try:
                ctx = QOpenGLContext.currentContext()
                if ctx is not None:
                    dpr = self.devicePixelRatio()
                    ctx.functions().glViewport(
                        0, 0, int(self.width() * dpr), int(self.height() * dpr),
                    )
                clear_framebuffer()
                self._overlay._render()
            except Exception as e:  # pragma: no cover
                logger.debug("paintGL erro: %s", e)

        def showEvent(self, event) -> None:
            super().showEvent(event)
            with contextlib.suppress(Exception):
                _apply_click_through_win32(int(self.winId()))


class HologramGLBackend:
    """Backend OpenGL do holograma (drop-in do QPainter via facade)."""

    def __init__(
        self,
        *,
        hand_size_px: int = 180,
        opacity: float = 0.85,
        target_fps: int = 30,
        style: Optional[HandStyle] = None,
        frame_aspect: float = 16.0 / 9.0,
    ) -> None:
        self.available: bool = False
        self.click_through_active: bool = False
        self._enabled: bool = False
        self._target_fps = max(10, int(target_fps))
        self._screen_w: int = 1920
        self._screen_h: int = 1080

        self._solver = HandPoseSolver(
            hand_size_px=hand_size_px, frame_aspect=frame_aspect,
        )
        self._renderer = HandGLRenderer(
            style=style or STYLE_PRESETS["quest"], opacity=opacity,
        )
        self._posed: Optional[PosedHand] = None
        self._mesh: Optional[HandMesh] = None
        self._pose_version = 0
        self._mesh_version = -1

        self._app = None
        self._window: Optional["_HologramGLWindow"] = None

        if not _PYSIDE_OK:
            logger.warning("HologramGLBackend: PySide6 indisponivel (%s)", _PYSIDE_ERROR)
            return
        try:
            self._app = QApplication.instance() or QApplication(
                sys.argv if hasattr(sys, "argv") else [],
            )
            if not isinstance(self._app, QApplication):
                logger.warning("HologramGLBackend: app Qt sem suporte a janelas")
                return
            ok, info = probe_opengl()
            if not ok:
                logger.warning("HologramGLBackend: OpenGL 3.3 indisponivel (%s)", info)
                return
            screen = self._app.primaryScreen()
            if screen is not None:
                # Pixels FISICOS (mesmo espaco do CursorController / pyautogui).
                geom = screen.geometry()
                dpr = float(screen.devicePixelRatio() or 1.0)
                self._screen_w = int(round(geom.width() * dpr))
                self._screen_h = int(round(geom.height() * dpr))
            self._window = _HologramGLWindow(self, self._target_fps)
            self._window.hide()
            self.available = True
            self.click_through_active = True
            logger.info("Holograma GL pronto | %s", info)
        except Exception as e:
            logger.warning("HologramGLBackend init falhou: %s", e)
            self.available = False

    # ------------------------------------------------------------ API

    def set_enabled(self, enabled: bool) -> None:
        if not self.available or self._window is None or enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._window.show()
            self._window.raise_()
        else:
            self._window.hide()
            self._posed = None

    def toggle(self) -> bool:
        self.set_enabled(not self._enabled)
        return self._enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def screen_size(self) -> Tuple[int, int]:
        return self._screen_w, self._screen_h

    def set_frame_aspect(self, aspect: float) -> None:
        self._solver.set_frame_aspect(aspect)

    def update_pose(
        self,
        landmarks: Optional[Sequence[Tuple[float, float, float]]],
        screen_x: float,
        screen_y: float,
    ) -> None:
        self._posed = self._solver.solve(landmarks, screen_x, screen_y)
        self._pose_version += 1
        if self._window is not None:
            self._window.set_active_rate(self._posed is not None)

    def update_cursor(self, screen_x: float, screen_y: float) -> None:
        """Mantido por compat (o cursor ja chega via update_pose)."""

    def fire_burst(self, kind: str = "click") -> None:
        """Sem anel de clique: o feedback e' o brilho das pontas na pinca."""

    def pump(self) -> None:
        if not self.available or self._app is None:
            return
        try:
            self._app.processEvents()
        except Exception as e:  # pragma: no cover
            logger.debug("pump erro: %s", e)

    def close(self) -> None:
        if self._window is not None:
            try:
                self._window.makeCurrent()
                self._renderer.release()
                self._window.doneCurrent()
            except Exception:
                pass
            try:
                self._window.close()
                self._window.deleteLater()
            except Exception:
                pass
            self._window = None
        self.available = False
        self._enabled = False

    # --------------------------------------------------------- render

    def _render(self) -> None:
        """Chamado pelo paintGL (contexto atual, framebuffer limpo)."""
        posed = self._posed
        if not self._enabled or posed is None:
            return
        # Mesh so e' refeita quando chega pose nova (o timer de repaint
        # pode rodar mais rapido que a camera).
        if self._mesh is None or self._mesh_version != self._pose_version:
            self._mesh = build_hand_mesh(posed)
            self._mesh_version = self._pose_version
        self._renderer.render(self._mesh, posed, self._screen_w, self._screen_h)
