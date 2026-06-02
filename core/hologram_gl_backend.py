"""
core/hologram_gl_backend.py
============================

Backend GPU 3D do holograma usando ModernGL. Substitui o pipeline
QPainter (2D vetorial) por mesh real + shader GLSL com fresnel rim,
depth fade e blending volumetrico.

API PUBLICA IDENTICA ao QPainter backend pra ser drop-in via facade:
    HologramGLBackend(...)
    .available, .click_through_active, .enabled
    .set_enabled(bool) / .toggle() -> bool
    .update_pose(landmarks, x, y)
    .update_cursor(x, y)
    .fire_burst(kind)
    .pump()
    .close()

DESIGN INTENCIONAL:
- Soft-fail tripla camada: ModernGL ausente, PySide6 ausente, init Qt
  falhar => available=False, projeto continua via QPainter fallback.
- Sem coupling com hand_tracker / gesture / cursor — recebe so landmarks
  ja smoothed e renderiza.
- Sem state compartilhado com QPainter backend — se ambos disponiveis,
  facade escolhe um.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .hand_mesh import generate_hand_mesh
from .smoothing import OneEuroSmoother2D


logger = logging.getLogger(__name__)


# Soft-import ModernGL
try:
    import moderngl
    _MGL_OK = True
    _MGL_ERROR: Optional[str] = None
except ImportError as e:
    _MGL_OK = False
    _MGL_ERROR = str(e)


# Soft-import PySide6 (OpenGL surface support)
try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtOpenGL import QOpenGLWindow
    from PySide6.QtWidgets import QApplication
    _PYSIDE_OK = True
    _PYSIDE_ERROR: Optional[str] = None
except ImportError as e:
    _PYSIDE_OK = False
    _PYSIDE_ERROR = str(e)


# =====================================================================
# SHADERS GLSL — embedded como strings.
# Material holografico: cyan base + fresnel rim + depth-based alpha.
# =====================================================================

_VERTEX_SHADER = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;

uniform mat4 u_proj;

out vec3 v_normal;
out float v_depth;
out vec3 v_pos;

void main() {
    gl_Position = u_proj * vec4(in_position, 1.0);
    // Normal em world space (proj e' apenas ortho, sem rotacao)
    v_normal = normalize(in_normal);
    v_depth = in_position.z;
    v_pos = in_position;
}
"""

_FRAGMENT_SHADER = """
#version 330 core
in vec3 v_normal;
in float v_depth;
in vec3 v_pos;

uniform float u_time;
uniform float u_fresnel_power;
uniform float u_opacity;
uniform vec3 u_base_color;
uniform vec3 u_rim_color;
uniform float u_depth_fade_range;

out vec4 frag_color;

void main() {
    // View direction: cam olhando -Z (ortho space)
    vec3 view_dir = vec3(0.0, 0.0, 1.0);

    // Fresnel rim: bordas rasantes ficam brilhantes (rim light)
    float ndotv = max(abs(dot(normalize(v_normal), view_dir)), 0.001);
    float fresnel = pow(1.0 - ndotv, u_fresnel_power);

    // v6.9.8.10: depth fade quase plana (range 0.75 → 1.0). Antes 0.60→1.0
    // criava gradiente visivel entre front/back que se lia como "split"
    // no centro da palma. Agora variacao sutil pra preservar volume sem
    // criar ridge.
    float depth_factor = clamp(0.75 + v_depth / u_depth_fade_range, 0.75, 1.0);

    // Subtle breathing
    float breath = 0.92 + 0.08 * sin(u_time * 2.0);

    // v6.9.8.10: body alpha 0.90 + rim multiplier 0.8 = corpo unificado
    // dominante, rim apenas reforça silhueta (não cria "split" no centro).
    vec3 body = u_base_color * depth_factor * breath;
    vec3 rim = u_rim_color * fresnel * 0.8;  // rim suave, nao agressivo
    vec3 color = body + rim;

    // Alpha quase opaco — mata o "ridge" visual no centro
    float alpha = (0.90 + fresnel * 0.10) * u_opacity * depth_factor;
    alpha = clamp(alpha, 0.0, 1.0);

    frag_color = vec4(color, alpha);
}
"""


