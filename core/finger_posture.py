"""
core.finger_posture
====================

Features anatomicas por dedo — extensao (extendido/curvado) e angulo do
PIP. Usadas para desambiguar gestos onde DISTANCIA par-a-par sozinha
falha.

Caso pratico:
- Durante um PINCH normal (polegar+indicador), o flexor digitorum profundus
  arrasta o medio junto por acoplamento de tendoes (anatomia humana).
- O medio cai ~30-50% do quanto o indicador cai.
- dist(polegar, medio) pode ficar abaixo do threshold de PINCH_MIDDLE.
- O classificador antigo, que so olha distancias, dispara RIGHT_CLICK
  enquanto o usuario fez CLICK.

Solucao: classificar PINCH_MIDDLE so quando a POSTURA confirma intencao —
indicador EXTENDIDO (claramente apontando) e medio CURVADO (foi
deliberadamente ate o polegar).

Design:
- Modulo puro: so math, sem deps.
- Features sao rotation-invariant (funcionam com a mao em qualquer
  orientacao) — usam distancias relativas ao tamanho do dedo, nao
  comparacoes de eixo Y absoluto.
"""

from __future__ import annotations

from typing import Sequence, Tuple


# ---------------------------------------------------------------------
# Cadeias de landmarks por dedo (MediaPipe Hands)
# ---------------------------------------------------------------------
# Cada dedo: (MCP, PIP, DIP, TIP). Polegar usa (CMC, MCP, IP, TIP).
FINGER_CHAIN_THUMB:  Tuple[int, int, int, int] = (1, 2, 3, 4)
FINGER_CHAIN_INDEX:  Tuple[int, int, int, int] = (5, 6, 7, 8)
FINGER_CHAIN_MIDDLE: Tuple[int, int, int, int] = (9, 10, 11, 12)
FINGER_CHAIN_RING:   Tuple[int, int, int, int] = (13, 14, 15, 16)
FINGER_CHAIN_PINKY:  Tuple[int, int, int, int] = (17, 18, 19, 20)


# ---------------------------------------------------------------------
# Helpers numericos
# ---------------------------------------------------------------------

def _dist(p1, p2) -> float:
    """Distancia 2D entre dois landmarks (usa x,y; ignora z noisy)."""
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]
    return (dx * dx + dy * dy) ** 0.5


# ---------------------------------------------------------------------
# Score de extensao por dedo
# ---------------------------------------------------------------------

def finger_extension(
    landmarks: Sequence[Tuple[float, float, float]],
    chain: Tuple[int, int, int, int],
) -> float:
    """
    Quantifica quanto um dedo esta ESTENDIDO vs CURVADO.

    Metodo: razao entre distancia direta (MCP -> TIP) e soma das distancias
    de segmento (MCP->PIP + PIP->DIP + DIP->TIP). Para um dedo perfeitamente
    reto, a razao tende a 1.0. Curvado, a distancia direta cai mas a soma
    dos segmentos (que e' o comprimento osseo, constante) se mantem.

    Rotation invariant: nao depende da orientacao da mao.
    Scale invariant: e' uma razao, indiferente ao tamanho da mao no frame.

    Args:
        landmarks: 21 tuplas (x, y, z) normalizadas (MediaPipe).
        chain: tupla (MCP, PIP, DIP, TIP) do dedo de interesse.

    Returns:
        Score em [0, 1]:
          ~1.00 = dedo totalmente estendido
          ~0.85 = quase reto, leve flexao natural
          ~0.65 = flexao moderada (curvando)
          ~0.40 = totalmente fechado contra a palma
    """
    mcp, pip, dip, tip = chain
    p_mcp = landmarks[mcp]
    p_pip = landmarks[pip]
    p_dip = landmarks[dip]
    p_tip = landmarks[tip]

    # Comprimento osseo: aprox. constante para o usuario.
    segment_sum = _dist(p_mcp, p_pip) + _dist(p_pip, p_dip) + _dist(p_dip, p_tip)
    if segment_sum <= 1e-9:
        return 0.0

    # Distancia direta MCP -> TIP. Maxima = segment_sum (dedo reto).
    direct = _dist(p_mcp, p_tip)
    # Clamp pra remover ruido (jamais > 1.0).
    ratio = direct / segment_sum
    return 0.0 if ratio < 0.0 else (1.0 if ratio > 1.0 else ratio)


def thumb_extension(
    landmarks: Sequence[Tuple[float, float, float]],
) -> float:
    """
    Score de extensao do polegar.

    Polegar usa a mesma metrica (razao distancia direta / soma de segmentos)
    com a cadeia (CMC=1, MCP=2, IP=3, TIP=4). Funciona igualmente bem.
    """
    return finger_extension(landmarks, FINGER_CHAIN_THUMB)


# ---------------------------------------------------------------------
# Conveniencias semanticas — para uso no classificador
# ---------------------------------------------------------------------

def is_clearly_extended(score: float, threshold: float = 0.92) -> bool:
    """True se o dedo esta claramente estendido (apontando)."""
    return score >= threshold


def is_clearly_curled(score: float, threshold: float = 0.70) -> bool:
    """True se o dedo esta claramente curvado (recolhido)."""
    return score <= threshold


def is_partial_curl(score: float) -> bool:
    """True se o dedo esta em flexao intermediaria (ex: pinch posture)."""
    return 0.70 < score < 0.92
