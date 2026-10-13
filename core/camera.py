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
from typing import Optional, Tuple

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
        # Numero de sequencia do frame: deixa o consumidor processar cada
        # frame da webcam UMA vez (sem reprocessar duplicatas).
        self._seq: int = 0
        self._cond = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._opened = False
        # True quando a webcam parou de entregar frames (desconectada ou
        # tomada por outro app). O thread tenta reabrir sozinho.
        self._stalled = False

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        """Abre o dispositivo (DirectShow primeiro, depois default)."""
        # Tenta backend especifico do SO (mais estavel)
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logger.debug("CAP_DSHOW falhou, tentando default")
            cap.release()
            cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            cap.release()
            return None

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Buffer minimo = sempre frame mais recente
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def open(self) -> "ThreadedCamera":
        """
        Abre a webcam e inicia o thread de captura.

        Tenta primeiro com backend nativo do SO; se falhar, usa default.
        """
        cap = self._open_capture()
        if cap is None:
            raise CameraOpenError(
                f"Nao foi possivel abrir a webcam (index={self.index}). "
                "Verifique se ela esta conectada e nao ocupada por outro app."
            )

        self._cap = cap
        self._opened = True
        self._stalled = False

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
        with self._cond:
            self._cond.notify_all()
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

    _MAX_FAILURES: int = 30
    _REOPEN_INTERVAL_S: float = 1.0

    def _capture_loop(self) -> None:
        """Roda em thread separada lendo frames sem parar.

        Se a webcam parar de responder (cabo USB solto, outro app pegou o
        dispositivo), marca `stalled`, descarta o ultimo frame (pra ninguem
        processar imagem congelada como se fosse ao vivo) e tenta reabrir
        a cada segundo ate voltar ou ate close().
        """
        consecutive_failures = 0

        while not self._stop_event.is_set():
            cap = self._cap or self._try_reopen()
            if cap is None:
                continue

            ok, frame = cap.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= self._MAX_FAILURES:
                    logger.error(
                        "Webcam falhou %d frames consecutivos — tentando "
                        "reconectar", self._MAX_FAILURES,
                    )
                    self._mark_stalled()
                    consecutive_failures = 0
                    continue
                time.sleep(0.01)
                continue

            consecutive_failures = 0
            with self._cond:
                self._frame = frame
                self._seq += 1
                self._cond.notify_all()

    def _mark_stalled(self) -> None:
        with self._cond:
            self._stalled = True
            self._frame = None
            self._cond.notify_all()
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _try_reopen(self) -> Optional[cv2.VideoCapture]:
        """Uma tentativa de reabrir. None = esperou o intervalo e falhou."""
        if self._stop_event.wait(self._REOPEN_INTERVAL_S):
            return None
        cap = self._open_capture()
        if cap is None:
            return None
        if self._stop_event.is_set():
            # close() chegou durante a abertura (DirectShow pode levar
            # segundos): nao deixar o dispositivo aberto sem dono.
            cap.release()
            return None
        self._cap = cap
        with self._cond:
            self._stalled = False
        logger.info("Webcam %d reconectada", self.index)
        return cap

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
        with self._cond:
            if self._frame is None:
                return None
            frame = self._frame.copy()

        if mirror:
            frame = cv2.flip(frame, 1)
        return frame

    def read_new(
        self,
        last_seq: int,
        timeout: float,
        mirror: bool = True,
    ) -> Tuple[Optional[np.ndarray], int]:
        """
        Espera ate `timeout` por um frame MAIS NOVO que `last_seq`.

        O loop principal costuma rodar mais rapido que a webcam (30 fps
        e' o comum). Com read() ele reprocessava o mesmo frame varias
        vezes: inferencia desperdicada e, pior, frames repetidos com
        velocidade zero distorciam histerese, sticky e follow-through.

        Returns:
            (frame, seq) — frame None se nada novo chegou no prazo; nesse
            caso seq volta igual a `last_seq`.
        """
        with self._cond:
            if self._seq == last_seq or self._frame is None:
                self._cond.wait_for(
                    lambda: (
                        (self._seq != last_seq and self._frame is not None)
                        or self._stop_event.is_set()
                    ),
                    timeout=timeout,
                )
            if self._seq == last_seq or self._frame is None:
                return None, last_seq
            frame = self._frame.copy()
            seq = self._seq

        if mirror:
            frame = cv2.flip(frame, 1)
        return frame, seq

    @property
    def is_stalled(self) -> bool:
        """Webcam parou de entregar frames e esta tentando reconectar."""
        return self._stalled

    @property
    def is_opened(self) -> bool:
        """Camera aberta e thread ativa."""
        return self._opened and self._cap is not None and self._cap.isOpened()
