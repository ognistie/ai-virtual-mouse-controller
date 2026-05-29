"""
core/hand_renderer.py
=====================

Renderiza a mao "holografica" como SILHUETA - palma + 5 dedos como
poligonos tubulares preenchidos com glow nas bordas. Estilo low-poly
particle hand das referencias.

Funcao pura: recebe 21 landmarks normalizados + alvo na tela + tamanho,
devolve primitivas a desenhar.

Composicao final na tela (em ordem de profundidade):
  1. Palm polygon                     - silhueta da palma (filled + outline)
  2. Finger polygons (5)              - dedos como tubos (filled + outline)
  3. Joints (21 small dots)           - marcadores anatomicos sutis
  4. Tip halos                        - aura nos fingertips
  5. Tip cores                        - nucleos brilhantes nos fingertips

Pontos de clique calculados (em screen coords) pra cada tipo de gesto:
  - pinch_left:   (4 + 8) / 2    polegar + indicador
  - pinch_right:  (4 + 12) / 2   polegar + medio
  - pinch_double: (8 + 12) / 2   indicador + medio (peace sign)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


# Cadeias de landmarks por dedo
THUMB_CHAIN = (1, 2, 3, 4)
INDEX_CHAIN = (5, 6, 7, 8)
MIDDLE_CHAIN = (9, 10, 11, 12)
RING_CHAIN = (13, 14, 15, 16)
PINKY_CHAIN = (17, 18, 19, 20)
FINGER_CHAINS = (THUMB_CHAIN, INDEX_CHAIN, MIDDLE_CHAIN, RING_CHAIN, PINKY_CHAIN)

# Pares de landmarks usados pra posicionar particulas internas (efeito mesh).
# 6 pares = visual mais limpo, sem poluir o interior da mao
# Largura proporcional por dedo (fracao de size_px), na base.
# Proporcoes anatomicas humanas:
# - polegar e' o mais grosso (musculo thenar visivel)
# - medio levemente mais largo que indicador/anelar
# - mindinho claramente mais fino
FINGER_WIDTH_FACTOR = (0.095, 0.075, 0.082, 0.072, 0.060)

# Taper na ponta — fracao da largura base (fallback)
FINGER_TIP_TAPER = 0.55

# Per-joint width factors — anatomia humana:
# MCP grosso (base do dedo), PIP knuckle bulge (junta media),
# DIP afinando (junta perto da ponta), TIP arredondado (nao pontudo).
# Index 0=MCP, 1=PIP, 2=DIP, 3=TIP.
FINGER_KNUCKLE_FACTORS = (0.96, 1.04, 0.88, 0.58)

# Polegar com anatomia distinta — base musculosa, ponta arredondada.
# Index 0=MCP, 1=IP, 2=DIP, 3=TIP.
THUMB_KNUCKLE_FACTORS = (1.12, 1.02, 0.88, 0.68)

# Sequencia counterclockwise da palma (mao direita)
# 0=wrist, 17=pinky MCP, 13=ring MCP, 9=middle MCP, 5=index MCP, 1=thumb CMC
PALM_VERTICES = (0, 17, 13, 9, 5, 1)

# Pontas dos 5 dedos
FINGERTIPS = (4, 8, 12, 16, 20)

# Pinch points - 3 tipos
PINCH_LEFT = (4, 8)     # polegar + indicador (CLICK)
PINCH_RIGHT = (4, 12)   # polegar + medio (RIGHT_CLICK)
PINCH_DOUBLE = (8, 12)  # indicador + medio (DOUBLE_CLICK = peace)

# Distribuicao da nuvem densa por zona da mao
_CLOUD_PALM_HOSTS = (0, 5, 9, 13, 17)  # wrist + 4 MCPs
_CLOUD_FINGER_HOSTS = tuple(range(1, 21))  # tudo exceto wrist
_CLOUD_PALM_FRAC = 0.50      # 50% palm cluster
_CLOUD_FINGER_FRAC = 0.40    # 40% finger trails
# resto (10%) = edge bloom

# Ancora visual da renderizacao: midpoint do PINCH LEFT (polegar + indicador).
# Isso posiciona o holograma de forma que o ponto onde os dedos vao se
# encontrar (CLICK esquerdo, principal) coincida EXATAMENTE com o cursor.
# A mao "abre" e "fecha" em torno do cursor — visualmente intuitivo.
_PINCH_ANCHOR = PINCH_LEFT


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    radius: float


@dataclass(frozen=True)
class Tip:
    """Fingertip com halo + core (estilo holografico brilhante)."""
    x: float
    y: float
    core_radius: float
    halo_radius: float


@dataclass(frozen=True)
class Polygon:
    """Poligono fechado para a palma ou um dedo."""
    points: Tuple[Tuple[float, float], ...]  # vertices (x, y)
    outline_width: float


@dataclass(frozen=True)
class CloudParticle:
    """
    Particula da nuvem densa parametrizada por landmark hospedeiro +
    offset normalizado. Gera vez sao constantes — at runtime o overlay
    aplica transform pose-aware (particula segue o landmark hospedeiro).

    host_idx:    landmark de ancora (0-20)
    offset_x/y:  offset em frac do bbox (multiplica size_px)
    phase:       fase do twinkle [0, 2pi]
    base_alpha:  alpha baseline [0, 1] antes do twinkle
    """
    host_idx: int
    offset_x: float
    offset_y: float
    phase: float
    base_alpha: float


@dataclass(frozen=True)
class HandPrimitives:
    """
    Conjunto completo de primitivas pro overlay desenhar.

    palm:           silhueta da palma (polygon preenchido + outline)
    fingers:        5 polygons tubulares (thumb, index, middle, ring, pinky)
    joints:         circulos pequenos em todos os 21 landmarks
    tips:           5 fingertips com halo + core brilhante
    cursor_marker:  posicao do landmark 9 (ancora do cursor)
    pinch_*:        pontos de clique pra cada tipo de gesto (screen coords)
    """
    palm: Polygon
    fingers: Tuple[Polygon, ...]
    joints: Tuple[Point, ...]
    tips: Tuple[Tip, ...]
    cursor_marker: Tuple[float, float]
    pinch_left: Tuple[float, float]
    pinch_right: Tuple[float, float]
    pinch_double: Tuple[float, float]


def _empty() -> HandPrimitives:
    return HandPrimitives(
        palm=Polygon(points=(), outline_width=0.0),
        fingers=(),
        joints=(),
        tips=(),
        cursor_marker=(0.0, 0.0),
        pinch_left=(0.0, 0.0),
        pinch_right=(0.0, 0.0),
        pinch_double=(0.0, 0.0),
    )


# Cache da nuvem por (count, seed) — gera 1x por config no processo
_cloud_cache: Dict[Tuple[int, int], Tuple[CloudParticle, ...]] = {}


def generate_particle_cloud(
    count: int = 180,
    seed: int = 42,
) -> Tuple[CloudParticle, ...]:
    """
    Gera distribuicao deterministica de particles ancoradas a landmarks.

    Estrategia:
    - 50% palm cluster: ao redor de wrist + 4 MCPs, spread medio
    - 40% finger trails: ao redor dos joints dos 5 dedos, spread baixo
    - 10% edge bloom: offset maior, alpha menor (halo difuso)

    Reproduzivel via seed. Particles sao constantes — overlay aplica
    transform pose-aware a cada frame.

    Args:
        count: numero total de particles. Default 180.
        seed: RNG seed pra reprodutibilidade.

    Returns:
        Tupla imutavel de CloudParticle.
    """
    if count <= 0:
        return ()
    rng = random.Random(seed)
    particles: List[CloudParticle] = []

    palm_n = int(count * _CLOUD_PALM_FRAC)
    finger_n = int(count * _CLOUD_FINGER_FRAC)
    edge_n = count - palm_n - finger_n

    # Palm cluster — spread medio em torno de wrist + MCPs
    for _ in range(palm_n):
        host = _CLOUD_PALM_HOSTS[rng.randint(0, len(_CLOUD_PALM_HOSTS) - 1)]
        ox = rng.gauss(0.0, 0.06)
        oy = rng.gauss(0.0, 0.07)
        phase = rng.uniform(0.0, 6.283185)
        alpha = rng.uniform(0.45, 0.95)
        particles.append(CloudParticle(host, ox, oy, phase, alpha))

    # Finger trails — spread baixo, alpha alto (brilhante)
    for _ in range(finger_n):
        host = _CLOUD_FINGER_HOSTS[
            rng.randint(0, len(_CLOUD_FINGER_HOSTS) - 1)
        ]
        ox = rng.gauss(0.0, 0.035)
        oy = rng.gauss(0.0, 0.035)
        phase = rng.uniform(0.0, 6.283185)
        alpha = rng.uniform(0.55, 1.0)
        particles.append(CloudParticle(host, ox, oy, phase, alpha))

    # Edge bloom — spread maior, alpha baixo (halo difuso)
    for _ in range(edge_n):
        host = rng.randint(0, 20)
        ox = rng.gauss(0.0, 0.11)
        oy = rng.gauss(0.0, 0.11)
        phase = rng.uniform(0.0, 6.283185)
        alpha = rng.uniform(0.20, 0.50)
        particles.append(CloudParticle(host, ox, oy, phase, alpha))

    return tuple(particles)


def get_particle_cloud(
    count: int = 180,
    seed: int = 42,
) -> Tuple[CloudParticle, ...]:
    """Versao cached. Gera 1x por (count, seed) e reusa."""
    key = (count, seed)
    if key not in _cloud_cache:
        _cloud_cache[key] = generate_particle_cloud(count, seed)
    return _cloud_cache[key]


def _tube_polygon(
    joints: Sequence[Tuple[float, float]],
    base_width: float,
    tip_taper: float = FINGER_TIP_TAPER,
    width_factors: Optional[Sequence[float]] = None,
) -> Tuple[Tuple[float, float], ...]:
    """
    Constroi poligono tubular ao longo de cadeia de joints.

    width_factors: fator por joint (anatomia natural). Se None usa
    taper linear (fallback). Tamanho deve casar com len(joints).
    Ex: (0.92, 1.06, 0.80, 0.42) gera knuckle bulge no joint 1.
    """
    n = len(joints)
    if n < 2:
        return ()

    left: List[Tuple[float, float]] = []
    right: List[Tuple[float, float]] = []

    # Valida width_factors
    use_factors = (
        width_factors is not None and len(width_factors) == n
    )

    for i in range(n):
        x, y = joints[i]

        # Direcao no ponto: media de forward + backward (suaviza curvas)
        if i == 0:
            dx = joints[1][0] - x
            dy = joints[1][1] - y
        elif i == n - 1:
            dx = x - joints[i - 1][0]
            dy = y - joints[i - 1][1]
        else:
            fdx = joints[i + 1][0] - x
            fdy = joints[i + 1][1] - y
            bdx = x - joints[i - 1][0]
            bdy = y - joints[i - 1][1]
            dx = (fdx + bdx) / 2.0
            dy = (fdy + bdy) / 2.0

        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-6:
            length = 1.0

        # Perpendicular normalizada
        px = -dy / length
        py = dx / length

        if use_factors:
            # Anatomia: knuckle bulge no PIP
            width = base_width * width_factors[i] * 0.5
        else:
            # Fallback: taper linear do base ao tip
            t = i / (n - 1) if n > 1 else 0.0
            width = base_width * (1.0 - t * (1.0 - tip_taper)) * 0.5

        left.append((x + px * width, y + py * width))
        right.append((x - px * width, y - py * width))

    # Poligono: left forward + right backward (fecha em volta)
    return tuple(left) + tuple(right[::-1])


def compute_hand_primitives(
    landmarks: Sequence[Tuple[float, float, float]],
    center_x: float,
    center_y: float,
    size_px: float,
) -> HandPrimitives:
    """
    Calcula as primitivas pra renderizar a mao como silhueta.

    Args:
        landmarks: 21 tuplas (x, y, z) normalizadas (do MediaPipe).
        center_x, center_y: posicao na tela do landmark 9 (cursor).
        size_px: tamanho do bounding box em pixels.

    Returns:
        HandPrimitives com tudo em coords absolutas de tela.
    """
    if len(landmarks) < 21:
        return _empty()

    # Bounding box em coords normalizadas
    xs = [lm[0] for lm in landmarks]
    ys = [lm[1] for lm in landmarks]
    bbox_w = max(max(xs) - min(xs), 1e-6)
    bbox_h = max(max(ys) - min(ys), 1e-6)
    scale = size_px / max(bbox_w, bbox_h)

    # Anchor = midpoint do pinch (polegar + indicador). Isso posiciona
    # o pinch (= local do clique) exatamente em (center_x, center_y)
    # que e' a posicao do cursor do mouse.
    pa, pb = _PINCH_ANCHOR
    anchor_x = (landmarks[pa][0] + landmarks[pb][0]) / 2.0
    anchor_y = (landmarks[pa][1] + landmarks[pb][1]) / 2.0

    # Coords de tela
    screen_pts: List[Tuple[float, float, float]] = []
    for lm in landmarks:
        x = center_x + (lm[0] - anchor_x) * scale
        y = center_y + (lm[1] - anchor_y) * scale
        z = lm[2]
        screen_pts.append((x, y, z))

    # Coords 2D pra geometria de poligonos
    pts2d = [(p[0], p[1]) for p in screen_pts]

    # ---------- palm polygon ----------
    palm_points = tuple(pts2d[i] for i in PALM_VERTICES)
    palm_outline_w = max(2.0, size_px * 0.012)
    palm = Polygon(points=palm_points, outline_width=palm_outline_w)

    # ---------- finger polygons (5 tubos) ----------
    fingers: List[Polygon] = []
    for idx, (chain, w_factor) in enumerate(zip(FINGER_CHAINS, FINGER_WIDTH_FACTOR)):
        joints = [pts2d[i] for i in chain]
        base_width = size_px * w_factor
        # Polegar anatomia distinta (idx=0 em FINGER_CHAINS = THUMB_CHAIN)
        factors = THUMB_KNUCKLE_FACTORS if idx == 0 else FINGER_KNUCKLE_FACTORS
        tube = _tube_polygon(joints, base_width, width_factors=factors)
        fingers.append(Polygon(
            points=tube,
            outline_width=max(1.5, size_px * 0.009),
        ))

    # ---------- joints (todos os 21 pontos, discretos) ----------
    base_radius = max(2.0, size_px * 0.018)
    joints_list: List[Point] = []
    for x, y, z in screen_pts:
        depth = 1.0 - max(-0.3, min(0.3, z)) * 0.6
        depth = max(0.75, min(1.25, depth))
        joints_list.append(Point(x=x, y=y, radius=base_radius * depth))

    # ---------- tips (5 fingertips brilhantes) ----------
    # Tips com mais presenca visual — efeito HUD/holograma profissional
    # core 2.0x, halo 4.5x = fingertips brilhantes mas nao ofuscam
    tip_core_r = base_radius * 2.0
    tip_halo_r = base_radius * 4.5
    tips_list: List[Tip] = []
    for idx in FINGERTIPS:
        x, y, z = screen_pts[idx]
        depth = 1.0 - max(-0.3, min(0.3, z)) * 0.6
        depth = max(0.75, min(1.25, depth))
        tips_list.append(Tip(
            x=x, y=y,
            core_radius=tip_core_r * depth,
            halo_radius=tip_halo_r * depth,
        ))

    # ---------- cursor marker (no pinch anchor) ----------
    # O cursor fica exatamente no ponto entre os dedos onde o CLICK
    # esquerdo vai acontecer.
    cursor_marker = (center_x, center_y)

    # ---------- pinch points (3 tipos de gesto) ----------
    def midpoint(a, b):
        return (
            (screen_pts[a][0] + screen_pts[b][0]) / 2.0,
            (screen_pts[a][1] + screen_pts[b][1]) / 2.0,
        )

    pinch_left = midpoint(*PINCH_LEFT)
    pinch_right = midpoint(*PINCH_RIGHT)
    pinch_double = midpoint(*PINCH_DOUBLE)

    return HandPrimitives(
        palm=palm,
        fingers=tuple(fingers),
        joints=tuple(joints_list),
        tips=tuple(tips_list),
        cursor_marker=cursor_marker,
        pinch_left=pinch_left,
        pinch_right=pinch_right,
        pinch_double=pinch_double,
    )
