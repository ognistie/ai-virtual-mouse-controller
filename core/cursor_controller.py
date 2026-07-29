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
- right_click() usa pyautogui.rightClick().
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
        screen_margin_x: Optional[float] = None,
        screen_margin_top: Optional[float] = None,
        screen_margin_bottom: Optional[float] = None,
        y_bottom_boost_enabled: bool = False,
        y_bottom_boost_knee: float = 0.72,
        y_bottom_boost_power: float = 1.6,
        edge_snap_px: int = 0,
        edge_creep_enabled: bool = False,
        edge_creep_band_px: int = 72,
        edge_creep_delay_s: float = 0.15,
        edge_creep_rate_px_s: float = 320.0,
        bottom_reach_enabled: bool = False,
        bottom_reach_band_px: int = 120,
        bottom_reach_max_gain: float = 3.0,
        bottom_reach_max_extra_px: int = 60,
    ) -> None:
        # Margens assimetricas: top/bottom geralmente menores que X.
        # Anatomia + camera 16:9 — alcance horizontal e' mais confortavel
        # que vertical (braco contra gravidade). Menos margem vertical
        # = canto de tela acessivel com menos esforco fisico.
        # Quando None, cai pro valor isotropico (compat com callers antigos).
        if not 0.0 <= screen_margin_percentage < 0.5:
            raise ValueError(
                "screen_margin_percentage deve estar em [0, 0.5)"
            )
        mx = screen_margin_x if screen_margin_x is not None else screen_margin_percentage
        mt = screen_margin_top if screen_margin_top is not None else screen_margin_percentage
        mb = screen_margin_bottom if screen_margin_bottom is not None else screen_margin_percentage
        for name, val in (("x", mx), ("top", mt), ("bottom", mb)):
            if not 0.0 <= val < 0.5:
                raise ValueError(f"screen_margin_{name} deve estar em [0, 0.5)")

        pyautogui.FAILSAFE = failsafe
        pyautogui.PAUSE = pyautogui_pause

        self.screen_margin = screen_margin_percentage
        self.screen_margin_x = float(mx)
        self.screen_margin_top = float(mt)
        self.screen_margin_bottom = float(mb)
        self.dead_zone = max(0, dead_zone_pixels)
        self.double_click_interval = double_click_interval
        # Feel de mouse fisico:
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

        # Alcance da borda inferior (ver config.py, secao "ALCANCE DA
        # BORDA INFERIOR"). Cada mecanismo cobre um elo da cadeia que
        # impede o cursor de chegar na taskbar.
        self.y_bottom_boost_enabled = bool(y_bottom_boost_enabled)
        self.y_bottom_boost_knee = min(0.95, max(0.0, float(y_bottom_boost_knee)))
        self.y_bottom_boost_power = max(1.0, float(y_bottom_boost_power))
        self.edge_snap_px = max(0, int(edge_snap_px))
        self.edge_creep_enabled = bool(edge_creep_enabled)
        self.edge_creep_band_px = max(1, int(edge_creep_band_px))
        self.edge_creep_delay_s = max(0.0, float(edge_creep_delay_s))
        self.edge_creep_rate_px_s = max(0.0, float(edge_creep_rate_px_s))

        # Assistencia suave da borda inferior (substitui snap + creep).
        # Empurra o cursor um pouco MAIS pra baixo, proporcional ao quanto
        # o usuario ja esta descendo e a proximidade da borda. Sem
        # teleporte, sem mover cursor parado. Ver _apply_bottom_reach.
        self.bottom_reach_enabled = bool(bottom_reach_enabled)
        self.bottom_reach_band_px = max(1, int(bottom_reach_band_px))
        self.bottom_reach_max_gain = max(1.0, float(bottom_reach_max_gain))
        self.bottom_reach_max_extra_px = max(0, int(bottom_reach_max_extra_px))
        # Movimento descendente abaixo deste piso (px/frame) conta como
        # "parado" — nao dispara assistencia (evita deriva com jitter).
        self._bottom_reach_eps_px = 1

        self.screen_width, self.screen_height = pyautogui.size()
        self._last_x: Optional[int] = None
        self._last_y: Optional[int] = None
        self._dragging = False

        # Estado do freeze (timestamp futuro ate o qual ignoramos move).
        self._frozen_until: float = 0.0

        # Estado do border creep: offset acumulado (px) empurrando o
        # cursor pra borda + timestamps de permanencia na banda.
        # _last_raw_y rastreia o y mapeado SEM assistencias — a direcao
        # do movimento da mao e' decidida por ele; comparar com o y
        # assistido faria o proprio offset parecer "movimento pra cima"
        # e cancelaria o creep no frame seguinte.
        self._creep_offset: float = 0.0
        self._creep_entered_at: Optional[float] = None
        self._creep_last_t: Optional[float] = None
        self._last_raw_y: Optional[int] = None

        logger.info(
            "CursorController iniciado | tela=%dx%d | margin x=%.2f top=%.2f bot=%.2f | dead_zone=%dpx",
            self.screen_width, self.screen_height,
            self.screen_margin_x, self.screen_margin_top,
            self.screen_margin_bottom, self.dead_zone,
        )

    def map_to_screen(self, nx: float, ny: float) -> Tuple[int, int]:
        mx = self.screen_margin_x
        mt = self.screen_margin_top
        mb = self.screen_margin_bottom
        x_pixel = map_range(nx, mx, 1.0 - mx, 0, self.screen_width - 1)

        # Eixo Y em duas etapas: normaliza (0=topo, 1=fundo apos margens)
        # e aplica boost opcional no trecho inferior antes de escalar.
        ty = map_range(ny, mt, 1.0 - mb, 0.0, 1.0)
        ty = clamp(ty, 0.0, 1.0)
        if self.y_bottom_boost_enabled:
            ty = self._boost_bottom(ty)
        y_pixel = ty * (self.screen_height - 1)

        x_pixel = clamp(x_pixel, 0, self.screen_width - 1)
        y_pixel = clamp(y_pixel, 0, self.screen_height - 1)
        return int(x_pixel), int(y_pixel)

    def _boost_bottom(self, ty: float) -> float:
        """Remapeia o trecho inferior do eixo Y com ease-out.

        Acima do joelho o mapeamento fica linear (precisao intacta no
        miolo da tela). Abaixo, o restante do frame cobre o restante da
        tela com aceleracao: a mao alcanca o fundo sem entrar na zona
        onde o MediaPipe degrada (wrist saindo do FOV). Continua em
        ty=knee (sem degrau) e termina exatamente em 1.0.
        """
        knee = self.y_bottom_boost_knee
        if ty <= knee or knee >= 1.0:
            return ty
        u = (ty - knee) / (1.0 - knee)
        eased = 1.0 - (1.0 - u) ** self.y_bottom_boost_power
        return knee + (1.0 - knee) * eased

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

        y = self._apply_bottom_reach(y)

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

    def _apply_bottom_reach(self, y: int) -> int:
        """Fecha o gap terminal acima da taskbar.

        Assistencia suave (padrao, bottom_reach_enabled): descendo dentro
        da faixa inferior, o cursor ganha um empurrao EXTRA proporcional a
        (a) quanto o usuario esta descendo NESTE frame e (b) proximidade
        da borda (smoothstep). Propriedades:
          - sem teleporte: o empurrao e' um ganho sobre o passo do
            usuario, limitado por bottom_reach_max_extra_px;
          - cursor parado nao se move (raw_dy ~ 0 => empurrao ~ 0);
          - subir cancela na hora;
          - C1 na entrada da faixa (smoothstep tem derivada zero), entao
            entrar na faixa nao muda a velocidade aparente em degrau.

        Edge snap e border creep (legado) seguem implementados abaixo,
        porem desligados por padrao (ver config.py). O snap saltava direto
        pro ultimo pixel; o creep deslizava com a mao parada.

        Subir a mao cancela qualquer assistencia imediatamente.
        """
        bottom = self.screen_height - 1
        # Direcao/velocidade decididas pelo y CRU (pre-assistencia), com
        # ~3px de tolerancia pro jitter natural da mao parada.
        prev_raw = self._last_raw_y
        moving_down = prev_raw is None or y >= prev_raw - 3
        self._last_raw_y = y

        # Subiu de verdade: cancela assistencias, cursor volta ao controle
        if not moving_down:
            self._reset_creep()
            return y

        # Edge snap (legado) — gap terminal por teleporte. Desligado (0).
        if self.edge_snap_px > 0 and y >= bottom - self.edge_snap_px:
            self._reset_creep()
            return bottom

        # Assistencia suave (padrao): ganho no movimento descendente perto
        # da borda. Como e' proporcional ao passo do usuario, mao parada
        # nao gera empurrao — o cursor nao foge sozinho.
        if self.bottom_reach_enabled and prev_raw is not None:
            raw_dy = y - prev_raw
            if raw_dy <= self._bottom_reach_eps_px:
                return y  # parado ou quase: sem assistencia
            band = self.bottom_reach_band_px
            prox = (y - (bottom - band)) / band
            if prox <= 0.0:
                return y  # acima da faixa
            if prox > 1.0:
                prox = 1.0
            ramp = prox * prox * (3.0 - 2.0 * prox)  # smoothstep -> C1
            extra = (self.bottom_reach_max_gain - 1.0) * ramp * raw_dy
            if extra > self.bottom_reach_max_extra_px:
                extra = float(self.bottom_reach_max_extra_px)
            y_assisted = y + int(round(extra))
            return bottom if y_assisted > bottom else y_assisted

        # Border creep (legado) — homing parado na banda. Desligado.
        if not self.edge_creep_enabled:
            return y
        in_band = y >= self.screen_height - self.edge_creep_band_px
        if not in_band:
            self._reset_creep()
            return y

        now = time.perf_counter()
        if self._creep_entered_at is None:
            self._creep_entered_at = now
            self._creep_last_t = now
            return y
        if now - self._creep_entered_at < self.edge_creep_delay_s:
            self._creep_last_t = now
            return y

        dt = now - (self._creep_last_t or now)
        self._creep_last_t = now
        self._creep_offset += self.edge_creep_rate_px_s * dt
        return min(bottom, int(round(y + self._creep_offset)))

    def _reset_creep(self) -> None:
        self._creep_offset = 0.0
        self._creep_entered_at = None
        self._creep_last_t = None

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
        Faz duplo clique manualmente com interval explicito.

        pyautogui.doubleClick() pode disparar muito rapido em alguns
        sistemas — executamos dois cliques separados com delay
        configurable pra garantir que apps (Explorer, Chrome, etc.)
        reconhecam como duplo clique do sistema.
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
        Clique direito do mouse — aciona o menu de contexto.

        _pause=False mantem o tempo zero entre cliques (cooldown e
        tratado pelo GestureDetector).
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

    def press_key(self, key: str) -> None:
        """Press de uma tecla (sem mover o cursor). Aceita qualquer key
        name do PyAutoGUI (e.g. 'right', 'pagedown', 'space')."""
        try:
            pyautogui.press(key, _pause=False)
        except Exception as e:
            logger.warning("Falha em press_key(%s): %s", key, e)

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    @property
    def last_position(self) -> Tuple[Optional[int], Optional[int]]:
        """Ultima posicao em pixel pra qual o cursor foi movido; (None, None)
        antes do primeiro move."""
        return self._last_x, self._last_y

    def force_release(self) -> None:
        if self._dragging:
            try:
                pyautogui.mouseUp(_pause=False)
            except Exception as e:
                logger.debug("force_release mouseUp falhou: %s", e)
            self._dragging = False
