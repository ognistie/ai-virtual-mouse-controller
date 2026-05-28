"""
core/hand_renderer.py
=====================

Renderiza a mao "holografica" num canvas Tk.

Pensei essa parte como funcao pura: recebe 21 landmarks normalizados, a
posicao alvo na tela e o tamanho desejado, e devolve as primitivas a
desenhar. Isso torna ela testavel sem precisar abrir GUI.

A mao e' renderizada com:
- O ponto central (landmark 9 - base do dedo medio) ancorado em (cx, cy)
- Demais landmarks posicionados relativamente, com o bounding box da mao
  detectada escalado pra caber em `size_px`
- 5 "bones" ligando os dedos ate o pulso
- Pontos circulares em cima de cada landmark

Como nao temos OpenGL aqui, o "3D look" e' simulado:
- Profundidade (z) modula o raio de cada ponto (mais perto da camera = maior)
- Profundidade tambem modula a espessura do bone
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple


# Conexoes do esqueleto MediaPipe (apenas os 5 dedos principais + palma).
# Cada tupla e' uma cadeia de landmarks ligados.
HAND_BONES: Tuple[Tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4),       # polegar
    (0, 5, 6, 7, 8),       # indicador
    (0, 9, 10, 11, 12),    # medio
    (0, 13, 14, 15, 16),   # anelar
    (0, 17, 18, 19, 20),   # mindinho
    (5, 9, 13, 17),        # palma (atravessa MCPs)
)


# Landmark de ancora (base do dedo medio). Mesmo que CURSOR_ANCHOR_LANDMARK
# no config, mas redefinido aqui pra nao acoplar.
_ANCHOR_LANDMARK = 9


@dataclass(frozen=True)
class Bone:
    """Segmento de linha entre dois landmarks."""
    x1: float
    y1: float
    x2: float
    y2: float
    width: float


@dataclass(frozen=True)
class Point:
    """Circulo num landmark."""
    x: float
    y: float
    radius: float


@dataclass(frozen=True)
class HandPrimitives:
    """Conjunto de primitivas pro canvas desenhar."""
    bones: Tuple[Bone, ...]
    points: Tuple[Point, ...]


def compute_hand_primitives(
    landmarks: Sequence[Tuple[float, float, float]],
    center_x: float,
    center_y: float,
    size_px: float,
) -> HandPrimitives:
    """
    Calcula as primitivas pra renderizar a mao.

    Args:
        landmarks: 21 tuplas (x, y, z) normalizadas (do MediaPipe).
                   x, y em [0, 1] sobre o frame; z relativo ao pulso.
        center_x, center_y: posicao na tela onde o landmark 9 deve ficar.
        size_px: tamanho do bounding box da mao na tela.

    Returns:
        HandPrimitives com bones e points em coords absolutas de tela.
    """
    if len(landmarks) < 21:
        return HandPrimitives(bones=(), points=())

    # Computa bounding box da mao em coords normalizadas
    xs = [lm[0] for lm in landmarks]
    ys = [lm[1] for lm in landmarks]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    bbox_w = max(max_x - min_x, 1e-6)
    bbox_h = max(max_y - min_y, 1e-6)

    # Escala uniforme pro lado maior caber em size_px
    scale = size_px / max(bbox_w, bbox_h)

    # Offset pro landmark 9 ficar em (center_x, center_y)
    anchor = landmarks[_ANCHOR_LANDMARK]
    anchor_x, anchor_y = anchor[0], anchor[1]

    # Calcula coord de tela pra cada landmark
    screen_pts: List[Tuple[float, float, float]] = []
    for lm in landmarks:
        x = center_x + (lm[0] - anchor_x) * scale
        y = center_y + (lm[1] - anchor_y) * scale
        z = lm[2]
        screen_pts.append((x, y, z))

    # Pontos: raio modulado por z (mais perto da camera = maior).
    # MediaPipe z e' tipicamente em [-0.3, 0.1]; valores menores = mais perto.
    base_radius = max(2.0, size_px * 0.03)
    points: List[Point] = []
    for x, y, z in screen_pts:
        # mapeia z para fator multiplicativo em [0.7, 1.3]
        depth_factor = 1.0 - max(-0.3, min(0.3, z)) * 1.0
        depth_factor = max(0.7, min(1.3, depth_factor))
        points.append(Point(x=x, y=y, radius=base_radius * depth_factor))

    # Bones: espessura modulada pelo z medio dos dois landmarks
    base_width = max(1.5, size_px * 0.018)
    bones: List[Bone] = []
    for chain in HAND_BONES:
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            x1, y1, z1 = screen_pts[a]
            x2, y2, z2 = screen_pts[b]
            avg_z = (z1 + z2) / 2.0
            depth_factor = 1.0 - max(-0.3, min(0.3, avg_z)) * 0.8
            depth_factor = max(0.7, min(1.3, depth_factor))
            bones.append(
                Bone(
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    width=base_width * depth_factor,
                )
            )

    return HandPrimitives(bones=tuple(bones), points=tuple(points))
