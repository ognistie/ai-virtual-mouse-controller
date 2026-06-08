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

import atexit
import logging
import sys
import time
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .click_burst import (
    BurstManager,
    KIND_CLICK,
    KIND_DOUBLE_CLICK,
    KIND_DRAG_END,
    KIND_DRAG_START,
    KIND_RIGHT_CLICK,
)
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


# Shader minimalista pra bursts de clique (aneis expansivos no ponto
# do pinch). Cada vertice carrega corner ([-1..1]) + center (px) +
# radius (px) + alpha + color_id. Renderizado APOS o mesh, alpha-blended.
_BURST_VERTEX_SHADER = """
#version 330 core
uniform mat4 u_proj;
in vec2 in_corner;
in vec2 in_center;
in float in_radius;
in float in_alpha;
in float in_color_id;
out vec2 v_local;
out float v_alpha;
out float v_color_id;
void main() {
    vec2 world = in_center + in_corner * in_radius;
    gl_Position = u_proj * vec4(world, 0.0, 1.0);
    v_local = in_corner;
    v_alpha = in_alpha;
    v_color_id = in_color_id;
}
"""

_BURST_FRAGMENT_SHADER = """
#version 330 core
in vec2 v_local;
in float v_alpha;
in float v_color_id;
out vec4 frag_color;
void main() {
    float d = length(v_local);
    if (d > 1.0) discard;
    // Anel: pico em d ~= 0.82, espessura ~0.12 — borda macia.
    float ring = 1.0 - clamp(abs(d - 0.82) / 0.12, 0.0, 1.0);
    ring = pow(ring, 1.6);
    if (ring < 0.02) discard;
    // 0 = red-ish (clique esquerdo/double/drag), 1 = cyan (right-click).
    vec3 col = v_color_id < 0.5
        ? vec3(1.00, 0.42, 0.48)
        : vec3(0.40, 0.92, 1.00);
    // Halo interno leve pra dar profundidade no centro do anel.
    float halo = (1.0 - smoothstep(0.0, 0.7, d)) * 0.12;
    frag_color = vec4(col, (ring + halo) * v_alpha);
}
"""


# =====================================================================
# Click-through nativo Windows (deps lazy — so quando precisa)
# =====================================================================

# =====================================================================
# System cursor hide/restore (Win32)
# =====================================================================
# Quando o holograma esta ativo, queremos que A MAO seja o ponteiro —
# nao "cursor + mao". Trocamos os cursores do sistema por uma versao
# transparente (SetSystemCursor) e restauramos via SPI_SETCURSORS.
# Estado global pra evitar hide duplicado e garantir restore via atexit
# mesmo se o app crashar.
_CURSOR_HIDDEN: bool = False


def _hide_system_cursor_win32() -> bool:
    """Substitui cursores do sistema por versao invisivel.

    Idempotente: chamadas repetidas nao tem efeito adicional. Retorna True
    se aplicou (ou ja estava aplicado), False se nao for Windows ou falhar.
    """
    global _CURSOR_HIDDEN
    if _CURSOR_HIDDEN:
        return True
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32

        # Cursor 32x32 totalmente transparente:
        # AND mask all 1s + XOR mask all 0s = pixels totalmente transparentes.
        size = 32
        bytes_per_plane = size * size // 8
        and_plane = (ctypes.c_ubyte * bytes_per_plane)(*([0xFF] * bytes_per_plane))
        xor_plane = (ctypes.c_ubyte * bytes_per_plane)(*([0x00] * bytes_per_plane))
        blank = user32.CreateCursor(None, 0, 0, size, size, and_plane, xor_plane)
        if not blank:
            return False

        # OCR_* = cursores do sistema que substituimos.
        # SetSystemCursor ASSUME OWNERSHIP do handle a cada chamada,
        # portanto duplicamos pra cada substituicao.
        OCR_IDS = (
            32512,  # OCR_NORMAL  — seta padrao
            32513,  # OCR_IBEAM   — text
            32649,  # OCR_HAND    — link
            32650,  # OCR_APPSTARTING
            32651,  # OCR_HELP
            32646,  # OCR_SIZEALL
            32643,  # OCR_SIZENS
            32644,  # OCR_SIZEWE
            32642,  # OCR_SIZENWSE
            32645,  # OCR_SIZENESW
            32648,  # OCR_NO
            32514,  # OCR_WAIT
        )
        for ocr in OCR_IDS:
            try:
                dup = user32.CopyIcon(blank)
                if dup:
                    user32.SetSystemCursor(dup, ocr)
            except Exception:
                pass

        user32.DestroyCursor(blank)
        _CURSOR_HIDDEN = True
        logger.info("System cursor escondido (holograma ativo)")
        return True
    except Exception as e:  # pragma: no cover
        logger.debug("hide cursor falhou: %s", e)
        return False


