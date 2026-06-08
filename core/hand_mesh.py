"""
core/hand_mesh.py
=================

Geometria 3D PROCEDURAL da mao a partir de 21 landmarks MediaPipe.

Funcao PURA — sem Qt, sem GL, sem OpenCV. Recebe landmarks normalizados +
tamanho alvo e retorna 3 numpy arrays prontos pra upload em VBO/EBO:
    - vertices: (N, 3) float32 — posicoes em screen pixel space
    - normals:  (N, 3) float32 — normais de superficie (pra shader fresnel)
    - indices:  (M,)   uint32  — triangle indices

Modelagem ANATOMICA (v6.9.8.6):
- PALMA (`_build_anatomical_palm`): lofted surface via 7 rings eliticos
  ao longo do eixo wrist→MCPs. Largura variavel (estreito wrist, cheio
  MCPs), thenar bulge sutil offset lateral, depth convexa no centro.
  Caps fan no top + bottom.
- DEDOS (`_build_swept_finger`): Catmull-Rom subdivision (4→10 rings)
  + cross-section eliptica (ratio depth/lateral = 0.80) + hemisphere
  tip cap (3 rings + apex). Knuckle bulge no PIP via SEGMENT_TAPER.
- POLEGAR: mesma logica de dedo mas com THUMB_TAPER (base musculosa)
  + THUMB_MCP_INSET_FRAC = 0.30 (3x os outros) pra CMC enterrar no
  thenar do palm, evitando look "polegar colado de fora".

Princípio de design: geometria SOZINHA (sem shader) ja le como uma
mao humana simplificada. Shader holografico complementa, nao mascara
problemas de modelagem.

Reuso intencional do scaling/anchor de hand_renderer.py pra que o
holograma 3D fique alinhado com o cursor IDENTICAMENTE.

Mesh final: ~770 verts / ~1500 tris. ~1.5ms gen em Python+numpy.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np


# Cadeias de landmarks por dedo (matches hand_renderer.py)
THUMB_CHAIN = (1, 2, 3, 4)
INDEX_CHAIN = (5, 6, 7, 8)
MIDDLE_CHAIN = (9, 10, 11, 12)
RING_CHAIN = (13, 14, 15, 16)
PINKY_CHAIN = (17, 18, 19, 20)
FINGER_CHAINS = (THUMB_CHAIN, INDEX_CHAIN, MIDDLE_CHAIN, RING_CHAIN, PINKY_CHAIN)

# Largura base por dedo (fracao de size_px) — mesmo do hand_renderer.py
FINGER_WIDTH_FACTOR = (0.095, 0.075, 0.082, 0.072, 0.060)

# Fator de afilamento por joint (MCP->PIP->DIP->TIP).
# v6.9.8.2: knuckle bulge no PIP (junta media) + taper natural no TIP.
# Anatomia humana: dedo nao e' cone — tem leve bump na junta intermediaria.
# Index 0=MCP (base), 1=PIP (knuckle), 2=DIP (afina), 3=TIP (redondo).
SEGMENT_TAPER = (0.90, 1.00, 0.85, 0.65)

# Polegar tem proporcoes distintas — base musculosa, sem knuckle no IP.
THUMB_TAPER = (1.05, 0.95, 0.82, 0.68)

# Encolhe ligeiramente o MCP pra ele ficar DENTRO do palm,
# criando fusao visual sem precisar de boolean mesh union.
MCP_INSET_FRAC = 0.12

# v6.9.8.8: polegar inset aumentado pra 0.50 = polegar nasce do CENTRO
# da thenar, nao de fora. Sem isso parece "tubo grudado lateralmente".
THUMB_MCP_INSET_FRAC = 0.50

# Ancora visual = midpoint pinch_left (mesmo de hand_renderer)
_PINCH_ANCHOR = (4, 8)

# Resolution da malha (compromisso visual / performance)
FINGER_RING_SEGMENTS = 10  # verts por anel dos dedos
PALM_RING_COUNT = 7        # cross-sections ao longo do eixo wrist→top
PALM_RING_SEGMENTS = 16    # verts por ring eliptico da palma

# v6.9.8.8: angle tables pre-computadas globalmente — usadas em
# vectorized ring vertex generation pra evitar math.cos/sin per-vert
# nos hot loops (12ms → ~2ms target).
_FINGER_ANGLES = np.linspace(
    0, 2 * np.pi, FINGER_RING_SEGMENTS, endpoint=False, dtype=np.float32,
)
_FINGER_COS = np.cos(_FINGER_ANGLES).astype(np.float32)
_FINGER_SIN = np.sin(_FINGER_ANGLES).astype(np.float32)
_PALM_ANGLES = np.linspace(
    0, 2 * np.pi, PALM_RING_SEGMENTS, endpoint=False, dtype=np.float32,
)
_PALM_COS = np.cos(_PALM_ANGLES).astype(np.float32)
_PALM_SIN = np.sin(_PALM_ANGLES).astype(np.float32)

# Ratio depth/lateral pra cross-section dos dedos (1.0 = circular).
# Dedos humanos sao levemente flatter palmar-dorsal vs lateral.
FINGER_ELLIPSE_RATIO = 0.80

# Profundidade maxima da palma (no centro) como fracao de size_px.
# v6.9.8.10: 0.10 → 0.05. Antes criava "ridge" vertical visivel no
# meio da palma (bulge 3D + fresnel rim split). Agora palma FLAT como
# anatomia real (palm e' essencialmente um slab fino), sem ridge.
PALM_DEPTH_MAX_FRAC = 0.05

# Thenar bulge — quanto desloca o ring center na direcao do polegar.
# v6.9.8.8: 0.04 → 0.08 — thenar mais pronunciada pra absorver visualmente
# a base do polegar (que tambem foi puxada via THUMB_MCP_INSET_FRAC=0.50).
PALM_THENAR_BULGE_FRAC = 0.08

# Extensao do palm abaixo do landmark wrist. v6.9.8.8: 0.04 → 0.0.
# Antes criava "stub" de antebraco; agora palm para limpa no wrist.
PALM_WRIST_EXTEND_FRAC = 0.0


def _empty_mesh() -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.zeros((0, 3), dtype=np.float32),
        np.zeros((0, 3), dtype=np.float32),
        np.zeros((0,), dtype=np.uint32),
    )


def _catmull_rom_pos(
    p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray,
    t: float,
) -> np.ndarray:
    """
    Catmull-Rom spline interpolation entre p1 e p2 (passa pelos 2).
    p0 e p3 sao controles de tangente (vizinhos). t em [0, 1].
    Tension 0.5 (default classico).
    """
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2.0 * p1)
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
    )


def _subdivide_chain_catmull_rom(
    joints: List[np.ndarray],
    samples_per_segment: int,
) -> List[np.ndarray]:
    """
    Subdivide a cadeia de joints inserindo pontos intermediarios via
    Catmull-Rom. Preserva os joints originais nas posicoes corretas.

    Args:
        joints: lista de N pontos de controle (>= 2)
        samples_per_segment: quantos pontos NOVOS entre cada par adjacente

    Returns:
        Lista de M pontos onde M = N + (N-1) * samples_per_segment.
        Joints originais permanecem nos seus indices.
    """
    n = len(joints)
    if n < 2 or samples_per_segment < 1:
        return list(joints)

    out: List[np.ndarray] = [joints[0]]
    for i in range(n - 1):
        # Pontos de controle pra Catmull-Rom: vizinhos extendidos
        p0 = joints[max(0, i - 1)]
        p1 = joints[i]
        p2 = joints[i + 1]
        p3 = joints[min(n - 1, i + 2)]

        for s in range(1, samples_per_segment + 1):
            t = s / (samples_per_segment + 1)
            pos = _catmull_rom_pos(p0, p1, p2, p3, t)
            out.append(pos)
        out.append(p2)
    return out


def _interpolate_widths_smoothstep(
    widths: List[float],
    target_n: int,
) -> List[float]:
    """
    Interpola lista de widths pra target_n elementos via smoothstep.
    Mantém os valores originais nos joints "principais" (knuckle bulge
    no PIP preservado) com transicoes naturais entre eles.
    """
    n_orig = len(widths)
    if n_orig == 0 or target_n <= 0:
        return list(widths)
    if n_orig == 1:
        return [widths[0]] * target_n

    out: List[float] = []
    for i in range(target_n):
        pos = i * (n_orig - 1) / max(1, target_n - 1)
        idx = int(pos)
        if idx >= n_orig - 1:
            out.append(widths[-1])
            continue
        frac = pos - idx
        s = 3.0 * frac * frac - 2.0 * frac * frac * frac
        out.append(widths[idx] + (widths[idx + 1] - widths[idx]) * s)
    return out


def _perpendicular_basis(tangent: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Constroi base perpendicular (u, v) ortogonal a `tangent`.
    Tangent deve ser unit. Resultado u, v tambem unit.
    """
    ref = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    if abs(float(np.dot(ref, tangent))) > 0.9:
        ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    u = np.cross(tangent, ref)
    u = u / (float(np.linalg.norm(u)) + 1e-9)
    v = np.cross(tangent, u)
    return u, v


