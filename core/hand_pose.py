"""
core.hand_pose
==============

Interpretacao da pose da mao pro HOLOGRAMA (pura: sem Qt/GL/cv2).

Converte os 21 landmarks do MediaPipe (normalizados pelo frame) em
pontos 3D de tela prontos pra qualquer renderer (mesh GL ou QPainter),
com o objetivo de a mao virtual se mexer como a mao real:

- **Aspecto correto.** x do MediaPipe e' fracao da LARGURA do frame e y
  da ALTURA; num frame 16:9 usar a mesma escala nos dois eixos achatava a
  mao na horizontal (~0.56x). Aqui tudo vira unidade isotropica
  (x * aspecto, y, z * aspecto — o z do MediaPipe segue a escala de x).
- **Escala estavel.** O tamanho vem do comprimento 3D da PALMA (suavizado),
  nao do bounding box: fechar o punho ou fazer pinca nao "infla" a mao.
- **Translacao 1:1 com o cursor, articulacao independente.** A mao e'
  posicionada pela palma; o ponto da pinca (polegar+indicador) e' levado
  ate o cursor por um offset suavizado — mexer os dedos nao arrasta a mao
  inteira, e em repouso a pinca fecha em cima do cursor.
- **Profundidade real.** O z de cada landmark vira profundidade em pixels
  (vista "por tras da mao", como em headset VR: dedos que fecham pra
  palma ficam atras do dorso).
- **Suavizacao 3D por landmark** (OneEuro), com z filtrado mais forte.
- **Forca da pinca** (0..1) pra feedback visual nas pontas dos dedos.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .smoothing import OneEuroSmoother3D

N_LANDMARKS = 21
LM_WRIST = 0
LM_THUMB_TIP = 4
LM_INDEX_MCP = 5
LM_INDEX_TIP = 8
LM_MIDDLE_MCP = 9
LM_PINKY_MCP = 17
PALM_IDX = (0, 5, 9, 13, 17)

# Comprimento da mao aberta (pulso -> ponta do medio) em "palmas"
# (pulso -> MCP do medio). hand_size_px = comprimento da mao aberta.
HAND_LENGTH_PALMS = 1.9

# Proporcoes tipicas (em palmas) usadas pra estimar o comprimento da
# palma a partir de varios segmentos — a media resiste melhor a mao
# girada (um segmento encurta, os outros nao).
_PALM_SEGMENTS: Tuple[Tuple[int, int, float], ...] = (
    (LM_WRIST, LM_INDEX_MCP, 0.95),
    (LM_WRIST, LM_MIDDLE_MCP, 1.00),
    (LM_WRIST, LM_PINKY_MCP, 0.85),
    (LM_INDEX_MCP, LM_PINKY_MCP, 0.70),
)

# Pinca: distancia polegar-indicador (em palmas) totalmente fechada/aberta.
_PINCH_CLOSED = 0.12
_PINCH_OPEN = 0.45


@dataclass(frozen=True)
class PosedHand:
    """Mao pronta pra renderizar.

    points: (21, 3) float32 — x, y em pixels de tela; z = profundidade em
        pixels (positivo = mais perto de quem olha a tela).
    palm_px: comprimento da palma em pixels (referencia de espessura).
    pinch: forca da pinca polegar+indicador, 0 (aberta) .. 1 (fechada).
    """

    points: np.ndarray
    palm_px: float
    pinch: float

    @property
    def pinch_point(self) -> Tuple[float, float]:
        a = self.points[LM_THUMB_TIP]
        b = self.points[LM_INDEX_TIP]
        return float((a[0] + b[0]) * 0.5), float((a[1] + b[1]) * 0.5)


def _ema_alpha(dt: float, tau: float) -> float:
    if tau <= 0.0:
        return 1.0
    return 1.0 - math.exp(-max(0.0, dt) / tau)


class HandPoseSolver:
    """Stateful: suavizacao e escala dependem do historico. Um por mao.

    Args:
        hand_size_px: comprimento da mao aberta na tela (pulso -> ponta).
        frame_aspect: largura/altura do frame da camera.
        min_cutoff, beta: OneEuro dos landmarks (unidades isotropicas).
        z_min_cutoff: OneEuro da profundidade (mais forte que x/y).
        scale_tau_s: constante de tempo da escala (tamanho da palma).
        offset_tau_s: constante de tempo do alinhamento pinca->cursor.
    """

    def __init__(
        self,
        *,
        hand_size_px: float = 180.0,
        frame_aspect: float = 16.0 / 9.0,
        min_cutoff: float = 1.2,
        beta: float = 2.0,
        z_min_cutoff: float = 0.6,
        scale_tau_s: float = 0.4,
        offset_tau_s: float = 0.5,
    ) -> None:
        self.hand_size_px = float(hand_size_px)
        self.frame_aspect = max(0.1, float(frame_aspect))
        self._min_cutoff = float(min_cutoff)
        self._beta = float(beta)
        self._z_min_cutoff = float(z_min_cutoff)
        self._scale_tau = float(scale_tau_s)
        self._offset_tau = float(offset_tau_s)
        self._filters: Optional[List[OneEuroSmoother3D]] = None
        self._palm_len: Optional[float] = None
        self._offset: Optional[np.ndarray] = None
        self._last_t: Optional[float] = None

    def set_frame_aspect(self, aspect: float) -> None:
        aspect = max(0.1, float(aspect))
        if abs(aspect - self.frame_aspect) > 1e-6:
            self.frame_aspect = aspect
            self.reset()

    def reset(self) -> None:
        self._filters = None
        self._palm_len = None
        self._offset = None
        self._last_t = None

    # ------------------------------------------------------------- API

    def solve(
        self,
        landmarks: Optional[Sequence[Sequence[float]]],
        cursor_x: float,
        cursor_y: float,
        now: Optional[float] = None,
    ) -> Optional[PosedHand]:
        if landmarks is None or len(landmarks) < N_LANDMARKS:
            self.reset()
            return None
        t = time.perf_counter() if now is None else float(now)
        dt = 0.0 if self._last_t is None else t - self._last_t
        self._last_t = t

        iso = self._smoothed_iso(landmarks, t)

        # Escala pela palma (3D), suavizada.
        palm_len = self._estimate_palm_length(iso)
        if self._palm_len is None:
            self._palm_len = palm_len
        else:
            self._palm_len += _ema_alpha(dt, self._scale_tau) * (palm_len - self._palm_len)
        palm = max(1e-6, self._palm_len)
        scale = self.hand_size_px / (HAND_LENGTH_PALMS * palm)

        # Posicao: palma ancorada; ponto da pinca levado ao cursor com
        # offset suavizado (articulacao nao arrasta a mao).
        center = iso[list(PALM_IDX), :2].mean(axis=0)
        pinch_mid = (iso[LM_THUMB_TIP, :2] + iso[LM_INDEX_TIP, :2]) * 0.5
        offset = pinch_mid - center
        if self._offset is None:
            self._offset = offset
        else:
            self._offset = self._offset + _ema_alpha(dt, self._offset_tau) * (offset - self._offset)

        pts = np.empty((N_LANDMARKS, 3), dtype=np.float32)
        pts[:, 0] = cursor_x + (iso[:, 0] - center[0] - self._offset[0]) * scale
        pts[:, 1] = cursor_y + (iso[:, 1] - center[1] - self._offset[1]) * scale
        # Vista por tras da mao: mais longe da camera = mais perto de quem
        # olha a tela.
        pts[:, 2] = (iso[:, 2] - iso[LM_WRIST, 2]) * scale

        d = float(np.linalg.norm(iso[LM_THUMB_TIP] - iso[LM_INDEX_TIP])) / palm
        pinch = 1.0 - min(1.0, max(0.0, (d - _PINCH_CLOSED) / (_PINCH_OPEN - _PINCH_CLOSED)))

        return PosedHand(points=pts, palm_px=palm * scale, pinch=pinch)

    # ------------------------------------------------------------ helpers

    def _smoothed_iso(self, landmarks: Sequence[Sequence[float]], t: float) -> np.ndarray:
        if self._filters is None:
            self._filters = [
                OneEuroSmoother3D(
                    freq=30.0,
                    min_cutoff=self._min_cutoff,
                    beta=self._beta,
                    d_cutoff=1.0,
                    z_min_cutoff=self._z_min_cutoff,
                )
                for _ in range(N_LANDMARKS)
            ]
        a = self.frame_aspect
        out = np.empty((N_LANDMARKS, 3), dtype=np.float64)
        for i in range(N_LANDMARKS):
            lm = landmarks[i]
            z = float(lm[2]) if len(lm) > 2 else 0.0
            out[i] = self._filters[i](float(lm[0]) * a, float(lm[1]), z * a, t)
        return out

    @staticmethod
    def _estimate_palm_length(iso: np.ndarray) -> float:
        total = 0.0
        for a, b, ratio in _PALM_SEGMENTS:
            total += float(np.linalg.norm(iso[a] - iso[b])) / ratio
        return total / len(_PALM_SEGMENTS)