def _restore_system_cursor_win32() -> bool:
    """Restaura cursores do sistema ao default (SPI_SETCURSORS)."""
    global _CURSOR_HIDDEN
    if not _CURSOR_HIDDEN:
        return True
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        SPI_SETCURSORS = 0x0057
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETCURSORS, 0, None, 0,
        )
        _CURSOR_HIDDEN = False
        if ok:
            logger.info("System cursor restaurado")
        return bool(ok)
    except Exception as e:  # pragma: no cover
        logger.debug("restore cursor falhou: %s", e)
        return False


# Safety net: se o processo morrer, restaurar cursor pra nao deixar
# o usuario com cursor invisivel pelo sistema inteiro.
atexit.register(_restore_system_cursor_win32)


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
            # PERF: 4x → 2x MSAA. Em surface fullscreen + rim suave (depth
            # fade + fresnel), 2x produz resultado visualmente identico ao
            # 4x mas com ~50% do custo de fragment shader. Em telas 1080p+
            # esse foi o segundo maior ganho de fps medido.
            fmt.setSamples(2)
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

            # Timer de repaint — ADAPTATIVO:
            # - rate ATIVO (target_fps): quando ha mao OU bursts em curso.
            #   Garante render fluido (~30fps) durante uso real.
            # - rate IDLE (~6fps): sem mao detectada e sem burst pendente.
            #   Mantem janela viva sem queimar CPU/GPU em redraw a vazio
            #   (sem isso, cada paintGL faz clear+compositing fullscreen).
            # set_active() alterna entre os dois conforme o estado real.
            self._active_interval_ms = max(8, int(1000 / max(10, target_fps)))
            self._idle_interval_ms = 160  # ~6 fps quando nao ha o que mostrar
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.requestUpdate)
            self._timer.start(self._idle_interval_ms)
            self._timer_active = False

        def set_active_rate(self, active: bool) -> None:
            """Alterna timer entre rate ativo (mao visivel) e idle."""
            if active == self._timer_active:
                return
            self._timer_active = active
            self._timer.setInterval(
                self._active_interval_ms if active else self._idle_interval_ms,
            )

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

        # Smoothing dos landmarks.
        # v6.9.13: min_cutoff 0.8 → 0.55 — em repouso o OneEuro filtra mais
        # forte, eliminando ~60% do tremor visual do holograma quando a mao
        # esta parada (tinha micro-vibracao visivel que dava sensacao de
        # "instabilidade"). beta 1.5 → 1.8 mantem a abertura agressiva
        # quando ha movimento real — sem lag perceptivel.
        self._lm_smoothers: Optional[List[OneEuroSmoother2D]] = None
        self._lm_smoothed_buf: Optional[List[Tuple[float, float, float]]] = None
        self._lm_smoother_min_cutoff: float = 0.55
        self._lm_smoother_beta: float = 1.8

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

        # Bursts de clique — feedback visual quando A MAO dispara uma acao.
        # Sem isso, o usuario nao "sente" que a mao clicou (cursor escondido).
        # BurstManager e' puro state; renderizamos via _burst_program/VAO em
        # _render_bursts. Lazy-init dos recursos GL no primeiro burst.
        self._burst_mgr: BurstManager = BurstManager()
        self._burst_program = None
        self._burst_vbo = None
        self._burst_vao = None
        self._MAX_BURSTS: int = 16  # double-click conta 2 aneis simultaneos
        # PERF: scratch buffer pre-alocado pros vertices dos aneis.
        # Cada burst = 6 verts * 7 floats. Preenchemos in-place a cada render.
        # Evita np.empty + loop python aninhado por frame.
        self._burst_scratch: np.ndarray = np.zeros(
            (self._MAX_BURSTS * 6, 7), dtype=np.float32,
        )
        # Template de corners (mesma sequencia em todo burst): preencher 1x.
        # Layout das 6 colunas: [corner.x, corner.y, ...] — só corners agora.
        _corners = np.array([
            [-1.0, -1.0], [1.0, -1.0], [1.0, 1.0],
            [-1.0, -1.0], [1.0,  1.0], [-1.0, 1.0],
        ], dtype=np.float32)
        # Replica corners para todos os slots
        self._burst_scratch[:, 0:2] = np.tile(_corners, (self._MAX_BURSTS, 1))

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
            # FIX alinhamento cursor↔holograma:
            # geom.width()/height() do Qt vem em pixels LOGICOS (device-independent).
            # Mas (a) o framebuffer do QOpenGLWindow é em pixels FÍSICOS — o
            # resizeGL recebe physical e o viewport vive em physical — e (b) o
            # CursorController usa pyautogui, que reporta/move em pixels FÍSICOS
            # (processo DPI-aware). Se o ortho usar logical e os vértices vierem
            # em physical (pose_x = cursor._last_x), em telas com escala >100%
            # (125/150/175%) o holograma renderiza deslocado do cursor — quanto
            # maior o DPR, maior o drift.
            # Multiplicar por devicePixelRatio normaliza tudo pra physical e
            # garante que o midpoint do pinch caia EXATAMENTE no pixel do cursor.
            dpr = float(screen.devicePixelRatio() or 1.0)
            self._screen_w = int(round(geom.width()  * dpr))
            self._screen_h = int(round(geom.height() * dpr))

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
            # Esconde o cursor do sistema — a partir daqui A MAO HOLOGRAFICA
            # é o ponteiro do usuario. Sem isso o usuario veria "seta + mao",
            # quebrando a imersao de que a mao na tela e' a propria mao real.
            _hide_system_cursor_win32()
        else:
            self._window.hide()
            self._pose_visible = False
            # Restaura o cursor — overlay desligado volta ao mouse normal.
            _restore_system_cursor_win32()
            # Limpa bursts ativos pra nao "ressuscitarem" ao reativar.
            self._burst_mgr.clear()

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
            # Se nao ha bursts ativos, deixa o timer cair pra rate IDLE
            # (paintGL praticamente vazio nesse caso).
            if self._window is not None and self._burst_mgr.count() == 0:
                self._window.set_active_rate(False)
            return
        smoothed = self._smooth_landmarks(landmarks)
        self._pose_landmarks = smoothed
        self._pose_visible = True
        self._pose_x = float(screen_x)
        self._pose_y = float(screen_y)
        # Mao detectada → timer em rate ATIVO (target_fps) p/ render fluido.
        if self._window is not None:
            self._window.set_active_rate(True)

    def _smooth_landmarks(
        self,
        raw: Sequence[Tuple[float, float, float]],
    ) -> List[Tuple[float, float, float]]:
        n = len(raw)
        if self._lm_smoothers is None or len(self._lm_smoothers) != n:
            self._lm_smoothers = [
                OneEuroSmoother2D(
                    freq=60.0,
                    min_cutoff=self._lm_smoother_min_cutoff,
                    beta=self._lm_smoother_beta,
                    d_cutoff=1.0,
                )
                for _ in range(n)
            ]
            # PERF: lista pre-alocada — substituimos elementos em vez de
            # rebuild com list-append + tuple-alloc 21x por frame.
            self._lm_smoothed_buf: List[Tuple[float, float, float]] = [
                (0.0, 0.0, 0.0)
            ] * n
        buf = self._lm_smoothed_buf
        smoothers = self._lm_smoothers
        for i in range(n):
            p = raw[i]
            sx, sy = smoothers[i](p[0], p[1])
            buf[i] = (sx, sy, p[2])
        return buf

    def update_cursor(self, screen_x: float, screen_y: float) -> None:
        self._cursor_x = float(screen_x)
        self._cursor_y = float(screen_y)
        self._has_cursor = True

    def fire_burst(self, kind: str = "click") -> None:
        """No-op (v6.9.13): paradigma de mouse fisico — sem feedback visual.

        Real mice nao tem 'anel vermelho' explodindo no click; o feedback
        e' o cursor PARADO + UI reagindo. Anel visual estava roubando a
        atencao do usuario e quebrando a sensacao de naturalidade.

        Metodo mantido pra compat com call-sites (virtual_mouse_service
        chama em todos os eventos). Infraestrutura (BurstManager,
        shaders, scratch buffer) preservada — restauracao futura sem
        ajustar callers.
        """
        return

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
        self._release_burst_buffers()
        # Restaura cursor antes de fechar a janela — garantia extra de que
        # o sistema nao fica com cursor invisivel se close() for chamado
        # fora do fluxo normal de toggle.
        _restore_system_cursor_win32()
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
            # v6.9.13: bursts removidos (sem feedback visual no click)
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

    # ---------------------------------------------- Bursts (click feedback)

    def _render_bursts(self, ctx) -> None:
        """Renderiza aneis de feedback de click no ponto do pinch."""
        self._burst_mgr.cleanup()
        snaps = self._burst_mgr.snapshots()
        if not snaps:
            return

        # Lazy-init shader + buffers no primeiro burst (precisa ctx ativo).
        if self._burst_program is None:
            try:
                self._burst_program = ctx.program(
                    vertex_shader=_BURST_VERTEX_SHADER,
                    fragment_shader=_BURST_FRAGMENT_SHADER,
                )
            except Exception as e:  # pragma: no cover
                logger.debug("burst program falhou: %s", e)
                return
            # 6 verts por anel * 7 floats por vert (corner.xy, center.xy,
            # radius, alpha, color_id). Reservamos espaco com folga.
            self._burst_vbo = ctx.buffer(reserve=self._MAX_BURSTS * 6 * 7 * 4)
            self._burst_vao = ctx.vertex_array(
                self._burst_program,
                [
                    (
                        self._burst_vbo,
                        "2f 2f 1f 1f 1f",
                        "in_corner", "in_center", "in_radius",
                        "in_alpha", "in_color_id",
                    ),
                ],
            )

        # Preenche scratch buffer direto (corners ja sao constantes nas
        # colunas 0:2). Cada slot = 6 verts contiguos com center/r/a/cid
        # replicados. Sem listas Python intermediarias.
        scratch = self._burst_scratch
        slot_count = 0
        max_slots = self._MAX_BURSTS

        for snap in snaps:
            color_id = 0.0 if snap.color_kind == "red" else 1.0
            # Primario
            if snap.alpha > 0.01 and snap.radius > 0.5 and slot_count < max_slots:
                base = slot_count * 6
                scratch[base:base + 6, 2] = snap.x
                scratch[base:base + 6, 3] = snap.y
                scratch[base:base + 6, 4] = snap.radius
                scratch[base:base + 6, 5] = snap.alpha
                scratch[base:base + 6, 6] = color_id
                slot_count += 1
            # Secundario (double-click)
            if snap.secondary_alpha > 0.01 and snap.secondary_radius > 0.5 \
                    and slot_count < max_slots:
                base = slot_count * 6
                scratch[base:base + 6, 2] = snap.x
                scratch[base:base + 6, 3] = snap.y
                scratch[base:base + 6, 4] = snap.secondary_radius
                scratch[base:base + 6, 5] = snap.secondary_alpha
                scratch[base:base + 6, 6] = color_id
                slot_count += 1

        if slot_count == 0:
            return

        # Sobe APENAS os bytes em uso (slot_count * 6 verts * 7 floats * 4 bytes).
        self._burst_vbo.write(scratch[:slot_count * 6].tobytes())

        try:
            self._burst_program["u_proj"].write(
                self._ortho_projection_matrix().tobytes(),
            )
        except KeyError:
            pass

        self._burst_vao.render(moderngl.TRIANGLES, vertices=slot_count * 6)

    def _release_burst_buffers(self) -> None:
        for attr in ("_burst_vao", "_burst_vbo", "_burst_program"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.release()
                except Exception:
                    pass
                setattr(self, attr, None)

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
