"""
core/hand_gl_renderer.py
========================

Renderizador OpenGL da mao 3D (via Qt). Usado pela janela do holograma
(`hologram_gl_backend`) e por scripts de preview offscreen — o mesmo
codigo desenha nos dois lugares.

Visual estilo maos de headset VR:
- iluminacao difusa suave (half-lambert) vinda de cima/esquerda,
- borda clara (fresnel) contornando a silhueta,
- translucidez de CAMADA UNICA: depth pre-pass grava so a superficie mais
  proxima; a passada de cor desenha so ela. Dedo na frente da palma nao
  "soma" transparencia (sem raio-x),
- antebraco some em fade (atributo por vertice da mesh),
- brilho nas pontas do polegar/indicador conforme a pinca fecha.

Saida em alpha PRE-MULTIPLICADO (o que a composicao de janela
transparente espera). Usa o OpenGL do proprio Qt (sem moderngl).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .hand_mesh import HandMesh
from .hand_pose import LM_INDEX_TIP, LM_THUMB_TIP, PosedHand

Color = Tuple[float, float, float]

VERTEX_SHADER = """
#version 330 core
in vec3 in_position;
in vec3 in_normal;
in float in_fade;
uniform mat4 u_proj;
out vec3 v_normal;
out float v_fade;
out vec2 v_px;
void main() {
    gl_Position = u_proj * vec4(in_position, 1.0);
    v_normal = in_normal;
    v_fade = in_fade;
    v_px = in_position.xy;
}
"""

FRAGMENT_SHADER = """
#version 330 core
in vec3 v_normal;
in float v_fade;
in vec2 v_px;
uniform vec3 u_base;
uniform vec3 u_shadow;
uniform vec3 u_rim;
uniform vec3 u_glow;
uniform float u_opacity;
uniform float u_body_alpha;
uniform float u_rim_strength;
uniform vec2 u_tip_a;
uniform vec2 u_tip_b;
uniform float u_pinch;
uniform float u_glow_radius;
out vec4 frag_color;

