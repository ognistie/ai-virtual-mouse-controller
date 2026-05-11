"""
core.hand_tracker
=================

Wrapper sobre MediaPipe Hands.

Responsabilidades:
- Encapsular configuracao do MediaPipe.
- Converter landmarks em estrutura tipada conveniente.
- Expor helpers para landmarks de interesse (4=polegar, 8=indicador,
  12=medio, 0=pulso) e para detectar dedos levantados.

NAO acopla com OpenCV de captura — recebe apenas um array RGB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import mediapipe as mp
import numpy as np


logger = logging.getLogger(__name__)


# Indices uteis dos 21 landmarks do MediaPipe Hands
LM_WRIST = 0
LM_THUMB_TIP = 4
LM_INDEX_TIP = 8
LM_MIDDLE_TIP = 12
LM_RING_TIP = 16
LM_PINKY_TIP = 20

# Pontas e juntas para deteccao de "dedo levantado"
FINGER_TIPS = (LM_INDEX_TIP, LM_MIDDLE_TIP, LM_RING_TIP, LM_PINKY_TIP)
FINGER_PIPS = (6, 10, 14, 18)  # juntas intermediarias correspondentes


@dataclass(frozen=True)
class HandLandmarks:
    """
    Landmarks de uma mao detectada.

    Coordenadas normalizadas (0-1) em relacao ao frame.
    landmarks[i] = (x, y, z) do landmark i.

    handedness: 'Left' ou 'Right' (do ponto de vista da camera).
    score: confianca da deteccao (0-1).
    """

    landmarks: Tuple[Tuple[float, float, float], ...]
    handedness: str
    score: float

    # Atalhos para os mais usados (em x, y normalizados)
    @property
    def wrist(self) -> Tuple[float, float]:
        return self.landmarks[LM_WRIST][0], self.landmarks[LM_WRIST][1]

    @property
    def thumb_tip(self) -> Tuple[float, float]:
        return self.landmarks[LM_THUMB_TIP][0], self.landmarks[LM_THUMB_TIP][1]

    @property
    def index_tip(self) -> Tuple[float, float]:
        return self.landmarks[LM_INDEX_TIP][0], self.landmarks[LM_INDEX_TIP][1]

    @property
    def middle_tip(self) -> Tuple[float, float]:
        return self.landmarks[LM_MIDDLE_TIP][0], self.landmarks[LM_MIDDLE_TIP][1]

    def fingers_up(self) -> List[bool]:
        """
        Retorna lista [thumb, index, middle, ring, pinky] indicando se
        cada dedo esta levantado.

        Logica:
        - Polegar: comparacao horizontal (x) ja que se move lateralmente.
        - Outros: ponta esta acima (y menor) da junta intermediaria.

        Robusto para mao direita/esquerda detectada pelo MediaPipe.
        """
        lm = self.landmarks

        # Polegar: depende da mao
        # MediaPipe ja considera a perspectiva, comparamos polegar contra
        # o landmark 3 (junta MCP do polegar)
        if self.handedness.lower().startswith("r"):
            thumb_up = lm[LM_THUMB_TIP][0] < lm[3][0]
        else:
            thumb_up = lm[LM_THUMB_TIP][0] > lm[3][0]

        result = [thumb_up]
        # Outros dedos: ponta com y menor que junta PIP = levantado
        for tip, pip in zip(FINGER_TIPS, FINGER_PIPS):
            result.append(lm[tip][1] < lm[pip][1])
        return result


class HandTracker:
    """
    Wrapper sobre MediaPipe Hands.

    Args:
        max_num_hands: Quantas maos detectar.
        min_detection_confidence: Threshold inicial.
        min_tracking_confidence: Threshold para manter tracking.
        model_complexity: 0 (lite) ou 1 (full).

    Examples:
        >>> tracker = HandTracker()
        >>> # frame_rgb = np.array(...) shape (H, W, 3) RGB
        >>> # hands = tracker.process(frame_rgb)
        >>> tracker.close()
    """

    def __init__(
        self,
        max_num_hands: int = 1,
        min_detection_confidence: float = 0.7,
        min_tracking_confidence: float = 0.6,
        model_complexity: int = 1,
    ) -> None:
        self._mp_hands = mp.solutions.hands
        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=max_num_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
            model_complexity=model_complexity,
        )
        self._mp_drawing = mp.solutions.drawing_utils
        self._mp_styles = mp.solutions.drawing_styles
        logger.info(
            "HandTracker inicializado (max_hands=%d, complexity=%d)",
            max_num_hands, model_complexity,
        )

    def process(self, frame_rgb: np.ndarray) -> List[HandLandmarks]:
        """
        Processa um frame RGB e retorna lista de maos detectadas.

        Args:
            frame_rgb: Array (H, W, 3) RGB uint8.

        Returns:
            Lista de HandLandmarks (vazia se nada detectado).
        """
        results = self._hands.process(frame_rgb)
        if not results.multi_hand_landmarks:
            return []

        hands: List[HandLandmarks] = []
        handedness_list = results.multi_handedness or []

        for i, hand_lms in enumerate(results.multi_hand_landmarks):
            # Handedness e score
            handedness = "Right"
            score = 0.0
            if i < len(handedness_list):
                cl = handedness_list[i].classification[0]
                handedness = cl.label
                score = float(cl.score)

            landmarks_tuple = tuple(
                (float(lm.x), float(lm.y), float(lm.z)) for lm in hand_lms.landmark
            )
            hands.append(
                HandLandmarks(
                    landmarks=landmarks_tuple,
                    handedness=handedness,
                    score=score,
                )
            )

        return hands

    def draw(self, frame_bgr: np.ndarray, hands_raw_results=None) -> None:
        """
        Desenha landmarks no frame BGR (in-place).

        Para usar isto, recomenda-se chamar `process_with_raw` para
        manter o resultado MediaPipe original; alternativa abaixo.
        """
        # Passamos por process_with_raw em camadas superiores para nao
        # reprocessar; aqui mantemos signature simples por ora.
        if hands_raw_results and hands_raw_results.multi_hand_landmarks:
            for hand_lms in hands_raw_results.multi_hand_landmarks:
                self._mp_drawing.draw_landmarks(
                    frame_bgr,
                    hand_lms,
                    self._mp_hands.HAND_CONNECTIONS,
                    self._mp_styles.get_default_hand_landmarks_style(),
                    self._mp_styles.get_default_hand_connections_style(),
                )

    def process_with_raw(self, frame_rgb: np.ndarray):
        """
        Versao que retorna (lista_HandLandmarks, raw_results) para quem
        precisa desenhar via MediaPipe drawing utils.
        """
        results = self._hands.process(frame_rgb)
        hands = self._results_to_hands(results)
        return hands, results

    def _results_to_hands(self, results) -> List[HandLandmarks]:
        if not results.multi_hand_landmarks:
            return []
        out: List[HandLandmarks] = []
        handedness_list = results.multi_handedness or []
        for i, hand_lms in enumerate(results.multi_hand_landmarks):
            handedness = "Right"
            score = 0.0
            if i < len(handedness_list):
                cl = handedness_list[i].classification[0]
                handedness = cl.label
                score = float(cl.score)
            landmarks_tuple = tuple(
                (float(lm.x), float(lm.y), float(lm.z)) for lm in hand_lms.landmark
            )
            out.append(
                HandLandmarks(
                    landmarks=landmarks_tuple,
                    handedness=handedness,
                    score=score,
                )
            )
        return out

    def close(self) -> None:
        """Libera recursos do MediaPipe."""
        try:
            self._hands.close()
        except Exception as e:
            logger.warning("Erro ao fechar MediaPipe: %s", e)
