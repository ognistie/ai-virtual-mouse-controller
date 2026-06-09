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


# Cooldown entre presses (anti-bounce + ergonomia).
# 0.12s = 120ms = limite teorico ~8 chars/s de pinch encadeado.
# Anterior 0.18s limitava a ~5.5/s. Cooldown ainda alto o suficiente
# pra evitar bounce duplo do mesmo pinch fisico (~50ms tipico).
PRESS_COOLDOWN_S = 0.12

# Limiar mínimo de hover_score pra aceitar um pinch como press de tecla.
PRESS_HOVER_THRESHOLD = 0.30


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
    ) -> None:
        self.state = state
        self.typer = typer
        self.predictor = predictor
        self.adaptive = adaptive
        self.accessibility = accessibility

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
            return

        fx, fy = self._finger_smoother(*finger_xy_screen)

        # Hover update
        self.hover.update(fx, fy, self.state)

        # Edge detection — pinch_now True após False = press
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

    # ───────────────────────────────────────────────────── press logic

    def _handle_press(self, fx: float, fy: float) -> None:
        now = time.perf_counter()
        if now - self._last_press_t < PRESS_COOLDOWN_S:
            return
        hov = self.state.hovered_code
        if hov is None:
            return
        ks = self.state.keys.get(hov)
        if ks is None or ks.hover_score < PRESS_HOVER_THRESHOLD:
            return
        self._last_press_t = now

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

        # Dispatch
        if key.role == "char":
            self.typer.type_char(char)
            self.predictor.feed_char(char)
        elif key.role == "space":
            self.typer.press_code("space")
            self.predictor.feed_special("space")
        elif key.role == "enter":
            self.typer.press_code("enter")
            self.predictor.feed_special("enter")
        elif key.role == "backspace":
            self.typer.press_code("backspace")
            self.predictor.feed_special("backspace")
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
        """Chamado externamente quando usuário hover+pinch numa pílula."""
        word = self.predictor.accept(idx)
        if word is None:
            return
        # Apaga prefixo digitado e envia palavra completa + espaço
        # (assume que o usuário digitou todo o prefixo; precisa apagar
        # caracteres equivalentes — mas como type_char foi por pyautogui,
        # já está no SO. Heurística simples: send backspaces e depois word.)
        # Para MVP: só envia o restante.
        # TODO refino: tracking de "chars já enviados para esse prefix"
        self.typer.type_word(word + " ")
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