# =====================================================================
# Click-through nativo Windows (deps lazy — so quando precisa)
# =====================================================================

def _apply_click_through_win32(hwnd: int) -> bool:
    """Aplica click-through em janela Windows via SetWindowLongW.
    Returns True se aplicou com sucesso, False se nao for Windows ou falhar."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = ex_style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        return True
    except Exception as e:
        logger.debug("click-through win32 falhou: %s", e)
        return False


# =====================================================================
# Window OpenGL (so se Qt + ModernGL disponiveis)
# =====================================================================

if _PYSIDE_OK and _MGL_OK:

    class _HologramGLWindow(QOpenGLWindow):
        """
        Window OpenGL transparente em tela cheia. Gerencia contexto
        ModernGL e chama back no overlay pra render por frame.
        """

        def __init__(self, overlay: "HologramGLBackend", target_fps: int):
            super().__init__()
            self._overlay = overlay

            # Window flags pra overlay transparente + always on top + click-through
            self.setFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowTransparentForInput
                | Qt.WindowType.NoDropShadowWindowHint
            )

            # Surface format: alpha buffer + MSAA + GL 3.3 core
            fmt = QSurfaceFormat()
            fmt.setVersion(3, 3)
            fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
            fmt.setAlphaBufferSize(8)
            fmt.setSamples(4)  # 4x MSAA
            self.setFormat(fmt)

            # Tamanho = tela primaria inteira
            app = QApplication.instance()
            if app is not None:
                screen = app.primaryScreen()
                if screen is not None:
                    geom = screen.geometry()
                    self.setGeometry(geom)

            # Recursos GL — inicializados em initializeGL
            self._ctx: Optional[moderngl.Context] = None
            self._program: Optional[moderngl.Program] = None
            self._gl_ready: bool = False

            # Timer de repaint
            interval_ms = max(8, int(1000 / max(10, target_fps)))
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.requestUpdate)
            self._timer.start(interval_ms)

        def initializeGL(self) -> None:
            try:
                self._ctx = moderngl.create_context()
                self._program = self._ctx.program(
                    vertex_shader=_VERTEX_SHADER,
                    fragment_shader=_FRAGMENT_SHADER,
                )
                # Blend alpha
                self._ctx.enable(moderngl.BLEND)
                self._ctx.blend_func = (
                    moderngl.SRC_ALPHA,
                    moderngl.ONE_MINUS_SRC_ALPHA,
                )
                # Depth test off — alpha blending precisa ordem mas com
                # rim light/fresnel da pra ignorar Z porque a leitura
                # visual final fica "x-ray-ish" (boa pra hologram).
                self._gl_ready = True
                logger.info("ModernGL ctx OK | vendor=%s", self._ctx.info.get("GL_VENDOR", "?"))
            except Exception as e:
                logger.warning("ModernGL init falhou: %s", e)
                self._gl_ready = False

        def resizeGL(self, w: int, h: int) -> None:
            if self._ctx is not None:
                self._ctx.viewport = (0, 0, w, h)

        def paintGL(self) -> None:
            if not self._gl_ready or self._ctx is None or self._program is None:
                return
            try:
                self._overlay._render(self._ctx, self._program)
            except Exception as e:  # pragma: no cover
                logger.debug("paintGL error: %s", e)

        def showEvent(self, event) -> None:  # noqa: N802
            super().showEvent(event)
            # Reaplica click-through apos mostrar (alguns drivers resetam)
            try:
                hwnd = int(self.winId())
                _apply_click_through_win32(hwnd)
            except Exception:
                pass


# =====================================================================
# Backend publico
# =====================================================================

class HologramGLBackend:
    """
    Backend ModernGL pro holograma. Drop-in replacement do QPainter
    backend via facade em HologramOverlay.
    """

    # Cores default (deep blue hologram).
    # v6.9.8.7: ainda mais escuro/denso — usuario reportou hand sumindo
    # em browser/LinkedIn whites. Agora deep navy puro = solido em
    # qualquer background, mantendo hologram feel via rim brilhante.
    _DEFAULT_BASE_COLOR: Tuple[float, float, float] = (0.02, 0.12, 0.48)
    _DEFAULT_RIM_COLOR: Tuple[float, float, float] = (0.45, 0.82, 1.0)

    def __init__(
        self,
        *,
        hand_size_px: int = 180,
        opacity: float = 0.85,
        target_fps: int = 30,
        base_color: Optional[Tuple[float, float, float]] = None,
        rim_color: Optional[Tuple[float, float, float]] = None,
        fresnel_power: float = 1.8,  # v6.9.8.10: 2.5 → 1.8 (rim mais suave, sem split)
        depth_fade_range: float = 50.0,
    ) -> None:
        self.available: bool = False
        self.click_through_active: bool = False
        self._enabled: bool = False

        self._hand_size_px = int(hand_size_px)
        self._opacity = max(0.0, min(1.0, float(opacity)))
        self._target_fps = max(10, int(target_fps))
        self._base_color = base_color or self._DEFAULT_BASE_COLOR
        self._rim_color = rim_color or self._DEFAULT_RIM_COLOR
        self._fresnel_power = float(fresnel_power)
        self._depth_fade_range = float(depth_fade_range)

        self._screen_w: int = 1920
        self._screen_h: int = 1080

        # Pose state
        self._pose_landmarks: Optional[Sequence[Tuple[float, float, float]]] = None
        self._pose_x: float = 0.0
        self._pose_y: float = 0.0
        self._pose_visible: bool = False

        # Smoothing dos landmarks (mesmo padrao do Qt backend)
        self._lm_smoothers: Optional[List[OneEuroSmoother2D]] = None
        self._lm_smoother_min_cutoff: float = 0.8
        self._lm_smoother_beta: float = 1.5

        # Cursor (pra idle ring future)
        self._cursor_x: float = 0.0
        self._cursor_y: float = 0.0
        self._has_cursor: bool = False

        self._start_time = time.perf_counter()

        # Qt refs
        self._app = None
        self._window: Optional["_HologramGLWindow"] = None

        # v6.9.8.6: persistent GPU buffers — lazy init no primeiro _render.
        # Substitui ctx.buffer() per-frame por buffer.write() (zero alloc).
        # Reservamos MAX_VERTS/MAX_INDICES com slack pra futuras meshes.
        self._vbo_pos = None
        self._vbo_norm = None
        self._ibo = None
        self._vao = None
        self._MAX_VERTS: int = 1536    # ~2x current 769 verts
        self._MAX_INDICES: int = 4096  # ~2x current 4422 (1474*3)

        # v6.9.8.9: mesh cache — pula gen quando pose estavel.
        # Smoother converge quando mao para → landmarks identicos → cache hit
        # → 0ms mesh gen → frame budget livre → gestos respondem instant.
        # Threshold 5e-4 = sum-of-abs(63 components) < ~0.0005 = sub-pixel
        # spread total. Hand still pelo smoother costuma dar < 1e-4 sum.
        self._cached_landmarks: Optional[Sequence[Tuple[float, float, float]]] = None
        self._cached_center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._cached_mesh: Optional[
            Tuple["np.ndarray", "np.ndarray", "np.ndarray"]
        ] = None
        self._mesh_cache_hits: int = 0
        self._mesh_cache_misses: int = 0
        self._POSE_CACHE_THRESHOLD: float = 5e-4

        if not _MGL_OK:
            logger.warning(
                "HologramGLBackend: ModernGL nao instalado (%s). "
                "Backend GL indisponivel — facade usara QPainter.",
                _MGL_ERROR,
            )
            return
        if not _PYSIDE_OK:
            logger.warning(
                "HologramGLBackend: PySide6 indisponivel (%s).",
                _PYSIDE_ERROR,
            )
            return

        try:
            self._init_qt()
            self.available = True
            self.click_through_active = True
        except Exception as e:
            logger.warning("HologramGLBackend init falhou: %s", e)
            self.available = False

    # ---------------------------------------------- Qt setup

    def _init_qt(self) -> None:
        self._app = QApplication.instance()
        if self._app is None:
            self._app = QApplication(sys.argv if hasattr(sys, "argv") else [])

        screen = self._app.primaryScreen()
        if screen is not None:
            geom = screen.geometry()
            self._screen_w = geom.width()
            self._screen_h = geom.height()

        self._window = _HologramGLWindow(self, self._target_fps)
        self._window.hide()

    # ---------------------------------------------- Public API

    def set_enabled(self, enabled: bool) -> None:
        if not self.available or self._window is None:
            return
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._window.show()
            self._window.raise_()
        else:
            self._window.hide()
            self._pose_visible = False

    def toggle(self) -> bool:
        self.set_enabled(not self._enabled)
        return self._enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def update_pose(
        self,
        landmarks: Optional[Sequence[Tuple[float, float, float]]],
        screen_x: float,
        screen_y: float,
    ) -> None:
        if landmarks is None or len(landmarks) < 21:
            self._pose_visible = False
            self._pose_landmarks = None
            self._lm_smoothers = None
            return
        smoothed = self._smooth_landmarks(landmarks)
        self._pose_landmarks = smoothed
        self._pose_visible = True
        self._pose_x = float(screen_x)
        self._pose_y = float(screen_y)

    def _smooth_landmarks(
        self,
        raw: Sequence[Tuple[float, float, float]],
    ) -> List[Tuple[float, float, float]]:
        if self._lm_smoothers is None or len(self._lm_smoothers) != len(raw):
            self._lm_smoothers = [
                OneEuroSmoother2D(
                    freq=60.0,
                    min_cutoff=self._lm_smoother_min_cutoff,
                    beta=self._lm_smoother_beta,
                    d_cutoff=1.0,
                )
                for _ in raw
            ]
        out: List[Tuple[float, float, float]] = []
        for smoother, p in zip(self._lm_smoothers, raw):
            sx, sy = smoother(p[0], p[1])
            out.append((sx, sy, p[2]))
        return out

    def update_cursor(self, screen_x: float, screen_y: float) -> None:
        self._cursor_x = float(screen_x)
        self._cursor_y = float(screen_y)
        self._has_cursor = True

    def fire_burst(self, kind: str = "click") -> None:
        # TODO: implementar burst como pass adicional GL.
        # Por enquanto sem efeito visual (gestos continuam funcionando).
        pass

    def pump(self) -> None:
        if not self.available or self._app is None:
            return
        try:
            self._app.processEvents()
        except Exception as e:  # pragma: no cover
            logger.debug("pump erro: %s", e)

    def close(self) -> None:
        # v6.9.8.6: libera buffers persistentes antes da window
        self._release_buffers()
        if self._window is not None:
            try:
                self._window.close()
                self._window.deleteLater()
            except Exception:
                pass
            self._window = None
        self.available = False
        self._enabled = False

    # ---------------------------------------------- Render

    def _pose_changed_significantly(
        self,
        landmarks: Sequence[Tuple[float, float, float]],
        center: Tuple[float, float, float],
    ) -> bool:
        """
        Compara landmarks atuais com cache.

        Sum-of-abs-diff com early exit assim que threshold cruza.
        Quando mao parada: smoother converge → diff = 0 → cache HIT.
        Quando mao mexendo: diff > threshold → cache MISS = regen.
        """
        if self._cached_landmarks is None:
            return True
        if len(self._cached_landmarks) != len(landmarks):
            return True
        # Center includes position + size — qualquer mudanca = regen
        c0, c1, c2 = self._cached_center
        if (abs(c0 - center[0]) > 0.5
                or abs(c1 - center[1]) > 0.5
                or abs(c2 - center[2]) > 0.1):
            return True
        # Landmark deltas
        total = 0.0
        threshold = self._POSE_CACHE_THRESHOLD
        for a, b in zip(landmarks, self._cached_landmarks):
            total += abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
            if total > threshold:
                return True
        return False

    def _render(self, ctx, program) -> None:
        """
        Called from paintGL. v6.9.8.6: PERSISTENT buffers.

        Lazy-init dos VBOs/IBO/VAO no primeiro frame (precisa do ctx).
        Frames subsequentes apenas chamam buffer.write() = zero allocacao.
        Drastically reduces frame time and Python/GPU sync overhead.
        """
        if not self._enabled:
            ctx.clear(0.0, 0.0, 0.0, 0.0)
            return

        ctx.clear(0.0, 0.0, 0.0, 0.0)

        if not self._pose_visible or self._pose_landmarks is None:
            return

        # v6.9.8.9: mesh cache check antes de regenerar.
        # Compara pose contra cache; se mudanca subpixel apenas, reusa.
        center_key = (
            float(self._pose_x), float(self._pose_y), float(self._hand_size_px),
        )
        cache_valid = (
            self._cached_mesh is not None
            and not self._pose_changed_significantly(
                self._pose_landmarks, center_key,
            )
        )

        if cache_valid:
            vertices, normals, indices = self._cached_mesh  # type: ignore
            self._mesh_cache_hits += 1
        else:
            vertices, normals, indices = generate_hand_mesh(
                self._pose_landmarks,
                self._pose_x, self._pose_y,
                self._hand_size_px,
            )
            # Atualiza cache (deep copy landmarks pra evitar mutacao externa)
            self._cached_mesh = (vertices, normals, indices)
            self._cached_landmarks = tuple(self._pose_landmarks)
            self._cached_center = center_key
            self._mesh_cache_misses += 1

        n_verts = int(vertices.shape[0])
        n_indices = int(indices.shape[0])
        if n_verts == 0 or n_indices == 0:
            return

        # Defensive: realloc se mesh cresceu alem do reservado (raro)
        if n_verts > self._MAX_VERTS or n_indices > self._MAX_INDICES:
            self._MAX_VERTS = max(self._MAX_VERTS, n_verts * 2)
            self._MAX_INDICES = max(self._MAX_INDICES, n_indices * 2)
            self._release_buffers()

        # Lazy-init buffers no primeiro frame (precisa ctx ativo)
        if self._vbo_pos is None:
            self._vbo_pos = ctx.buffer(reserve=self._MAX_VERTS * 3 * 4)
            self._vbo_norm = ctx.buffer(reserve=self._MAX_VERTS * 3 * 4)
            self._ibo = ctx.buffer(reserve=self._MAX_INDICES * 4)
            self._vao = ctx.vertex_array(
                program,
                [
                    (self._vbo_pos, "3f", "in_position"),
                    (self._vbo_norm, "3f", "in_normal"),
                ],
                self._ibo,
            )

        # Update buffer contents APENAS se mesh foi regenerada.
        # Cache hit = GPU buffers ja contem dados corretos = skip upload.
        if not cache_valid:
            self._vbo_pos.write(vertices.tobytes())
            self._vbo_norm.write(normals.tobytes())
            self._ibo.write(indices.tobytes())

        # Uniforms
        proj = self._ortho_projection_matrix()
        try:
            program["u_proj"].write(proj.tobytes())
            program["u_time"].value = float(time.perf_counter() - self._start_time)
            program["u_fresnel_power"].value = self._fresnel_power
            program["u_opacity"].value = self._opacity
            program["u_base_color"].value = self._base_color
            program["u_rim_color"].value = self._rim_color
            program["u_depth_fade_range"].value = self._depth_fade_range
        except KeyError as e:
            logger.debug("uniform faltando: %s", e)

        # Render apenas os indices usados (buffer pode ser maior)
        self._vao.render(moderngl.TRIANGLES, vertices=n_indices)

    def _release_buffers(self) -> None:
        """Libera buffers GPU (chamado em realloc ou close)."""
        for attr in ("_vao", "_vbo_pos", "_vbo_norm", "_ibo"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.release()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _ortho_projection_matrix(self) -> np.ndarray:
        """
        Projecao ortografica screen-space — mapeia (0,0)..(W,H) em (-1,-1)..(1,1).
        Y invertido (origem top-left). Z passa direto (depth fade no shader).
        Column-major pra OpenGL.
        """
        w = float(self._screen_w)
        h = float(self._screen_h)
        # Matrix 4x4 ortho (row-major aqui, transpose ao enviar)
        mat = np.array([
            [2.0 / w,  0.0,       0.0,   -1.0],
            [0.0,      -2.0 / h,  0.0,    1.0],
            [0.0,      0.0,       0.001,  0.0],
            [0.0,      0.0,       0.0,    1.0],
        ], dtype=np.float32)
        # OpenGL espera column-major
        return mat.T.copy()