void main() {
    vec3 n = normalize(v_normal);
    // Espaco de tela: x direita, y PARA BAIXO, z para quem olha.
    vec3 v = vec3(0.0, 0.0, 1.0);
    float ndv = clamp(abs(dot(n, v)), 0.0, 1.0);
    vec3 l = normalize(vec3(-0.35, -0.65, 0.68));
    float lam = dot(n, l) * 0.5 + 0.5;
    lam = lam * lam;
    // Luz de preenchimento de baixo/direita: sombra nao fica chapada.
    float fill = max(dot(n, normalize(vec3(0.5, 0.6, 0.4))), 0.0) * 0.18;
    vec3 color = mix(u_shadow, u_base, lam) + u_base * fill;
    vec3 h = normalize(l + v);
    float spec = pow(max(dot(n, h), 0.0), 32.0) * 0.10;
    float fres = pow(1.0 - ndv, 2.0);
    color += u_rim * fres * u_rim_strength + vec3(spec);

    // Brilho nas pontas do polegar/indicador conforme a pinca fecha.
    float r2 = u_glow_radius * u_glow_radius;
    vec2 da = v_px - u_tip_a;
    vec2 db = v_px - u_tip_b;
    float glow = u_pinch * (exp(-dot(da, da) / r2) + exp(-dot(db, db) / r2));
    color = mix(color, u_glow, clamp(glow, 0.0, 1.0) * 0.75);

    float alpha = (u_body_alpha + (1.0 - u_body_alpha) * fres)
                  * u_opacity * clamp(v_fade, 0.0, 1.0);
    alpha = clamp(alpha + glow * 0.2 * u_opacity, 0.0, 1.0);
    // Teto de brilho: borda/ponta nunca estoura em branco puro.
    color = min(color, vec3(0.94, 0.97, 1.0));
    frag_color = vec4(color * alpha, alpha);
}
"""


@dataclass(frozen=True)
class HandStyle:
    """Paleta e materiais da mao. Cores RGB 0..1."""

    base: Color = (0.68, 0.77, 0.92)
    shadow: Color = (0.26, 0.32, 0.46)
    rim: Color = (0.86, 0.93, 1.00)
    glow: Color = (0.80, 0.93, 1.00)
    body_alpha: float = 0.80
    rim_strength: float = 0.62


STYLE_PRESETS = {
    # Maos de headset (Quest): azul-acinzentado claro, borda luminosa.
    "quest": HandStyle(),
    # Grafite translucido com contorno claro (fundos claros).
    "graphite": HandStyle(
        base=(0.42, 0.45, 0.50),
        shadow=(0.17, 0.18, 0.21),
        rim=(0.95, 0.97, 1.0),
        glow=(0.85, 0.95, 1.0),
        body_alpha=0.70,
        rim_strength=0.95,
    ),
    # Visual antigo: azul escuro com rim ciano.
    "neon": HandStyle(
        base=(0.02, 0.14, 0.50),
        shadow=(0.01, 0.05, 0.22),
        rim=(0.45, 0.85, 1.0),
        glow=(0.55, 0.95, 1.0),
        body_alpha=0.85,
        rim_strength=1.0,
    ),
}


def hex_to_rgb(value: str) -> Color:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"cor invalida: {value!r}")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def ortho_projection(width: float, height: float, depth_range: float = 4000.0) -> np.ndarray:
    """(0,0)-(W,H) com y pra baixo -> NDC; z (px, + = perto) -> depth.

    Retorna column-major (pronto pra upload).
    """
    mat = np.array(
        [
            [2.0 / width, 0.0, 0.0, -1.0],
            [0.0, -2.0 / height, 0.0, 1.0],
            [0.0, 0.0, -1.0 / depth_range, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )
    return mat.T.copy()


# Constantes GL (evita depender de PyOpenGL).
_GL_FLOAT = 0x1406
_GL_UNSIGNED_INT = 0x1405
_GL_TRIANGLES = 0x0004
_GL_DEPTH_TEST = 0x0B71
_GL_BLEND = 0x0BE2
_GL_LESS = 0x0201
_GL_LEQUAL = 0x0203
_GL_ONE = 1
_GL_ONE_MINUS_SRC_ALPHA = 0x0303
_GL_COLOR_BUFFER_BIT = 0x4000
_GL_DEPTH_BUFFER_BIT = 0x0100


class HandGLRenderer:
    """Renderer via OpenGL do proprio Qt (PySide6).

    Nao usa moderngl/glcontext: em Windows com Smart App Control a DLL
    do glcontext pode ser bloqueada e o holograma ficava invisivel. As
    DLLs do Qt sao as mesmas da janela, entao se a janela existe, o
    render funciona.

    Todas as chamadas exigem um QOpenGLContext ATUAL (initializeGL /
    paintGL da janela, ou contexto offscreen no preview).
    """

    _STRIDE = 7 * 4  # pos(3) + normal(3) + fade(1), float32

    def __init__(self, style: Optional[HandStyle] = None, opacity: float = 0.85) -> None:
        self.style = style or STYLE_PRESETS["quest"]
        self.opacity = max(0.0, min(1.0, float(opacity)))
        self._program = None
        self._vao = None
        self._vbo = None
        self._ibo = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def initialize(self) -> bool:
        """Compila shaders e cria buffers. False se o driver nao aguenta."""
        from PySide6.QtOpenGL import (
            QOpenGLBuffer,
            QOpenGLShader,
            QOpenGLShaderProgram,
            QOpenGLVertexArrayObject,
        )

        prog = QOpenGLShaderProgram()
        ok = (
            prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERTEX_SHADER)
            and prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT_SHADER)
            and prog.link()
        )
        if not ok:
            self._last_error = prog.log()
            return False
        vao = QOpenGLVertexArrayObject()
        if not vao.create():
            return False
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        vbo.create()
        vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        vbo.bind()
        ibo = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        ibo.create()
        ibo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        ibo.bind()
        prog.bind()
        for name, size, offset in (
            ("in_position", 3, 0),
            ("in_normal", 3, 12),
            ("in_fade", 1, 24),
        ):
            loc = prog.attributeLocation(name)
            if loc >= 0:
                prog.enableAttributeArray(loc)
                prog.setAttributeBuffer(loc, _GL_FLOAT, offset, size, self._STRIDE)
        vao.release()
        prog.release()
        self._program, self._vao, self._vbo, self._ibo = prog, vao, vbo, ibo
        self._ready = True
        return True

    def render(
        self,
        mesh: HandMesh,
        hand: PosedHand,
        screen_w: float,
        screen_h: float,
    ) -> None:
        """Desenha no framebuffer atual (o chamador limpa cor+depth)."""
        if not self._ready or mesh.empty:
            return
        from PySide6.QtGui import QMatrix4x4, QOpenGLContext, QVector2D, QVector3D
        from shiboken6 import VoidPtr

        gl_ctx = QOpenGLContext.currentContext()
        if gl_ctx is None:
            return
        f = gl_ctx.functions()

        n_v = int(mesh.vertices.shape[0])
        n_i = int(mesh.indices.shape[0])
        inter = np.empty((n_v, 7), dtype=np.float32)
        inter[:, 0:3] = mesh.vertices
        inter[:, 3:6] = mesh.normals
        inter[:, 6] = mesh.fade
        vbytes = inter.tobytes()
        ibytes = mesh.indices.astype(np.uint32).tobytes()

        prog = self._program
        self._vao.bind()
        self._vbo.bind()
        self._vbo.allocate(vbytes, len(vbytes))
        self._ibo.bind()
        self._ibo.allocate(ibytes, len(ibytes))
        prog.bind()

        proj = ortho_projection(screen_w, screen_h).T  # row-major p/ QMatrix4x4
        prog.setUniformValue("u_proj", QMatrix4x4(*proj.flatten().tolist()))
        st = self.style
        prog.setUniformValue("u_base", QVector3D(*st.base))
        prog.setUniformValue("u_shadow", QVector3D(*st.shadow))
        prog.setUniformValue("u_rim", QVector3D(*st.rim))
        prog.setUniformValue("u_glow", QVector3D(*st.glow))
        prog.setUniformValue1f("u_opacity", float(self.opacity))
        prog.setUniformValue1f("u_body_alpha", float(st.body_alpha))
        prog.setUniformValue1f("u_rim_strength", float(st.rim_strength))
        a = hand.points[LM_THUMB_TIP]
        b = hand.points[LM_INDEX_TIP]
        prog.setUniformValue("u_tip_a", QVector2D(float(a[0]), float(a[1])))
        prog.setUniformValue("u_tip_b", QVector2D(float(b[0]), float(b[1])))
        prog.setUniformValue1f("u_pinch", float(hand.pinch))
        prog.setUniformValue1f("u_glow_radius", float(max(2.0, hand.palm_px * 0.16)))

        f.glEnable(_GL_DEPTH_TEST)
        f.glEnable(_GL_BLEND)
        # 1) depth pre-pass: so profundidade.
        f.glColorMask(False, False, False, False)
        f.glDepthMask(True)
        f.glDepthFunc(_GL_LESS)
        f.glDrawElements(_GL_TRIANGLES, n_i, _GL_UNSIGNED_INT, VoidPtr(0))
        # 2) cor so na superficie mais proxima (camada unica translucida).
        f.glColorMask(True, True, True, True)
        f.glDepthMask(False)
        f.glDepthFunc(_GL_LEQUAL)
        f.glBlendFuncSeparate(
            _GL_ONE,
            _GL_ONE_MINUS_SRC_ALPHA,
            _GL_ONE,
            _GL_ONE_MINUS_SRC_ALPHA,
        )
        f.glDrawElements(_GL_TRIANGLES, n_i, _GL_UNSIGNED_INT, VoidPtr(0))
        f.glDepthMask(True)
        f.glDisable(_GL_DEPTH_TEST)
        prog.release()
        self._vao.release()

    def release(self) -> None:
        for attr in ("_vao", "_vbo", "_ibo"):
            obj = getattr(self, attr)
            if obj is not None:
                with contextlib.suppress(Exception):
                    obj.destroy()
                setattr(self, attr, None)
        self._program = None
        self._ready = False


def clear_framebuffer() -> None:
    """Limpa cor (transparente) + depth do framebuffer atual."""
    from PySide6.QtGui import QOpenGLContext

    gl_ctx = QOpenGLContext.currentContext()
    if gl_ctx is None:
        return
    f = gl_ctx.functions()
    f.glClearColor(0.0, 0.0, 0.0, 0.0)
    f.glClearDepthf(1.0)
    f.glDepthMask(True)
    f.glClear(_GL_COLOR_BUFFER_BIT | _GL_DEPTH_BUFFER_BIT)
