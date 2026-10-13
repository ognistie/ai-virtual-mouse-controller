"""
core.hand_tracker
=================

Wrapper sobre MediaPipe Hand Landmarker (Tasks API).

Responsabilidades:
- Encapsular configuracao do MediaPipe.
- Converter landmarks em estrutura tipada conveniente.
- Expor helpers para landmarks de interesse (4=polegar, 8=indicador,
  12=medio, 0=pulso) e para detectar dedos levantados.

NAO acopla com OpenCV de captura — recebe apenas um array RGB.

Por que Tasks API (e nao mp.solutions.hands)?
- mp.solutions foi removido no mediapipe 0.10.30. As versoes antigas
  (<=0.10.21) so tem wheels pra Python 3.9-3.12 e exigem numpy<2.
- A partir da 0.10.30 os wheels sao `py3-none` (qualquer Python 3), entao
  a Tasks API destrava Python 3.13, 3.14 e futuras.
- A Tasks API existe desde a 0.10.0, entao o mesmo codigo roda tanto no
  mediapipe antigo (ex: Mac Intel, que nao tem wheel novo) quanto no novo.

O modelo (`models/hand_landmarker.task`, ~7.8MB) vai junto do pacote:
roda offline, sem download no primeiro uso.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision as mp_vision


logger = logging.getLogger(__name__)


MODEL_PATH = Path(__file__).resolve().parent / "models" / "hand_landmarker.task"

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

# Conexoes do esqueleto (mesmas de mp.solutions.hands.HAND_CONNECTIONS),
# agrupadas por dedo pra colorir o preview.
_FINGER_CHAINS: Tuple[Tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4),      # polegar
    (5, 6, 7, 8),         # indicador
    (9, 10, 11, 12),      # medio
    (13, 14, 15, 16),     # anelar
    (17, 18, 19, 20),     # minimo
)
_PALM_CONNECTIONS: Tuple[Tuple[int, int], ...] = (
    (0, 5), (5, 9), (9, 13), (13, 17), (0, 17),
)
# Cores BGR por dedo (proximas do estilo default do mediapipe).
_FINGER_COLORS_BGR: Tuple[Tuple[int, int, int], ...] = (
    (180, 229, 255),  # polegar
    (128, 64, 128),   # indicador
    (0, 204, 255),    # medio
    (48, 255, 48),    # anelar
    (192, 101, 21),   # minimo
)
_PALM_COLOR_BGR = (128, 128, 128)
_JOINT_COLOR_BGR = (48, 48, 255)

_SWAP_HANDEDNESS = {"Left": "Right", "Right": "Left"}


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
    Wrapper sobre MediaPipe Hand Landmarker (modo VIDEO, com tracking
    entre frames).

    Args:
        max_num_hands: Quantas maos detectar.
        min_detection_confidence: Threshold inicial.
        min_tracking_confidence: Threshold para manter tracking.
        model_complexity: Mantido por compatibilidade com config.py. A
            Tasks API tem um unico modelo, entao o valor e' ignorado.
        model_path: Caminho do `.task`. Default = modelo empacotado.

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
        model_path: Optional[Path] = None,
    ) -> None:
        path = Path(model_path) if model_path is not None else MODEL_PATH
        # Carrega por buffer (bytes) em vez de caminho: o loader nativo do
        # mediapipe pode falhar no Windows com acento no path (C:\Users\Joao).
        model_bytes = path.read_bytes()
        options = mp_vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=model_bytes),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=max_num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_tracking_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        try:
            self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        except OSError as e:
            # mediapipe 0.10.30+ carrega a lib nativa via ctypes; no Linux ela
            # depende do libEGL, ausente em instalacoes minimas.
            if "libEGL" in str(e) or "libGLES" in str(e):
                raise RuntimeError(
                    f"{e}\nO MediaPipe precisa do libEGL. No Ubuntu/Debian: "
                    "sudo apt install libegl1 libgles2"
                ) from e
            raise
        # VIDEO mode exige timestamps estritamente crescentes (ms).
        self._t0 = time.monotonic()
        self._last_ts_ms = -1
        logger.info(
            "HandTracker inicializado (max_hands=%d, model=%s)",
            max_num_hands, path.name,
        )
        if model_complexity != 1:
            logger.debug(
                "model_complexity=%d ignorado: Tasks API usa modelo unico",
                model_complexity,
            )

    def _next_timestamp_ms(self) -> int:
        ts = int((time.monotonic() - self._t0) * 1000)
        if ts <= self._last_ts_ms:
            ts = self._last_ts_ms + 1
        self._last_ts_ms = ts
        return ts

    def process(self, frame_rgb: np.ndarray) -> List[HandLandmarks]:
        """
        Processa um frame RGB e retorna lista de maos detectadas.

        Args:
            frame_rgb: Array (H, W, 3) RGB uint8.

        Returns:
            Lista de HandLandmarks (vazia se nada detectado).
        """
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(frame_rgb),
        )
        result = self._landmarker.detect_for_video(image, self._next_timestamp_ms())
        return self._result_to_hands(result)

    def process_with_raw(self, frame_rgb: np.ndarray):
        """
        Versao que retorna (lista_HandLandmarks, raw) para quem precisa
        desenhar o esqueleto. `raw` e' a propria lista, aceita por `draw`.
        """
        hands = self.process(frame_rgb)
        return hands, hands

    @staticmethod
    def _result_to_hands(result) -> List[HandLandmarks]:
        if not result.hand_landmarks:
            return []
        out: List[HandLandmarks] = []
        handedness_list = result.handedness or []
        for i, hand_lms in enumerate(result.hand_landmarks):
            handedness = "Right"
            score = 0.0
            if i < len(handedness_list) and handedness_list[i]:
                cat = handedness_list[i][0]
                label = cat.category_name or cat.display_name or ""
                # mp.solutions assumia imagem espelhada (selfie) e a Tasks
                # API nao: o rotulo vem trocado. Destroca pra manter a
                # convencao que fingers_up() sempre usou.
                handedness = _SWAP_HANDEDNESS.get(label, handedness)
                score = float(cat.score)
            landmarks_tuple = tuple(
                (float(lm.x), float(lm.y), float(lm.z)) for lm in hand_lms
            )
            out.append(
                HandLandmarks(
                    landmarks=landmarks_tuple,
                    handedness=handedness,
                    score=score,
                )
            )
        return out

    def draw(self, frame_bgr: np.ndarray, hands: Optional[List[HandLandmarks]] = None) -> None:
        """Desenha o esqueleto das maos no frame BGR (in-place)."""
        if not hands:
            return
        import cv2

        h, w = frame_bgr.shape[:2]
        for hand in hands:
            pts = [(int(x * w), int(y * h)) for x, y, _z in hand.landmarks]
            for a, b in _PALM_CONNECTIONS:
                cv2.line(frame_bgr, pts[a], pts[b], _PALM_COLOR_BGR, 2, cv2.LINE_AA)
            for chain, color in zip(_FINGER_CHAINS, _FINGER_COLORS_BGR):
                for a, b in zip(chain, chain[1:]):
                    cv2.line(frame_bgr, pts[a], pts[b], color, 2, cv2.LINE_AA)
            for p in pts:
                cv2.circle(frame_bgr, p, 3, _JOINT_COLOR_BGR, -1, cv2.LINE_AA)

    def close(self) -> None:
        """Libera recursos do MediaPipe."""
        try:
            self._landmarker.close()
        except Exception as e:
            logger.warning("Erro ao fechar MediaPipe: %s", e)