def _build_swept_finger(
    joints: List[np.ndarray],
    radii: List[float],
    segments: int,
    base_idx: int,
    palm_center: np.ndarray,
    add_tip_cap: bool = True,
    mcp_inset_frac: float = MCP_INSET_FRAC,
) -> Tuple[List, List, List]:
    """
    Constroi superficie continua ao longo da cadeia de joints.

    Para cada joint:
      - Computa tangent (direcao do dedo nesse ponto, smoothing entre vizinhos)
      - Constroi base perpendicular (u, v)
      - Gera ring de `segments` vertices em torno do joint com radii[i]
      - Conecta ao ring anterior com 2 triangles por segment

    O MCP (joint[0]) e' deslocado LIGEIRAMENTE em direcao ao palm_center
    pra criar overlap visual com o palm — fusao sem mesh boolean.
    `mcp_inset_frac` configurável: dedos normais 0.12, polegar 0.30
    (anatomia thenar precisa do polegar mais "enterrado" no palm).

    Returns: verts, normals, indices (relative to base_idx).
    """
    n_joints_orig = len(joints)
    if n_joints_orig < 2:
        return [], [], []

    # Ajusta MCP em direcao ao palm pra fusao visual
    mcp_to_palm = palm_center - joints[0]
    mcp_dist = float(np.linalg.norm(mcp_to_palm))
    joints_adjusted: List[np.ndarray] = list(joints)
    if mcp_dist > 1e-6:
        mcp_dir = mcp_to_palm / mcp_dist
        joints_adjusted[0] = joints[0] + mcp_dir * mcp_dist * mcp_inset_frac

    # v6.9.8.4: subdivide a chain via Catmull-Rom + interpola widths
    # smoothstep. Antes: 4 rings lineares = "elbows" visiveis nos joints.
    # Agora: ~10 rings com curvatura natural = silhueta organica de dedo.
    SAMPLES_PER_SEGMENT = 2  # 4 joints + 3*2 = 10 rings
    joints_dense = _subdivide_chain_catmull_rom(
        joints_adjusted, samples_per_segment=SAMPLES_PER_SEGMENT,
    )
    radii_dense = _interpolate_widths_smoothstep(
        list(radii), len(joints_dense),
    )
    n_rings = len(joints_dense)

    # v6.9.8.8: VECTORIZED ring generation.
    # Antes: nested Python loop 13×10=130 iteracoes com sin/cos por vert.
    # Agora: precomputa tudo em arrays, gera ring inteiro com broadcasting
    # (1 numpy op em vez de 10 Python ops).
    # Use angle tables globais se segments == FINGER_RING_SEGMENTS, senao
    # gera dinamico (fallback defensivo).
    if segments == FINGER_RING_SEGMENTS:
        cos_table = _FINGER_COS
        sin_table = _FINGER_SIN
    else:
        cos_table = np.cos(np.linspace(
            0, 2 * np.pi, segments, endpoint=False, dtype=np.float32,
        ))
        sin_table = np.sin(np.linspace(
            0, 2 * np.pi, segments, endpoint=False, dtype=np.float32,
        ))

    # Buffer pre-alocado pra ring positions/normals (segments × 3)
    all_ring_pos: List[np.ndarray] = []
    all_ring_norm: List[np.ndarray] = []

    for j_idx in range(n_rings):
        # Tangent smoothed (mesmo logic, mais conciso)
        if j_idx == 0:
            tangent = joints_dense[1] - joints_dense[0]
        elif j_idx == n_rings - 1:
            tangent = joints_dense[-1] - joints_dense[-2]
        else:
            tangent = (joints_dense[j_idx + 1] - joints_dense[j_idx - 1]) * 0.5
        t_len = float(np.linalg.norm(tangent))
        tangent_unit = (
            tangent / t_len if t_len > 1e-6
            else np.array([0.0, 1.0, 0.0], dtype=np.float32)
        )

        u, v = _perpendicular_basis(tangent_unit)
        r = float(radii_dense[j_idx])
        r_u = r
        r_v = r * FINGER_ELLIPSE_RATIO

        # VECTORIZED: offset (segments, 3) = u[None,:] * (cos*r_u)[:,None]
        #                                  + v[None,:] * (sin*r_v)[:,None]
        # Cada linha do offset e' um vertex offset relativo ao centro
        offsets = (
            np.outer(cos_table * r_u, u)
            + np.outer(sin_table * r_v, v)
        )  # shape (segments, 3)
        positions = joints_dense[j_idx][None, :] + offsets  # (segments, 3)

        # Normais: gradiente da elipse, vectorized
        n_u_arr = cos_table / max(r_u, 1e-6)
        n_v_arr = sin_table / max(r_v, 1e-6)
        normals_arr = np.outer(n_u_arr, u) + np.outer(n_v_arr, v)  # (seg, 3)
        # Normalize por linha
        norm_lens = np.linalg.norm(normals_arr, axis=1, keepdims=True)
        norm_lens = np.maximum(norm_lens, 1e-6)
        normals_arr = normals_arr / norm_lens

        all_ring_pos.append(positions)
        all_ring_norm.append(normals_arr)

    # v6.9.8.8: keep numpy arrays — evita conversao tuple por vert.
    # .tolist() e' significativamente mais rapido que Python comprehension.
    ring_verts_array = np.concatenate(all_ring_pos, axis=0)
    ring_norms_array = np.concatenate(all_ring_norm, axis=0)
    verts: List[Tuple[float, float, float]] = ring_verts_array.tolist()
    normals: List[Tuple[float, float, float]] = ring_norms_array.tolist()

    indices: List[int] = []

    # Triangles entre rings consecutivos
    for ring_idx in range(n_rings - 1):
        for s in range(segments):
            s2 = (s + 1) % segments
            i0 = base_idx + ring_idx * segments + s
            i1 = base_idx + ring_idx * segments + s2
            i2 = base_idx + (ring_idx + 1) * segments + s
            i3 = base_idx + (ring_idx + 1) * segments + s2
            indices.extend([i0, i1, i2, i1, i3, i2])

    # v6.9.8.8: hemisphere cap VECTORIZED — antes nested Python loops
    # 3×10=30 iter c/ tuple appends; agora numpy broadcasting.
    if add_tip_cap and n_rings >= 2:
        tip_tangent = joints_dense[-1] - joints_dense[-2]
        tip_len = float(np.linalg.norm(tip_tangent))
        if tip_len > 1e-6:
            tip_tangent_unit = tip_tangent / tip_len
            r_tip = float(radii_dense[-1])
            tip_origin = joints_dense[-1]
            u_tip, v_tip = _perpendicular_basis(tip_tangent_unit)

            CAP_RINGS = 3
            # theta de 1/4 a 3/4 do quarter circle (excludes endpoints)
            thetas = np.array([
                (k / (CAP_RINGS + 1)) * (np.pi / 2.0)
                for k in range(1, CAP_RINGS + 1)
            ], dtype=np.float32)
            sin_ths = np.sin(thetas)  # (CAP_RINGS,)
            cos_ths = np.cos(thetas)  # (CAP_RINGS,)
            r_rings = r_tip * cos_ths  # (CAP_RINGS,)
            z_offsets = r_tip * sin_ths  # (CAP_RINGS,)

            # Build all cap vertices in one go
            for k in range(CAP_RINGS):
                cos_th = float(cos_ths[k])
                sin_th = float(sin_ths[k])
                r_ring = float(r_rings[k])
                ring_center = tip_origin + tip_tangent_unit * float(z_offsets[k])

                r_u_cap = r_ring
                r_v_cap = r_ring * FINGER_ELLIPSE_RATIO

                # Vectorized positions
                offsets = (
                    np.outer(cos_table * r_u_cap, u_tip)
                    + np.outer(sin_table * r_v_cap, v_tip)
                )
                positions = ring_center[None, :] + offsets  # (segments, 3)

                # Vectorized normals (side blended with tip direction)
                n_u_cap = cos_table / max(r_u_cap, 1e-6)
                n_v_cap = sin_table / max(r_v_cap, 1e-6)
                side = np.outer(n_u_cap, u_tip) + np.outer(n_v_cap, v_tip)
                side_lens = np.linalg.norm(side, axis=1, keepdims=True)
                side = side / np.maximum(side_lens, 1e-6)
                # Blend side * cos_th + tip * sin_th
                normal_blend = side * cos_th + tip_tangent_unit[None, :] * sin_th
                nb_lens = np.linalg.norm(normal_blend, axis=1, keepdims=True)
                normal_blend = normal_blend / np.maximum(nb_lens, 1e-6)

                verts.extend(positions.tolist())
                normals.extend(normal_blend.tolist())

            # Apex vertex
            apex_pos = tip_origin + tip_tangent_unit * r_tip
            verts.append([float(apex_pos[0]), float(apex_pos[1]), float(apex_pos[2])])
            normals.append([float(tip_tangent_unit[0]),
                            float(tip_tangent_unit[1]),
                            float(tip_tangent_unit[2])])

            # Indices: conecta finger ultimo ring -> 1st cap ring -> ... -> apex
            finger_last_ring = base_idx + (n_rings - 1) * segments
            cap_ring0 = base_idx + n_rings * segments
            # Quad strip: finger ring -> cap ring 0
            for s in range(segments):
                s2 = (s + 1) % segments
                i0 = finger_last_ring + s
                i1 = finger_last_ring + s2
                i2 = cap_ring0 + s
                i3 = cap_ring0 + s2
                indices.extend([i0, i1, i2, i1, i3, i2])
            # Strips entre cap rings consecutivos
            for k in range(CAP_RINGS - 1):
                ra = base_idx + n_rings * segments + k * segments
                rb = base_idx + n_rings * segments + (k + 1) * segments
                for s in range(segments):
                    s2 = (s + 1) % segments
                    indices.extend([
                        ra + s, ra + s2, rb + s,
                        ra + s2, rb + s2, rb + s,
                    ])
            # Fan do ultimo cap ring pro apex
            apex_idx = base_idx + n_rings * segments + CAP_RINGS * segments
            last_cap_ring = base_idx + n_rings * segments + (CAP_RINGS - 1) * segments
            for s in range(segments):
                s2 = (s + 1) % segments
                indices.extend([apex_idx, last_cap_ring + s, last_cap_ring + s2])

    return verts, normals, indices


