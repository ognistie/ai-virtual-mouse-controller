"""
core.camera
===========

Captura assincrona de webcam com threading.

Por que threading?
- cv2.VideoCapture.read() e blocking
- Fazer no thread principal derruba FPS quando processamento e pesado
- Solucao: thread daemon que sempre le o frame mais recente, descarta antigos
- Resultado: ~60 FPS estavel vs ~25 FPS sem thread

Uso:
    with ThreadedCamera(0) as cam:
        while True:
            frame = cam.read()
            if frame is None:
                continue
            # processa frame...
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np


logger = logging.getLogger(__name__)


class CameraOpenError(RuntimeError):
    """Webcam nao pode ser aberta (ocupada ou inexistente)."""


class ThreadedCamera:
    """
    Wrapper de cv2.VideoCapture com captura em thread separada.

    Mantem apenas o ultimo frame disponivel — descarta atrasados.
    Suporta context manager para release garantido.

    Args:
        index: Indice da webcam (geralmente 0).
        width: Largura solicitada.
        height: Altura solicitada.
        fps: FPS alvo. O hardware decide o real.
        warmup_seconds: Tempo de aquecimento da webcam apos abrir.

    Raises:
        CameraOpenError: Se a webcam nao puder ser aberta.
    """

    def __init__(
        self,
        index: int = 0,
        width: int = 960,
        height: int = 540,
        fps: int = 60,
        warmup_seconds: float = 0.5,
    ) -> None:
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self.warmup_seconds = warmup_seconds

        self._cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._opened = False

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def open(self) -> "ThreadedCamera":
        """
        Abre a webcam e inicia o thread de captura.

        Tenta primeiro com backend nativo do SO; se falhar, usa default.
        """
        # Tenta backend especifico do SO (mais estavel)
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logger.debug("CAP_DSHOW falhou, tentando default")
            cap = cv2.VideoCapture(self.index)

        if not cap.isOpened():
            raise CameraOpenError(
                f"Nao foi possivel abrir a webcam (index={self.index}). "
                "Verifique se ela esta conectada e nao ocupada por outro app."
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Buffer minimo = sempre frame mais recente
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._cap = cap
        self._opened = True

        # Aquece a webcam (descarta primeiros frames pretos)
        time.sleep(self.warmup_seconds)

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="CameraCaptureThread",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Camera %d aberta (%dx%d @ %d fps)",
            self.index, self.width, self.height, self.fps,
        )
        return self

    def close(self) -> None:
        """Fecha thread e libera webcam."""
        if not self._opened:
            return

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        if self._cap is not None:
            self._cap.release()
            self._cap = None

        self._opened = False
        logger.info("Camera %d fechada", self.index)

    def __enter__(self) -> "ThreadedCamera":
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -----------------------------------------------------------------
    # Loop interno
    # -----------------------------------------------------------------

    def _capture_loop(self) -> None:
        """Roda em thread separada lendo frames sem parar."""
        consecutive_failures = 0
        max_failures = 30

        while not self._stop_event.is_set():
            if self._cap is None:
                break

            ok, frame = self._cap.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    logger.error(
                        "Webcam falhou %d frames consecutivos, parando thread",
                        max_failures,
                    )
                    break
                time.sleep(0.01)
                continue

            consecutive_failures = 0

            with self._lock:
                self._frame = frame

    # -----------------------------------------------------------------
    # API publica
    # -----------------------------------------------------------------

    def read(self, mirror: bool = True) -> Optional[np.ndarray]:
        """
        Retorna o frame mais recente capturado.

        Args:
            mirror: Espelhar horizontalmente (movimento natural do usuario).

        Returns:
            Frame BGR (numpy array) ou None se nada disponivel ainda.
        """
        with self._lock:
            if self._frame is None:
                return None
            frame = self._frame.copy()

        if mirror:
            frame = cv2.flip(frame, 1)
        return frame

    @property
    def is_opened(self) -> bool:
        """Camera aberta e thread ativa."""
        return self._opened and self._cap is not None and self._cap.isOpened()
