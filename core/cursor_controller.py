"""
core.cursor_controller
=======================

Wrapper sobre PyAutoGUI para controle do cursor do sistema.

Responsabilidades:
- Mapear coordenadas normalizadas (0-1) para pixel do monitor
- Aplicar dead zone para reduzir tremor de cursor parado
- Executar acoes do mouse: click, double_click, right_click, drag, scroll

Notas tecnicas:
- double_click() usa intervalo explicito entre os 2 cliques (~100ms),
  necessario para que apps como Explorer/Chrome reconhecam o duplo clique
  do sistema. pyautogui.doubleClick() pode disparar rapido demais.
- right_click() usa pyautogui.rightClick() (NOVO v6.9).
- _pause=False em todas as chamadas: o cooldown de gestos e tratado a
  montante pelo GestureDetector.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

import pyautogui

from .utils import clamp, map_range


logger = logging.getLogger(__name__)


class CursorController:
    """Controlador do cursor do sistema."""

    def __init__(
        self,
        screen_margin_percentage: float = 0.10,
        dead_zone_pixels: int = 3,
        failsafe: bool = False,
        pyautogui_pause: float = 0.0,
        double_click_interval: float = 0.10,
    ) -> None:
        if not 0.0 <= screen_margin_percentage < 0.5:
            raise ValueError(
                f"screen_margin_percentage deve estar em [0, 0.5)"
            )

        pyautogui.FAILSAFE = failsafe
        pyautogui.PAUSE = pyautogui_pause

        self.screen_margin = screen_margin_percentage
        self.dead_zone = max(0, dead_zone_pixels)
        self.double_click_interval = double_click_interval

        self.screen_width, self.screen_height = pyautogui.size()
        self._last_x: Optional[int] = None
        self._last_y: Optional[int] = None
        self._dragging = False

        logger.info(
            "CursorController iniciado | tela=%dx%d | margin=%.2f | dead_zone=%dpx | dbl_interval=%.2fs",
            self.screen_width, self.screen_height,
            self.screen_margin, self.dead_zone, self.double_click_interval,
        )

    def map_to_screen(self, nx: float, ny: float) -> Tuple[int, int]:
        m = self.screen_margin
        x_pixel = map_range(nx, m, 1.0 - m, 0, self.screen_width - 1)
        y_pixel = map_range(ny, m, 1.0 - m, 0, self.screen_height - 1)
        x_pixel = clamp(x_pixel, 0, self.screen_width - 1)
        y_pixel = clamp(y_pixel, 0, self.screen_height - 1)
        return int(x_pixel), int(y_pixel)

    def move(self, nx: float, ny: float) -> None:
        x, y = self.map_to_screen(nx, ny)

        if self._last_x is not None and self._last_y is not None:
            if abs(x - self._last_x) < self.dead_zone and \
               abs(y - self._last_y) < self.dead_zone:
                return

        try:
            pyautogui.moveTo(x, y, _pause=False)
        except Exception as e:
            logger.warning("Falha ao mover cursor: %s", e)
            return

        self._last_x = x
        self._last_y = y

    def click(self) -> None:
        try:
            pyautogui.click(_pause=False)
            logger.debug("CLICK executado")
        except Exception as e:
            logger.warning("Falha ao clicar: %s", e)

    def click_at(self, x: int, y: int) -> None:
        """
        Clica em coordenada absoluta da tela (sem usar a posicao do cursor).

        Usado pelo holograma: o clique acontece no ponto de pinch entre os
        dedos (nao na posicao do cursor anchor), de forma que a mao realmente
        substitui o cursor pro evento de clique.
        """
        try:
            x_clamped = clamp(int(x), 0, self.screen_width - 1)
            y_clamped = clamp(int(y), 0, self.screen_height - 1)
            pyautogui.click(x=x_clamped, y=y_clamped, _pause=False)
            self._last_x = x_clamped
            self._last_y = y_clamped
            logger.debug("CLICK_AT (%s, %s)", x_clamped, y_clamped)
        except Exception as e:
            logger.warning("Falha em click_at(%s, %s): %s", x, y, e)

    def double_click(self) -> None:
        """
        FIX v6.1: Faz duplo clique MANUALMENTE com interval explicito.

        Ao inves de usar pyautogui.doubleClick() (que pode disparar muito
        rapido em alguns sistemas), executamos dois cliques separados com
        delay configurable. Isso garante que apps como Explorer, Chrome,
        etc. reconhecam como duplo clique do sistema.
        """
        try:
            pyautogui.click(_pause=False)
            time.sleep(self.double_click_interval)
            pyautogui.click(_pause=False)
            logger.debug("DOUBLE_CLICK executado (interval=%.2fs)", self.double_click_interval)
        except Exception as e:
            logger.warning("Falha no duplo clique: %s", e)

    def double_click_at(self, x: int, y: int) -> None:
        """Duplo clique em coordenada absoluta."""
        try:
            x_clamped = clamp(int(x), 0, self.screen_width - 1)
            y_clamped = clamp(int(y), 0, self.screen_height - 1)
            pyautogui.moveTo(x_clamped, y_clamped, _pause=False)
            pyautogui.click(_pause=False)
            time.sleep(self.double_click_interval)
            pyautogui.click(_pause=False)
            self._last_x = x_clamped
            self._last_y = y_clamped
            logger.debug("DOUBLE_CLICK_AT (%s, %s)", x_clamped, y_clamped)
        except Exception as e:
            logger.warning("Falha em double_click_at: %s", e)

    def right_click(self) -> None:
        """
        NOVO v6.9: clique direito do mouse.

        Aciona o menu de contexto (Copiar, Colar, Inspecionar, etc.) — mesma
        coisa que o botao direito de um mouse fisico ou Ctrl+Click no macOS.

        Usa pyautogui.rightClick() que e a API estavel da biblioteca para
        esse evento. _pause=False mantem o tempo zero entre cliques (nosso
        cooldown ja e tratado pelo GestureDetector).
        """
        try:
            pyautogui.rightClick(_pause=False)
            logger.debug("RIGHT_CLICK executado")
        except Exception as e:
            logger.warning("Falha em right_click: %s", e)

    def right_click_at(self, x: int, y: int) -> None:
        """Clique direito em coordenada absoluta."""
        try:
            x_clamped = clamp(int(x), 0, self.screen_width - 1)
            y_clamped = clamp(int(y), 0, self.screen_height - 1)
            pyautogui.rightClick(x=x_clamped, y=y_clamped, _pause=False)
            self._last_x = x_clamped
            self._last_y = y_clamped
            logger.debug("RIGHT_CLICK_AT (%s, %s)", x_clamped, y_clamped)
        except Exception as e:
            logger.warning("Falha em right_click_at: %s", e)

    def drag_start(self) -> None:
        if self._dragging:
            return
        try:
            pyautogui.mouseDown(_pause=False)
            self._dragging = True
            logger.debug("DRAG_START")
        except Exception as e:
            logger.warning("Falha em drag_start: %s", e)

    def drag_start_at(self, x: int, y: int) -> None:
        """Inicia drag em coordenada absoluta."""
        if self._dragging:
            return
        try:
            x_clamped = clamp(int(x), 0, self.screen_width - 1)
            y_clamped = clamp(int(y), 0, self.screen_height - 1)
            pyautogui.mouseDown(x=x_clamped, y=y_clamped, _pause=False)
            self._dragging = True
            self._last_x = x_clamped
            self._last_y = y_clamped
            logger.debug("DRAG_START_AT (%s, %s)", x_clamped, y_clamped)
        except Exception as e:
            logger.warning("Falha em drag_start_at: %s", e)

    def drag_end(self) -> None:
        if not self._dragging:
            return
        try:
            pyautogui.mouseUp(_pause=False)
            self._dragging = False
            logger.debug("DRAG_END")
        except Exception as e:
            logger.warning("Falha em drag_end: %s", e)

    def scroll(self, amount: int) -> None:
        try:
            pyautogui.scroll(amount, _pause=False)
        except Exception as e:
            logger.warning("Falha em scroll: %s", e)

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    def force_release(self) -> None:
        if self._dragging:
            try:
                pyautogui.mouseUp(_pause=False)
            except Exception:
                pass
            self._dragging = False