def _build_anatomical_palm(
    pts_3d: List[np.ndarray],
    size_px: float,
    base_idx: int,
    ring_count: int = PALM_RING_COUNT,
    ring_segments: int = PALM_RING_SEGMENTS,
) -> Tuple[List, List, List]:
    """
    Palma anatomica via LOFTED SURFACE.

    Em vez de uma UV sphere (egg-shape), constroi swept surface ao
    longo do eixo wrist → middle_MCP, com rings ELITICOS que mudam de
    tamanho e posicao por altura:

    - Bottom (t≈0): ring estreito + curto na profundidade = wrist taper
    - Body (t≈0.2-0.7): largura crescente + thenar bulge offset lateral
                       pro lado do polegar + max profundidade no meio
    - Top (t≈1): largura cheia nos MCPs + leve rollover

    O resultado tem silhueta orgânica de palma humana, não um ovo.

    Eixos:
    - main_axis = wrist → middle_MCP (longitudinal)
    - lateral_axis = pinky_MCP → index_MCP (transversal, orto a main)
    - depth_axis = perpendicular a ambos (out-of-screen)
    """
    wrist = pts_3d[0]
    thumb_cmc = pts_3d[1]
    index_mcp = pts_3d[5]
    middle_mcp = pts_3d[9]
    pinky_mcp = pts_3d[17]

    # Eixo principal
    main_axis = middle_mcp - wrist
    main_axis_len = float(np.linalg.norm(main_axis))
    if main_axis_len < 1e-6:
        return [], [], []
    main_unit = main_axis / main_axis_len

    # Eixo lateral: ortogonalizar (pinky → index) contra main
    lateral_raw = index_mcp - pinky_mcp
    lateral_raw = lateral_raw - main_unit * float(np.dot(lateral_raw, main_unit))
    lateral_len = float(np.linalg.norm(lateral_raw))
    if lateral_len < 1e-6:
        lateral_unit = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        lateral_len = size_px * 0.30
    else:
        lateral_unit = lateral_raw / lateral_len

    # Depth axis (perpendicular)
    depth_unit = np.cross(main_unit, lateral_unit)
    if float(np.linalg.norm(depth_unit)) < 1e-6:
        depth_unit = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    else:
        depth_unit = depth_unit / float(np.linalg.norm(depth_unit))

    # Determina lado do polegar (sinal projetado no eixo lateral)
    palm_mid = (wrist + middle_mcp) * 0.5
    thumb_proj = float(np.dot(thumb_cmc - palm_mid, lateral_unit))
    thumb_sign = 1.0 if thumb_proj > 0 else -1.0

    palm_length = main_axis_len * (1.0 + PALM_WRIST_EXTEND_FRAC)
    palm_width = lateral_len
    palm_depth_max = size_px * PALM_DEPTH_MAX_FRAC

    # Origem deslocada pra dar extensao abaixo do wrist
    palm_origin = wrist - main_unit * (main_axis_len * PALM_WRIST_EXTEND_FRAC)

    verts: List[Tuple[float, float, float]] = []
    normals: List[Tuple[float, float, float]] = []

    for ring_idx in range(ring_count):
        t = ring_idx / (ring_count - 1)  # 0 = base extendida, 1 = top MCPs

        # Posicao ao longo do eixo principal
        center_axis = palm_origin + main_unit * (t * palm_length)

        # v6.9.8.10: width curve mais cheia pra envelopar finger bases.
        # Top expand 10% alem MCPs = "envolve" bases dos dedos visualmente.
        if t < 0.20:
            # Zona do pulso — taper moderado (0.65 → 0.88)
            width_factor = 0.65 + (t / 0.20) * 0.23
        elif t < 0.85:
            # Body — cheio
            width_factor = 0.88 + (t - 0.20) / 0.65 * 0.17  # 0.88 → 1.05
        else:
            # Top — leve overshoot pra envelopar finger MCPs
            width_factor = 1.05 - (t - 0.85) * 0.05

        r_lateral = palm_width * 0.5 * width_factor

        # Depth — convexo no meio, fino nos extremos
        depth_t = math.sin(t * math.pi)  # 0 → 1 → 0
        r_depth = palm_depth_max * (0.35 + 0.65 * depth_t)

        # Thenar bulge — desloca ring center lateralmente pro lado do polegar
        thenar_offset = 0.0
        if 0.10 < t < 0.55:
            # Bell curve com pico em t ≈ 0.32
            bulge_t = math.sin((t - 0.10) / 0.45 * math.pi)
            thenar_offset = thumb_sign * bulge_t * palm_width * PALM_THENAR_BULGE_FRAC

        ring_center = center_axis + lateral_unit * thenar_offset

        # VECTORIZED ring generation
        if ring_segments == PALM_RING_SEGMENTS:
            cos_t = _PALM_COS
            sin_t = _PALM_SIN
        else:
            cos_t = np.cos(np.linspace(
                0, 2 * np.pi, ring_segments, endpoint=False, dtype=np.float32,
            ))
            sin_t = np.sin(np.linspace(
                0, 2 * np.pi, ring_segments, endpoint=False, dtype=np.float32,
            ))

        offsets = (
            np.outer(cos_t * r_lateral, lateral_unit)
            + np.outer(sin_t * r_depth, depth_unit)
        )
        ring_positions = ring_center[None, :] + offsets  # (seg, 3)

        # Normals = gradient eliptico
        nx_arr = cos_t / max(r_lateral, 1e-6)
        nz_arr = sin_t / max(r_depth, 1e-6)
        ring_normals = (
            np.outer(nx_arr, lateral_unit) + np.outer(nz_arr, depth_unit)
        )
        nlens = np.linalg.norm(ring_normals, axis=1, keepdims=True)
        nlens = np.maximum(nlens, 1e-6)
        ring_normals = ring_normals / nlens

        # .tolist() converte ring (segments, 3) numpy → lista de listas
        # muito mais rapido que loop com float() casts.
        verts.extend(ring_positions.tolist())
        normals.extend(ring_normals.tolist())

    # Triangles entre rings consecutivos
    indices: List[int] = []
    for ri in range(ring_count - 1):
        for s in range(ring_segments):
            s2 = (s + 1) % ring_segments
            i0 = base_idx + ri * ring_segments + s
            i1 = base_idx + ri * ring_segments + s2
            i2 = base_idx + (ri + 1) * ring_segments + s
            i3 = base_idx + (ri + 1) * ring_segments + s2
            indices.extend([i0, i1, i2, i1, i3, i2])

    # Bottom cap (wrist end)
    bot_center_idx = base_idx + ring_count * ring_segments
    bot_center_pos = palm_origin
    verts.append((float(bot_center_pos[0]), float(bot_center_pos[1]), float(bot_center_pos[2])))
    normals.append((float(-main_unit[0]), float(-main_unit[1]), float(-main_unit[2])))
    bot_ring_start = base_idx
    for s in range(ring_segments):
        s2 = (s + 1) % ring_segments
        indices.extend([bot_center_idx, bot_ring_start + s2, bot_ring_start + s])

    # Top cap (MCP end)
    top_center_idx = bot_center_idx + 1
    top_center_pos = palm_origin + main_unit * palm_length
    verts.append((float(top_center_pos[0]), float(top_center_pos[1]), float(top_center_pos[2])))
    normals.append((float(main_unit[0]), float(main_unit[1]), float(main_unit[2])))
    top_ring_start = base_idx + (ring_count - 1) * ring_segments
    for s in range(ring_segments):
        s2 = (s + 1) % ring_segments
        indices.extend([top_center_idx, top_ring_start + s, top_ring_start + s2])

    return verts, normals, indices


