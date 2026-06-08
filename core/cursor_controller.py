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
        click_freeze_seconds: float = 0.12,
        drag_precision_factor: float = 0.55,
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
        # v6.9.13 — feel de mouse fisico:
        # click_freeze: quanto tempo o cursor fica TRAVADO no pixel atual
        # imediatamente apos um click. Em mouse fisico, apertar o botao
        # NUNCA move o cursor; aqui o curl dos dedos no pinch desloca o
        # midpoint e o cursor andava 5-15px na hora do click. Freeze
        # ANULA esse drift — clique cai exatamente onde voce mirou.
        self.click_freeze_seconds = max(0.0, float(click_freeze_seconds))
        # drag_precision_factor: quanto escala o delta do cursor enquanto
        # DRAG esta ativo. 0.55 = mao precisa andar quase 2x pra cobrir a
        # mesma distancia em pixel — replica o feel de "mouse DPI baixo"
        # que profissionais usam pra selecionar texto/icones sem tremedeira.
        self.drag_precision_factor = max(0.1, min(1.0, float(drag_precision_factor)))

        self.screen_width, self.screen_height = pyautogui.size()
        self._last_x: Optional[int] = None
        self._last_y: Optional[int] = None
        self._dragging = False

        # Estado do freeze (timestamp futuro ate o qual ignoramos move).
        self._frozen_until: float = 0.0

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
        # Click freeze: enquanto travado, ignoramos todas as updates de
        # posicao. O cursor permanece exatamente onde estava no instante
        # do click — feel de mouse fisico (apertar botao nao move).
        if self._frozen_until > 0.0 and time.perf_counter() < self._frozen_until:
            return

        x, y = self.map_to_screen(nx, ny)

        # Drag precision: enquanto DRAG ativo, escala o delta — replica o
        # feel de DPI baixo pra selecao de texto / movimentos finos.
        # Aplicado em PIXEL space pra ser independente de escala normalizada.
        if (
            self._dragging
            and self._last_x is not None
            and self._last_y is not None
        ):
            dx = (x - self._last_x) * self.drag_precision_factor
            dy = (y - self._last_y) * self.drag_precision_factor
            x = int(round(self._last_x + dx))
            y = int(round(self._last_y + dy))

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

    # ---------------------------------------------------------- Freeze API

    def freeze(self, duration_seconds: float) -> None:
        """Trava o cursor no pixel atual por N segundos.

        Usado pelos metodos de click pra anular o drift causado pelo
        curl dos dedos durante o pinch — clique cai exatamente onde
        voce mirou, igual mouse fisico.

        Composavel: chamadas sucessivas levam o freeze ATE o maior
        dos timestamps (nao reset).
        """
        if duration_seconds <= 0.0:
            return
        target = time.perf_counter() + duration_seconds
        if target > self._frozen_until:
            self._frozen_until = target

    def is_frozen(self) -> bool:
        return (
            self._frozen_until > 0.0
            and time.perf_counter() < self._frozen_until
        )

    def click(self) -> None:
        # Trava o cursor ANTES de disparar — frames seguintes da pose
        # podem chegar com pinch midpoint deslocado pelo curl dos dedos,
        # mas o cursor ja esta congelado no pixel mirado.
        self.freeze(self.click_freeze_seconds)
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
        # Freeze cobre os dois cliques + interval (composavel).
        self.freeze(self.click_freeze_seconds + self.double_click_interval)
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
        self.freeze(self.click_freeze_seconds)
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
        # Freeze breve no inicio do drag pra que o mouseDown caia no pixel
        # exato onde o usuario mirou (sem drift do curl). Depois do freeze,
        # drag_precision_factor entra em acao escalando o movimento.
        self.freeze(self.click_freeze_seconds)
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
        # Freeze no drop tambem — quando o usuario solta o pinch o curl
        # reverso pode arrastar o cursor 5-10px antes do mouseUp registrar.
        self.freeze(self.click_freeze_seconds)
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