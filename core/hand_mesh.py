"""
core/hand_mesh.py
=================

Geometria 3D PROCEDURAL da mao (estilo maos de headset VR: superficie
lisa, dedos com volume, antebraco que some em fade).

Funcao PURA — sem Qt, sem GL, sem OpenCV. Recebe a pose ja interpretada
(`core.hand_pose.PosedHand`: pontos em pixels de tela + profundidade) e
devolve arrays prontos pra VBO/EBO:
    - vertices: (N, 3) float32 — x, y em px; z = profundidade (px, + = perto)
    - normals:  (N, 3) float32 — normais unitarias pra iluminacao
    - fade:     (N,)   float32 — 1 = opaco, 0 = some (fim do antebraco)
    - indices:  (M,)   uint32  — triangulos

Modelagem:
- PALMA + ANTEBRACO: cada secao vai da borda do lado do polegar ate a
  borda do minimo, interpolando entre a linha do pulso e a LINHA DOS NOS
  (indicador..minimo, inclinada como na mao real) — os MCPs ficam dentro
  da borda. Bulge da tenar pro lado do polegar, borda dos nos
  arredondada, antebraco reto que some em fade.
- DEDOS: tubo continuo raiz->MCP->PIP->DIP->ponta (Catmull-Rom), secao
  eliptica achatada dorso-palma, raio proporcional a PALMA (fechar a mao
  nao muda a espessura). A raiz nasce DENTRO da palma; as duas pontas tem
  tampa hemisferica (o apice da ponta cai no landmark).
- POLEGAR: nasce na "teia" da tenar (entre pulso e indicador).
- Quadros por TRANSPORTE PARALELO: dedo apontando pra camera nao torce.
- Indices por TEMPLATE cacheado: nada de loop Python por triangulo.

O renderer desenha com depth pre-pass: so a superficie mais proxima
aparece, o que da a translucidez "de camada unica" das maos de VR.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .hand_pose import HandPoseSolver, PosedHand

# Cadeias de landmarks por dedo (MediaPipe Hands)
THUMB_CHAIN = (1, 2, 3, 4)
INDEX_CHAIN = (5, 6, 7, 8)
MIDDLE_CHAIN = (9, 10, 11, 12)
RING_CHAIN = (13, 14, 15, 16)
PINKY_CHAIN = (17, 18, 19, 20)
FINGER_CHAINS = (THUMB_CHAIN, INDEX_CHAIN, MIDDLE_CHAIN, RING_CHAIN, PINKY_CHAIN)

# Raio (raiz, MCP, PIP, DIP, ponta) em fracao do comprimento da palma.
# Dedo humano ~1.8-2 cm de largura pra palma de ~9.5 cm.
# Polegar: raiz = teia da tenar, depois CMC..ponta.
FINGER_RADII: Tuple[Tuple[float, ...], ...] = (
    (0.150, 0.170, 0.150, 0.128, 0.112),  # polegar
    (0.100, 0.126, 0.116, 0.103, 0.090),  # indicador
    (0.105, 0.131, 0.121, 0.106, 0.092),  # medio
    (0.098, 0.122, 0.112, 0.100, 0.087),  # anelar
    (0.085, 0.107, 0.098, 0.088, 0.077),  # minimo
)
FINGER_ELLIPSE_RATIO = 0.86  # secao achatada: profundidade/largura
# Raiz do dedo: quanto entra na palma, em direcao ao CENTRO dela (fracao
# da palma). Raiz mais fina que o MCP: fica escondida dentro da palma.
FINGER_ROOT_DEPTH = 0.26
# Raiz do polegar = ponto da teia (fracao do segmento pulso->MCP indicador).
THUMB_ROOT_ALONG = 0.32

FINGER_RING_SEGMENTS = 16
FINGER_SAMPLES_PER_SEGMENT = 3
CAP_RINGS = 4

PALM_RING_SEGMENTS = 24
# Perfil da palma (t: 0 = pulso, 1 = linha dos nos; negativo = antebraco):
# (t, espessura relativa a palma, fade).
_PALM_PROFILE: Tuple[Tuple[float, float, float], ...] = (
    (-0.75, 0.42, 0.00),
    (-0.50, 0.40, 0.25),
    (-0.25, 0.37, 0.70),
    (0.00, 0.35, 1.00),
    (0.20, 0.34, 1.00),
    (0.40, 0.33, 1.00),
    (0.60, 0.32, 1.00),
    (0.80, 0.31, 1.00),
    (0.95, 0.30, 1.00),
    (1.00, 0.29, 1.00),
)
# Arredondamento da borda dos nos: (avanco em palmas, escala da largura,
# escala da espessura) relativos ao ultimo anel do perfil.
_PALM_TOP_ROUND: Tuple[Tuple[float, float, float], ...] = (
    (0.05, 0.96, 0.90),
    (0.10, 0.86, 0.72),
    (0.145, 0.66, 0.48),
    (0.17, 0.36, 0.22),
)
PALM_WRIST_WIDTH = 0.62   # largura do pulso (fracao da palma)
PALM_THENAR_BULGE = 0.16  # bulge lateral max da tenar (fracao da palma)


@dataclass(frozen=True)
class HandMesh:
    vertices: np.ndarray
    normals: np.ndarray
    fade: np.ndarray
    indices: np.ndarray

    @property
    def empty(self) -> bool:
        return self.indices.size == 0


def _empty_mesh() -> HandMesh:
    return HandMesh(
        np.zeros((0, 3), np.float32),
        np.zeros((0, 3), np.float32),
        np.zeros((0,), np.float32),
        np.zeros((0,), np.uint32),
    )


# ---------------------------------------------------------------- tables

@lru_cache(maxsize=8)
def _circle(segments: int) -> Tuple[np.ndarray, np.ndarray]:
    ang = np.linspace(0.0, 2.0 * np.pi, segments, endpoint=False)
    return np.cos(ang), np.sin(ang)


@lru_cache(maxsize=32)
def _grid_indices(rings: int, segments: int) -> np.ndarray:
    """Triangulos ligando aneis consecutivos (rings x segments), base 0."""
    r = np.arange(rings - 1)[:, None]
    s = np.arange(segments)[None, :]
    s2 = (s + 1) % segments
    i0 = r * segments + s
    i1 = r * segments + s2
    i2 = (r + 1) * segments + s
    i3 = (r + 1) * segments + s2
    tris = np.stack([i0, i1, i2, i1, i3, i2], axis=-1)
    return tris.reshape(-1).astype(np.uint32)


@lru_cache(maxsize=8)
def _fan_indices(segments: int, apex_first: bool = False) -> np.ndarray:
    """Leque anel<->apice. apex_first=False: anel em 0..seg-1 e apice em
    `segments`. True: apice em 0 e anel em 1..seg."""
    s = np.arange(segments)
    s2 = (s + 1) % segments
    if apex_first:
        apex = np.zeros(segments, dtype=np.int64)
        tri = np.stack([apex, s2 + 1, s + 1], axis=-1)
    else:
        apex = np.full(segments, segments)
        tri = np.stack([apex, s, s2], axis=-1)
    return tri.reshape(-1).astype(np.uint32)


# ------------------------------------------------------------- geometry

def _catmull_rom(points: np.ndarray, samples: int) -> np.ndarray:
    """Subdivide (N,3) inserindo `samples` pontos por segmento."""
    n = len(points)
    if n < 2 or samples < 1:
        return points
    padded = np.vstack([points[:1], points, points[-1:]])
    t = (np.arange(1, samples + 1, dtype=np.float64) / (samples + 1))[:, None]
    t2 = t * t
    t3 = t2 * t
    out = [points[0]]
    for i in range(n - 1):
        p0, p1, p2, p3 = padded[i], padded[i + 1], padded[i + 2], padded[i + 3]
        seg = 0.5 * (
            2.0 * p1
            + (-p0 + p2) * t
            + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
            + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
        )
        out.extend(seg)
        out.append(p2)
    return np.asarray(out, dtype=np.float64)


def _tangents(centers: np.ndarray) -> np.ndarray:
    tan = np.empty_like(centers)
    tan[1:-1] = centers[2:] - centers[:-2]
    tan[0] = centers[1] - centers[0]
    tan[-1] = centers[-1] - centers[-2]
    lens = np.linalg.norm(tan, axis=1, keepdims=True)
    fallback = np.array([0.0, -1.0, 0.0])
    return np.where(lens > 1e-9, tan / np.maximum(lens, 1e-9), fallback)


def _transport_frames(
    tangents: np.ndarray, first_u: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Quadros (u, v) por transporte paralelo — sem torcao entre aneis."""
    n = len(tangents)
    us = np.empty((n, 3))
    vs = np.empty((n, 3))
    t0 = tangents[0]
    if first_u is None or float(np.linalg.norm(first_u)) < 1e-9:
        ref = np.array([0.0, 0.0, 1.0])
        if abs(float(np.dot(ref, t0))) > 0.9:
            ref = np.array([1.0, 0.0, 0.0])
        u = np.cross(t0, ref)
    else:
        u = np.asarray(first_u, dtype=np.float64)
    for i in range(n):
        t = tangents[i]
        u = u - t * float(np.dot(u, t))
        norm = float(np.linalg.norm(u))
        if norm < 1e-9:
            ref = np.array([1.0, 0.0, 0.0]) if abs(t[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
            u = np.cross(t, ref)
            norm = float(np.linalg.norm(u))
        u = u / norm
        us[i] = u
        vs[i] = np.cross(t, u)
    return us, vs


def _ring_block(
    centers: np.ndarray,
    us: np.ndarray,
    vs: np.ndarray,
    r_u: np.ndarray,
    r_v: np.ndarray,
    segments: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Aneis elipticos: posicoes e normais (rings*segments, 3)."""
    cos, sin = _circle(segments)
    cu = cos[None, :, None]
    sv = sin[None, :, None]
    pos = (
        centers[:, None, :]
        + us[:, None, :] * (r_u[:, None, None] * cu)
        + vs[:, None, :] * (r_v[:, None, None] * sv)
    )
    nrm = (
        us[:, None, :] * (cu / np.maximum(r_u, 1e-6)[:, None, None])
        + vs[:, None, :] * (sv / np.maximum(r_v, 1e-6)[:, None, None])
    )
    nrm /= np.maximum(np.linalg.norm(nrm, axis=2, keepdims=True), 1e-9)
    return pos.reshape(-1, 3), nrm.reshape(-1, 3)


def _cap_rings(
    center: np.ndarray,
    direction: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    radius: float,
    toward_apex: bool,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hemisferio sobre o anel (center, radius) apontando pra `direction`.

    Aneis ordenados do tubo pro apice (toward_apex) ou do apice pro tubo.
    Retorna posicoes, normais e o apice.
    """
    seg = FINGER_RING_SEGMENTS
    thetas = (np.arange(1, CAP_RINGS + 1) / (CAP_RINGS + 1)) * (np.pi / 2.0)
    if not toward_apex:
        thetas = thetas[::-1]
    centers = center[None, :] + direction[None, :] * (radius * np.sin(thetas))[:, None]
    r = radius * np.cos(thetas)
    pos, side = _ring_block(
        centers,
        np.repeat(u[None, :], CAP_RINGS, axis=0),
        np.repeat(v[None, :], CAP_RINGS, axis=0),
        r,
        r * FINGER_ELLIPSE_RATIO,
        seg,
    )
    blend = np.repeat(np.sin(thetas), seg)[:, None]
    nrm = side * (1.0 - blend) + direction[None, :] * blend
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    return pos, nrm, center + direction * radius


def _finger(
    chain_pts: np.ndarray, radii: np.ndarray, dorsal_hint: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Tubo continuo raiz->ponta com tampas arredondadas nas duas pontas.

    chain_pts: (K, 3) raiz (dentro da palma), MCP, PIP, DIP, ponta.
    radii: (K,) raio em px por ponto.
    """
    seg = FINGER_RING_SEGMENTS
    pts = chain_pts.astype(np.float64).copy()
    rad = radii.astype(np.float64)
    # Apice da tampa cai no landmark da ponta: recua o centro do ultimo anel.
    tip_dir = pts[-1] - pts[-2]
    tip_len = float(np.linalg.norm(tip_dir))
    if tip_len > 1e-6:
        pts[-1] = pts[-1] - tip_dir / tip_len * min(rad[-1] * 0.9, tip_len * 0.6)

    centers = _catmull_rom(pts, FINGER_SAMPLES_PER_SEGMENT)
    n_dense = len(centers)
    pos_param = np.linspace(0.0, len(rad) - 1, n_dense)
    idx = np.minimum(pos_param.astype(int), len(rad) - 2)
    f = pos_param - idx
    f = f * f * (3.0 - 2.0 * f)
    r = rad[idx] + (rad[idx + 1] - rad[idx]) * f

    tans = _tangents(centers)
    us, vs = _transport_frames(tans, first_u=dorsal_hint)
    tube_pos, tube_nrm = _ring_block(centers, us, vs, r, r * FINGER_ELLIPSE_RATIO, seg)
    s_pos, s_nrm, s_apex = _cap_rings(centers[0], -tans[0], us[0], vs[0], float(r[0]), False)
    e_pos, e_nrm, e_apex = _cap_rings(centers[-1], tans[-1], us[-1], vs[-1], float(r[-1]), True)

    # Ordem: apice inicial, tampa inicial, tubo, tampa final, apice final.
    verts = np.vstack([s_apex[None, :], s_pos, tube_pos, e_pos, e_apex[None, :]])
    norms = np.vstack([-tans[0][None, :], s_nrm, tube_nrm, e_nrm, tans[-1][None, :]])
    n_rings = CAP_RINGS + n_dense + CAP_RINGS
    last_ring = 1 + (n_rings - 1) * seg
    indices = np.concatenate([
        _fan_indices(seg, True),
        _grid_indices(n_rings, seg) + np.uint32(1),
        _fan_indices(seg) + np.uint32(last_ring),
    ])
    return verts, norms, indices


def _palm(
    points: np.ndarray, palm_px: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Palma + antebraco seguindo a linha real dos nos (ver docstring)."""
    wrist = points[0]
    index_mcp = points[5]
    pinky_mcp = points[17]
    thumb_cmc = points[1]

    knuckle_mid = (index_mcp + pinky_mcp) * 0.5
    main = knuckle_mid - wrist
    length = float(np.linalg.norm(main))
    across = index_mcp - pinky_mcp
    if length < 1e-6 or float(np.linalg.norm(across)) < 1e-6:
        empty = np.zeros((0, 3))
        return empty, empty, np.zeros(0), np.zeros(0, np.uint32)
    main_u = main / length
    lateral = across - main_u * float(np.dot(across, main_u))
    lateral /= max(float(np.linalg.norm(lateral)), 1e-9)
    normal = np.cross(main_u, lateral)
    normal /= max(float(np.linalg.norm(normal)), 1e-9)
    thumb_sign = 1.0 if float(np.dot(thumb_cmc - wrist, lateral)) > 0 else -1.0

    # Bordas: linha dos nos + folga do raio dos dedos das pontas.
    idx_edge = index_mcp + lateral * (FINGER_RADII[1][1] * palm_px * 1.05)
    pky_edge = pinky_mcp - lateral * (FINGER_RADII[4][1] * palm_px * 1.10)
    half_w = 0.5 * PALM_WRIST_WIDTH * palm_px
    wrist_a = wrist + lateral * half_w  # lado do indicador
    wrist_b = wrist - lateral * half_w  # lado do minimo

    # Arco dos nos: o MCP do medio fica acima da reta indicador-minimo.
    arch = max(0.0, float(np.dot(points[9] - knuckle_mid, main_u)))

    rows_l: List[np.ndarray] = []
    rows_r: List[np.ndarray] = []
    rows_th: List[float] = []
    rows_fade: List[float] = []
    for t, thick, fade_t in _PALM_PROFILE:
        if t <= 0.0:
            left = wrist_a + main_u * (t * length)
            right = wrist_b + main_u * (t * length)
        else:
            s = t * t * (3.0 - 2.0 * t) * 0.35 + t * 0.65  # alarga cedo
            left = wrist_a + (idx_edge - wrist_a) * s
            right = wrist_b + (pky_edge - wrist_b) * s
            if t > 0.55:
                lift = main_u * (arch * ((t - 0.55) / 0.45) ** 2)
                left = left + lift * 0.4
                right = right + lift * 0.4
            if 0.05 < t < 0.75:  # tenar
                bulge = float(np.sin((t - 0.05) / 0.70 * np.pi)) * PALM_THENAR_BULGE * palm_px
                if thumb_sign > 0:
                    left = left + lateral * bulge
                else:
                    right = right - lateral * bulge
        rows_l.append(left)
        rows_r.append(right)
        rows_th.append(thick)
        rows_fade.append(fade_t)
    # Borda dos nos arredondada.
    l_last, r_last = rows_l[-1], rows_r[-1]
    mid_last = (l_last + r_last) * 0.5 + main_u * (arch * 0.6)
    th_last = _PALM_PROFILE[-1][1]
    for adv, ws, ts in _PALM_TOP_ROUND:
        c = mid_last + main_u * (adv * length)
        rows_l.append(c + (l_last - mid_last) * ws)
        rows_r.append(c + (r_last - mid_last) * ws)
        rows_th.append(th_last * ts)
        rows_fade.append(1.0)

    left_pts = np.asarray(rows_l)
    right_pts = np.asarray(rows_r)
    centers = (left_pts + right_pts) * 0.5
    span = left_pts - right_pts
    r_lat = 0.5 * np.linalg.norm(span, axis=1)
    us = span / np.maximum(r_lat[:, None] * 2.0, 1e-9)
    vs = np.cross(us, np.repeat(main_u[None, :], len(us), axis=0))
    vs *= np.sign(np.sum(vs * normal[None, :], axis=1, keepdims=True) + 1e-12)
    vs /= np.maximum(np.linalg.norm(vs, axis=1, keepdims=True), 1e-9)
    r_dep = 0.5 * palm_px * np.asarray(rows_th)
    pos, nrm = _ring_block(centers, us, vs, r_lat, r_dep, PALM_RING_SEGMENTS)

    # Inclina as normais no eixo onde a secao encolhe (topo arredondado).
    axis_pos = np.concatenate(
        [[0.0], np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))],
    )
    size = np.sqrt(r_lat * r_dep)
    dr = np.gradient(size) / np.maximum(np.gradient(axis_pos), 1e-6)
    nrm = nrm - np.repeat(dr, PALM_RING_SEGMENTS)[:, None] * main_u[None, :] * 0.8
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    fade = np.repeat(np.asarray(rows_fade), PALM_RING_SEGMENTS)

    n = len(centers)
    apex = centers[-1] + main_u * (0.012 * length)
    verts = np.vstack([pos, apex[None, :]])
    norms = np.vstack([nrm, main_u[None, :]])
    fade = np.concatenate([fade, [1.0]])
    idx = np.concatenate([
        _grid_indices(n, PALM_RING_SEGMENTS),
        _fan_indices(PALM_RING_SEGMENTS) + np.uint32((n - 1) * PALM_RING_SEGMENTS),
    ])
    return verts, norms, fade, idx


def build_hand_mesh(hand: PosedHand) -> HandMesh:
    """Mesh completa (palma+antebraco+5 dedos) a partir da pose."""
    points = np.asarray(hand.points, dtype=np.float64)
    if points.shape[0] < 21:
        return _empty_mesh()
    palm_px = max(float(hand.palm_px), 1.0)

    parts_v: List[np.ndarray] = []
    parts_n: List[np.ndarray] = []
    parts_f: List[np.ndarray] = []
    parts_i: List[np.ndarray] = []
    base = 0

    pv, pn, pf, pi = _palm(points, palm_px)
    if len(pv):
        parts_v.append(pv)
        parts_n.append(pn)
        parts_f.append(pf)
        parts_i.append(pi)
        base += len(pv)

    n_palm = np.cross(points[5] - points[0], points[17] - points[0])
    n_len = float(np.linalg.norm(n_palm))
    dorsal = n_palm / n_len if n_len > 1e-9 else np.array([0.0, 0.0, 1.0])
    palm_axis = (points[5] + points[17]) * 0.5 - points[0]
    pa_len = float(np.linalg.norm(palm_axis))
    palm_axis = palm_axis / pa_len if pa_len > 1e-9 else np.array([0.0, -1.0, 0.0])
    palm_center = points[0] + palm_axis * (0.45 * pa_len)

    for chain_idx, chain in enumerate(FINGER_CHAINS):
        joints = points[list(chain)]
        if chain_idx == 0:
            # Teia do polegar: nasce dentro da palma, entre pulso e indicador.
            root = points[0] + (points[5] - points[0]) * THUMB_ROOT_ALONG
        else:
            # Raiz afundada na palma, em direcao ao centro dela.
            to_center = palm_center - joints[0]
            tc_len = float(np.linalg.norm(to_center))
            to_center = to_center / tc_len if tc_len > 1e-9 else -palm_axis
            root = joints[0] + to_center * (FINGER_ROOT_DEPTH * palm_px)
        chain_pts = np.vstack([root[None, :], joints])
        radii = np.asarray(FINGER_RADII[chain_idx]) * palm_px
        hint = np.cross(dorsal, joints[1] - joints[0])
        fv, fn, fi = _finger(chain_pts, radii, hint)
        parts_v.append(fv)
        parts_n.append(fn)
        parts_f.append(np.ones(len(fv)))
        parts_i.append(fi + np.uint32(base))
        base += len(fv)

    return HandMesh(
        vertices=np.vstack(parts_v).astype(np.float32),
        normals=np.vstack(parts_n).astype(np.float32),
        fade=np.concatenate(parts_f).astype(np.float32),
        indices=np.concatenate(parts_i).astype(np.uint32),
    )


def generate_hand_mesh(
    landmarks: Sequence[Tuple[float, float, float]],
    center_x: float,
    center_y: float,
    size_px: float,
    frame_aspect: float = 16.0 / 9.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compat: pose de um unico frame (sem suavizacao) -> (verts, normais,
    indices). No render continuo use HandPoseSolver + build_hand_mesh."""
    posed = None
    if len(landmarks) >= 21:
        posed = HandPoseSolver(hand_size_px=size_px, frame_aspect=frame_aspect).solve(
            landmarks, center_x, center_y,
        )
    m = _empty_mesh() if posed is None else build_hand_mesh(posed)
    return m.vertices, m.normals, m.indices