def _build_finger_palm_bridges(
    all_verts: List,
    finger_mcp_starts: List[int],
    palm_top_ring_start: int,
    finger_segments: int,
    palm_segments: int,
) -> List[int]:
    """
    v6.9.9.0 — MANO-inspired CONTINUITY GEOMETRY.

    Cria triangulos conectando cada MCP ring dos dedos com a arc
    correspondente da palm top ring. Resultado: mesh CONTINUA palm↔
    fingers (sem seam visivel).

    Algoritmo:
    1. Para cada vert do MCP ring do dedo, encontra o palm top vert
       MAIS PROXIMO (3D euclidean distance).
    2. Triangle strip:
       - mcp[s], palm_near[s], mcp[(s+1)%N]
       - palm_near[s], palm_near[(s+1)%N], mcp[(s+1)%N]

    Sem alocar verts novos — apenas indices conectando existentes.
    Custo: ~10 triangles por dedo × 4 dedos = 40 tris extras.

    Args:
        all_verts: lista mutavel sendo construida (precisa pra np.array)
        finger_mcp_starts: indices base de cada finger (skipping thumb)
        palm_top_ring_start: indice base do palm top ring
        finger_segments: 10
        palm_segments: 16

    Returns:
        Lista de indices triangles pra extend em all_indices.
    """
    if not finger_mcp_starts:
        return []

    # Converte verts pra numpy pra busca eficiente
    verts_arr = np.array(all_verts, dtype=np.float32)
    palm_top_verts = verts_arr[
        palm_top_ring_start:palm_top_ring_start + palm_segments
    ]

    indices: List[int] = []

    for finger_start in finger_mcp_starts:
        mcp_verts = verts_arr[finger_start:finger_start + finger_segments]

        # Para cada MCP vert, indice do palm top mais proximo
        # Distancias 2D (X, Y) suficientes pra anatomia — z aproximado
        dists = np.linalg.norm(
            palm_top_verts[None, :, :2] - mcp_verts[:, None, :2],
            axis=2,
        )  # shape (finger_segs, palm_segs)
        nearest_palm = np.argmin(dists, axis=1)  # shape (finger_segs,)

        # Triangle strip MCP → palm_top
        for s in range(finger_segments):
            s_next = (s + 1) % finger_segments
            mcp_a = finger_start + s
            mcp_b = finger_start + s_next
            palm_a = palm_top_ring_start + int(nearest_palm[s])
            palm_b = palm_top_ring_start + int(nearest_palm[s_next])

            # Skip degenerate (se ambos MCP verts mapeiam pro mesmo palm)
            if palm_a == palm_b:
                # Single tri: mcp_a, mcp_b, palm_a
                indices.extend([mcp_a, palm_a, mcp_b])
            else:
                # Quad → 2 triangles
                indices.extend([mcp_a, palm_a, mcp_b])
                indices.extend([mcp_b, palm_a, palm_b])

    return indices


