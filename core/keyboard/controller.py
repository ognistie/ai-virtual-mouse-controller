"""
core.keyboard.controller
========================

KeyboardController — orquestra hover, pinch e dispatch de KeyEvent.

Reusa:
- Gesture do GestureDetector (já existente — press-to-click PINCH).
- OneEuroSmoother2D do projeto.
- BurstManager pro ripple no press (já usado pelo holograma).
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Sequence, Tuple

from ..smoothing import OneEuroSmoother2D
from .accessibility import AccessibilitySettings
from .adaptive import AdaptiveModel
from .hover import HoverDetector
from .models import Key, KeyboardState, KeyEvent
from .output import SystemTyper
from .prediction import TextPredictor

logger = logging.getLogger(__name__)


# Cooldown entre presses (modo pinch — legacy quando dwell desligado).
PRESS_COOLDOWN_S = 0.12

# Limiar mínimo de hover_score pra aceitar press.
PRESS_HOVER_THRESHOLD = 0.18

# Dwell-to-type defaults (override via construtor a partir do config).
DWELL_DURATION_S_DEFAULT = 3.0
DWELL_COOLDOWN_S_DEFAULT = 0.25

# Backspace repeat-on-hold: apos dispatch inicial via dwell, se usuario
# continua sobre BACKSPACE, dispara novamente a cada N segundos.
BACKSPACE_REPEAT_INTERVAL_S = 0.15


class KeyboardController:
    """
    API:
      ctrl.on_frame(landmarks, gesture_is_pinch, finger_xy_screen)
        → atualiza hover; se pinch edge + hover → dispara press.
      ctrl.set_rects(rects, screen_w, screen_h)
      ctrl.state → KeyboardState (somente leitura externa pro renderer)
      ctrl.on_press(callback) — observer pro renderer (ripple anim)
    """

    def __init__(
        self,
        state: KeyboardState,
        typer: SystemTyper,
        predictor: TextPredictor,
        adaptive: AdaptiveModel,
        accessibility: AccessibilitySettings,
        *,
        dwell_enabled: bool = True,
        dwell_duration_s: float = DWELL_DURATION_S_DEFAULT,
        dwell_cooldown_s: float = DWELL_COOLDOWN_S_DEFAULT,
    ) -> None:
        self.state = state
        self.typer = typer
        self.predictor = predictor
        self.adaptive = adaptive
        self.accessibility = accessibility

        # Dwell-to-type config
        self.dwell_enabled = dwell_enabled
        self.dwell_duration_s = max(0.3, dwell_duration_s)
        self.dwell_cooldown_s = max(0.0, dwell_cooldown_s)
        # Estado runtime do dwell
        self._dwell_key: Optional[str] = None
        self._dwell_start_t: float = 0.0

        self.hover = HoverDetector()

        # Fingertip smoothing — re-inicializado se tremor_compensation muda.
        # OneEuro params: min_cutoff alto + beta moderado = responsivo pra
        # navegacao rapida entre teclas. Tremor compensation override via
        # accessibility (params mais agressivos quando ON).
        # Sem tremor (default): (1.5, 0.05) — 25% mais responsivo que cursor
        # padrao (1.2, 0.020), reduzindo lag percebido em hover rapido.
        mc, b = self._smoother_params()
        self._finger_smoother = OneEuroSmoother2D(
            freq=60.0, min_cutoff=mc, beta=b, d_cutoff=1.0
        )

        # Estado pinch: edge detection (raw transition False→True dispara press)
        self._pinch_active = False
        self._last_press_t = 0.0
        self._press_observers: list[Callable[[KeyEvent], None]] = []
        # Contador de chars do prefix atual digitado no SO — usado pra
        # apagar prefix antes de enviar a sugestao aceita (auto-complete real).
        self._prefix_chars_typed = 0

        # Adaptive aplica a cada N frames pra não custar nada
        self._adaptive_apply_counter = 0
        self._half_w_avg = 40.0
        self._half_h_avg = 30.0

    # ───────────────────────────────────────────────────── public

    def set_rects_from(self, rects, screen_w: float, screen_h: float) -> None:
        """Delega ao HoverDetector e armazena meia-dimensão média (p/ adaptive)."""
        self.hover.set_rects(rects, screen_w, screen_h)
        if rects:
            self._half_w_avg = sum(r.half_w for r in rects) / len(rects)
            self._half_h_avg = sum(r.half_h for r in rects) / len(rects)

    def on_press(self, cb: Callable[[KeyEvent], None]) -> None:
        self._press_observers.append(cb)

    def _smoother_params(self) -> Tuple[float, float]:
        """Params do OneEuro pro fingertip. Tremor compensation override."""
        if self.accessibility.tremor_compensation > 0:
            return self.accessibility.tremor_one_euro_params()
        # Modo padrao (sem tremor): tunado pra responsividade em teclado.
        # Mais responsivo que cursor (1.2, 0.020) → reduz lag em hover rapido.
        return (1.5, 0.05)

    def refresh_smoother(self) -> None:
        """Chamar se accessibility.tremor_compensation mudar."""
        mc, b = self._smoother_params()
        self._finger_smoother = OneEuroSmoother2D(
            freq=60.0, min_cutoff=mc, beta=b, d_cutoff=1.0
        )

    def on_frame(
        self,
        finger_xy_screen: Tuple[float, float],
        pinch_now: bool,
    ) -> None:
        if not self.state.visible:
            self._pinch_active = pinch_now
            self.state.cursor_xy = None
            self.state.dwell_progress = 0.0
            self._dwell_key = None
            return

        fx, fy = self._finger_smoother(*finger_xy_screen)

        # Compartilha posicao smoothada com renderer pra desenhar marker
        # visual exatamente onde o hover detecta. Usuario mira no marker.
        self.state.cursor_xy = (fx, fy)

        # Hover update — atualiza state.hovered_code
        self.hover.update(fx, fy, self.state)

        # Modo selecao: dwell-to-type OU pinch edge
        if self.dwell_enabled:
            self._update_dwell(fx, fy)
        else:
            # Legacy pinch mode (mantido como fallback se dwell desabilitado)
            edge = pinch_now and not self._pinch_active
            self._pinch_active = pinch_now
            if edge:
                self._handle_press(fx, fy)

        # Adaptive periódico (a cada ~30 frames = 0.5 s @60 fps)
        self._adaptive_apply_counter += 1
        if self._adaptive_apply_counter >= 30:
            self._adaptive_apply_counter = 0
            self.adaptive.apply_to_state(
                self.state, self._half_w_avg, self._half_h_avg
            )

    # ───────────────────────────────────────────────────── dwell logic

    def _update_dwell(self, fx: float, fy: float) -> None:
        """Dwell-to-type: tecla dispara quando dedo permanece sobre ela
        por self.dwell_duration_s segundos. Sem necessidade de pinca.

        Algoritmo:
          1. Se hovered_code mudou → reseta timer + zera progress.
          2. Se hovered_code is None → progress = 0, sem dispatch.
          3. Se em cooldown (post-press) → progress = 0, sem dispatch.
          4. Senao, calcula progress = elapsed / duration.
          5. Quando progress >= 1.0 → press + reset timer + cooldown.
        """
        now = time.perf_counter()
        hov = self.state.hovered_code

        # Mudou de tecla? Reset.
        if hov != self._dwell_key:
            self._dwell_key = hov
            self._dwell_start_t = now
            self.state.dwell_progress = 0.0
            return

        # Sem hover ou hover_score insuficiente → sem progresso.
        if hov is None:
            self.state.dwell_progress = 0.0
            return
        ks = self.state.keys.get(hov)
        if ks is None or ks.hover_score < PRESS_HOVER_THRESHOLD:
            self.state.dwell_progress = 0.0
            return

        # Repeat-on-hold em BACKSPACE: apos dispatch inicial via dwell,
        # se usuario continua sobre backspace, repete a cada N segundos.
        # Acelera correcao de erros sem precisar sair e voltar.
        if hov == "backspace" and self._last_press_t > 0:
            since_last = now - self._last_press_t
            if since_last >= BACKSPACE_REPEAT_INTERVAL_S:
                self._handle_press(fx, fy)
                self._dwell_start_t = now
                # Indicador visual: progress cheia durante repeat
                self.state.dwell_progress = 1.0
                return
            # Durante o gap de 150ms entre repeats: mostra prog cheia
            self.state.dwell_progress = 1.0
            return

        # Cooldown pos-press (outras teclas): skip ate sair da tecla.
        if now - self._last_press_t < self.dwell_cooldown_s:
            self.state.dwell_progress = 0.0
            return

        elapsed = now - self._dwell_start_t
        progress = elapsed / self.dwell_duration_s
        if progress >= 1.0:
            self.state.dwell_progress = 1.0
            self._handle_press(fx, fy)
            # Reset pra proxima tecla (mesma tecla precisa "sair e voltar")
            self._dwell_start_t = now
            self.state.dwell_progress = 0.0
        else:
            self.state.dwell_progress = max(0.0, min(1.0, progress))

    # ───────────────────────────────────────────────────── press logic

    def _handle_press(self, fx: float, fy: float) -> None:
        now = time.perf_counter()
        hov_preview = self.state.hovered_code
        # Modo pinch usa PRESS_COOLDOWN_S; modo dwell usa dwell_cooldown_s
        # (verificado em _update_dwell antes de chamar). Cooldown abaixo
        # protege ambos os modos contra dispatch duplicado no mesmo frame.
        # Bypass cooldown se backspace + intervalo de repeat ja passou
        # (rota repeat-on-hold ja validou tempo no _update_dwell).
        is_backspace_repeat = (
            hov_preview == "backspace"
            and (now - self._last_press_t) >= BACKSPACE_REPEAT_INTERVAL_S
        )
        if not is_backspace_repeat:
            cooldown = (
                self.dwell_cooldown_s if self.dwell_enabled
                else PRESS_COOLDOWN_S
            )
            if now - self._last_press_t < cooldown:
                logger.debug(
                    "[KB] press skip cooldown (%.3fs since last)",
                    now - self._last_press_t,
                )
                return
        hov = self.state.hovered_code
        if hov is None:
            logger.info(
                "[KB] press SEM hovered_code @ finger=(%.0f,%.0f)", fx, fy,
            )
            return
        ks = self.state.keys.get(hov)
        if ks is None:
            return
        if ks.hover_score < PRESS_HOVER_THRESHOLD:
            logger.info(
                "[KB] press REJECTED key=%s hover=%.2f < %.2f",
                hov, ks.hover_score, PRESS_HOVER_THRESHOLD,
            )
            return
        self._last_press_t = now
        mode = "dwell" if self.dwell_enabled else "pinch"
        logger.info(
            "[KB] PRESS(%s) key=%s hover=%.2f finger=(%.0f,%.0f)",
            mode, hov, ks.hover_score, fx, fy,
        )

        key = ks.key
        # Resolve caractere final considerando modificadores
        char, code_final = self._resolve_char(key)

        # Emite KeyEvent (renderer pode escutar pra ripple)
        event = KeyEvent(
            code=code_final,
            char=char,
            timestamp=now,
            confidence=ks.hover_score,
            x=fx,
            y=fy,
        )
        ks.pressed_t = now
        ks.ripple_t = now
        ks.hit_count += 1

        # Modificadores: toggle (caps), one-shot (shift/altgr/ctrl/alt)
        if key.role == "modifier":
            self._toggle_modifier(key.code)
            self._notify(event)
            return

        # Dispatch + tracking de prefix chars (pra auto-complete real)
        if key.role == "char":
            self.typer.type_char(char)
            self.predictor.feed_char(char)
            if char.isalpha():
                self._prefix_chars_typed += 1
            else:
                self._prefix_chars_typed = 0
        elif key.role == "space":
            self.typer.press_code("space")
            self.predictor.feed_special("space")
            self._prefix_chars_typed = 0
        elif key.role == "enter":
            self.typer.press_code("enter")
            self.predictor.feed_special("enter")
            self._prefix_chars_typed = 0
        elif key.role == "backspace":
            self.typer.press_code("backspace")
            self.predictor.feed_special("backspace")
            self._prefix_chars_typed = max(0, self._prefix_chars_typed - 1)
        elif key.role == "system":
            self.typer.press_code(key.code)
        elif key.role == "suggestion":
            # Suggestion clicks são dispatched por outro caminho
            pass

        # Adaptive — registra press (post-dispatch pra timestamp consistente)
        self.adaptive.record_press(event, (fx, fy), self.state)

        # Atualiza sugestões na state pra renderer atualizar
        self.state.suggestions = self.predictor.suggestions()

        # One-shot modifiers consumidos
        if not self.state.caps_on:
            self.state.shift_on = False
        self.state.altgr_on = False
        self.state.ctrl_on = False
        self.state.alt_on = False

        # Typing speed estimate (EMA)
        if self.state.last_keypress_t > 0:
            dt = now - self.state.last_keypress_t
            if dt > 0:
                cps = 1.0 / dt
                self.state.typing_speed_cps = (
                    0.2 * cps + 0.8 * self.state.typing_speed_cps
                )
        self.state.last_keypress_t = now

        self._notify(event)

    def accept_suggestion(self, idx: int) -> None:
        """Aceita sugestao #idx: apaga prefix digitado, envia palavra
        completa + espaco automatico. Reduz tempo total de digitacao
        (1 dwell substitui escrever palavra letra a letra)."""
        word = self.predictor.accept(idx)
        if word is None:
            return
        # Apaga chars do prefix ja enviados ao SO (rastreados em
        # _prefix_chars_typed). Envia palavra completa + espaco.
        for _ in range(self._prefix_chars_typed):
            self.typer.press_code("backspace")
        self.typer.type_word(word + " ")
        self._prefix_chars_typed = 0
        self.state.suggestions = self.predictor.suggestions()

    # ───────────────────────────────────────────────────── modifiers

    def _toggle_modifier(self, code: str) -> None:
        s = self.state
        if code in ("shift", "shift_r"):
            s.shift_on = not s.shift_on
        elif code == "caps":
            s.caps_on = not s.caps_on
        elif code == "altgr":
            s.altgr_on = not s.altgr_on
        elif code in ("ctrl", "ctrl_r"):
            s.ctrl_on = not s.ctrl_on
        elif code in ("alt", "alt_r"):
            s.alt_on = not s.alt_on

    def _resolve_char(self, key: Key) -> Tuple[str, str]:
        """Retorna (char_a_digitar, code_final_para_event)."""
        if key.role != "char":
            return ("", key.code)
        if self.state.altgr_on and key.label_altgr:
            return (key.label_altgr, key.code)
        if (self.state.shift_on ^ self.state.caps_on) and key.label_shift:
            return (key.label_shift, key.code)
        return (key.label, key.code)

    def _notify(self, event: KeyEvent) -> None:
        for cb in self._press_observers:
            try:
                cb(event)
            except Exception as e:
                logger.debug("press observer falhou: %s", e)