def generate_hand_mesh(
    landmarks: Sequence[Tuple[float, float, float]],
    center_x: float,
    center_y: float,
    size_px: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Constroi mesh 3D da mao.

    Args:
        landmarks: 21 tuplas (x, y, z) normalizadas (MediaPipe)
        center_x, center_y: posicao na tela do anchor (pinch midpoint)
        size_px: tamanho alvo do bbox em pixels

    Returns:
        vertices (N,3) float32, normals (N,3) float32, indices (M,) uint32
    """
    if len(landmarks) < 21:
        return _empty_mesh()

    # PERF: vetoriza tudo num unico ndarray (21,3) — antes eram 21 calls de
    # np.array([x,y,z]) por mesh-regen, mais alocs Python que somavam ~ms
    # durante motion. Agora 1 alloc + 1 broadcast + scaling vetorizado.
    lm_arr = np.asarray(landmarks, dtype=np.float32)  # (N, 3) -- normalizado
    xs = lm_arr[:, 0]
    ys = lm_arr[:, 1]
    bbox_w = max(float(xs.max() - xs.min()), 1e-6)
    bbox_h = max(float(ys.max() - ys.min()), 1e-6)
    scale = size_px / max(bbox_w, bbox_h)

    pa, pb = _PINCH_ANCHOR
    anchor_x = float((lm_arr[pa, 0] + lm_arr[pb, 0]) * 0.5)
    anchor_y = float((lm_arr[pa, 1] + lm_arr[pb, 1]) * 0.5)

    # Converte pra screen pixels (3D — z escalado pra dar profundidade)
    z_scale = scale * 0.4  # fator de profundidade
    pts_arr = np.empty((len(landmarks), 3), dtype=np.float32)
    pts_arr[:, 0] = center_x + (xs - anchor_x) * scale
    pts_arr[:, 1] = center_y + (ys - anchor_y) * scale
    pts_arr[:, 2] = lm_arr[:, 2] * z_scale
    # Mantem API existente: pts_3d e' lista indexavel de arrays (1,3).
    # Slicing de ndarray e' barato (views, sem copia).
    pts_3d: List[np.ndarray] = [pts_arr[i] for i in range(pts_arr.shape[0])]

    all_verts: List[Tuple[float, float, float]] = []
    all_norms: List[Tuple[float, float, float]] = []
    all_indices: List[int] = []
    base_idx = 0

    # ----------- PALM CENTER (precisamos antes pra fusao dos dedos) -----------
    _PALM_IDX = (0, 1, 5, 9, 13, 17)
    palm_xs = [pts_3d[i][0] for i in _PALM_IDX]
    palm_ys = [pts_3d[i][1] for i in _PALM_IDX]
    palm_zs = [pts_3d[i][2] for i in _PALM_IDX]
    palm_center = np.array([
        (max(palm_xs) + min(palm_xs)) * 0.5,
        (max(palm_ys) + min(palm_ys)) * 0.5,
        (max(palm_zs) + min(palm_zs)) * 0.5,
    ], dtype=np.float32)

    # v6.9.9.0 — MANO-inspired: track ring boundaries pra bridge geometry.
    # Salvamos start index dos MCP rings de cada dedo + start index do top
    # ring da palma. Depois conectamos com triangles pra criar mesh
    # CONTINUA (sem seam visivel entre dedos e palma).
    finger_mcp_starts: List[int] = []

    # ----------- DEDOS: swept cylinder unico por dedo, com tip cap -----------
    for chain_idx, (chain, w_factor) in enumerate(
        zip(FINGER_CHAINS, FINGER_WIDTH_FACTOR),
    ):
        is_thumb = (chain_idx == 0)
        taper = THUMB_TAPER if is_thumb else SEGMENT_TAPER
        inset_frac = THUMB_MCP_INSET_FRAC if is_thumb else MCP_INSET_FRAC
        base_radius = size_px * w_factor * 0.85
        joints = [pts_3d[i] for i in chain]
        radii = [base_radius * t for t in taper]
        verts, norms, idx = _build_swept_finger(
            joints, radii, FINGER_RING_SEGMENTS, base_idx,
            palm_center=palm_center, add_tip_cap=True,
            mcp_inset_frac=inset_frac,
        )
        # MCP ring (primeiro ring deste dedo) = primeiros FINGER_RING_SEGMENTS verts
        finger_mcp_starts.append(base_idx)
        all_verts.extend(verts)
        all_norms.extend(norms)
        all_indices.extend(idx)
        base_idx += len(verts)

    # ----------- PALMA: lofted surface ANATOMICA -----------
    palm_base_idx = base_idx
    verts, norms, idx = _build_anatomical_palm(
        pts_3d, size_px, base_idx,
    )
    all_verts.extend(verts)
    all_norms.extend(norms)
    all_indices.extend(idx)
    base_idx += len(verts)

    # Palm top ring (ultimo ring antes dos caps) começa em:
    palm_top_ring_start = palm_base_idx + (PALM_RING_COUNT - 1) * PALM_RING_SEGMENTS

    # v6.9.9.0 — BRIDGES palm↔fingers: cria triangulos conectando cada
    # MCP ring (10 verts) com a arc correspondente da palm top ring
    # (16 verts). Por dedo: triangle strip de ~10 triangles.
    # SKIP THUMB porque seu CMC esta MUITO offset lateral pro polegar
    # (anatomia distinta — thenar absorption ja resolve).
    bridge_indices = _build_finger_palm_bridges(
        all_verts,
        finger_mcp_starts=finger_mcp_starts[1:],  # skip thumb
        palm_top_ring_start=palm_top_ring_start,
        finger_segments=FINGER_RING_SEGMENTS,
        palm_segments=PALM_RING_SEGMENTS,
    )
    all_indices.extend(bridge_indices)

    # Conversao final
    vertices = np.array(all_verts, dtype=np.float32)
    normals = np.array(all_norms, dtype=np.float32)
    indices = np.array(all_indices, dtype=np.uint32)
    return vertices, normals, indices
