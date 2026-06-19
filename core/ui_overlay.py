"""
core.ui_overlay
===============

UI moderna sobre o frame da camera com OpenCV.

Componentes:
- TopBar: indicadores minimalistas (FPS, shape, badges)
- SettingsPanel: painel lateral com sliders, botoes e DOC de valores
- HintBar: dicas de teclado no rodape
- DocSection: tabela "Recommended vs Current" para nao perder os defaults

Interacao:
- Teclado (S abre/fecha, setas navegam, etc.)
- Mouse fisico (clique e arrasta nos sliders)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2


# ===================================================================
# DESIGN TOKENS
# ===================================================================

COLOR_BG_PANEL = (24, 24, 24)
COLOR_BG_PANEL_ALPHA = 0.92
COLOR_BG_DOC = (32, 32, 32)
COLOR_BORDER = (60, 60, 60)
COLOR_BORDER_LIGHT = (80, 80, 80)
COLOR_TEXT_PRIMARY = (240, 240, 240)
COLOR_TEXT_SECONDARY = (170, 170, 170)
COLOR_TEXT_TERTIARY = (110, 110, 110)
COLOR_TEXT_HINT = (90, 90, 90)
COLOR_TEXT_DIFF = (39, 191, 251)        # amarelo (valor diferente do recommended)
COLOR_TEXT_SAME = (110, 110, 110)        # cinza (valor igual)

# Acentos (BGR)
COLOR_AMBER = (39, 191, 251)
COLOR_GREEN = (140, 239, 134)
COLOR_PURPLE = (252, 180, 192)
COLOR_CYAN = (220, 200, 100)
COLOR_PINK = (180, 140, 240)
COLOR_ORANGE = (80, 130, 240)

# Badges
COLOR_BADGE_PRECISION_BG = (40, 70, 40)
COLOR_BADGE_PRECISION_FG = (140, 239, 134)
COLOR_BADGE_STICKY_BG = (40, 50, 80)
COLOR_BADGE_STICKY_FG = (110, 200, 250)
COLOR_BADGE_FROZEN_BG = (60, 40, 70)
COLOR_BADGE_FROZEN_FG = (200, 150, 240)
COLOR_BADGE_DRAG_BG = (30, 60, 90)
COLOR_BADGE_DRAG_FG = (130, 200, 250)

# Botoes
COLOR_BTN_NORMAL_FG = (160, 160, 160)
COLOR_BTN_NORMAL_BORDER = (60, 60, 60)
COLOR_BTN_ACTIVE_BG = (40, 40, 40)
COLOR_BTN_ACTIVE_FG = (240, 240, 240)
COLOR_BTN_HOVER_BG = (40, 40, 40)

# Layout
PANEL_WIDTH = 280
PANEL_PADDING = 10
PANEL_RIGHT_MARGIN = 10
PANEL_TOP_MARGIN = 8
SLIDER_HEIGHT = 5
SLIDER_THUMB_RADIUS = 6
SLIDER_BLOCK_HEIGHT = 32
BUTTON_HEIGHT = 21
BUTTON_GAP = 3
SECTION_GAP = 6
DOC_ROW_HEIGHT = 12

# Typography
FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SIZE_LABEL = 0.40
FONT_SIZE_VALUE = 0.40
FONT_SIZE_HINT = 0.35
FONT_SIZE_DOC = 0.36
FONT_SIZE_TITLE = 0.42
FONT_SIZE_BADGE = 0.36
FONT_SIZE_TOPBAR = 0.45
FONT_SIZE_BUTTON = 0.38
FONT_THICKNESS = 1


# ===================================================================
# DATA STRUCTURES
# ===================================================================

@dataclass
class SliderSpec:
    key: str
    label: str
    value: float
    color: Tuple[int, int, int]
    min_label: str = "off"
    max_label: str = "max"
    display_value: str = ""
    _bbox: Optional[Tuple[int, int, int, int]] = None
    _focused: bool = False


@dataclass
class ButtonSpec:
    key: str
    label: str
    active: bool = False
    color: Tuple[int, int, int] = (39, 191, 251)
    _bbox: Optional[Tuple[int, int, int, int]] = None
    _hover: bool = False


@dataclass
class DocRow:
    """Linha da tabela 'Recommended vs Current'."""
    label: str
    recommended: str
    current: str

    @property
    def is_different(self) -> bool:
        return self.recommended != self.current


@dataclass
class UIState:
    panel_visible: bool = False
    sliders: List[SliderSpec] = field(default_factory=list)
    focused_slider_index: int = 0
    profile_buttons: List[ButtonSpec] = field(default_factory=list)
    aim_toggle: Optional[ButtonSpec] = None
    sticky_toggle: Optional[ButtonSpec] = None
    presentation_toggle: Optional[ButtonSpec] = None
    reset_button: Optional[ButtonSpec] = None
    apply_recommended_button: Optional[ButtonSpec] = None

    def action_buttons(self) -> List[ButtonSpec]:
        """Ordem canonica dos botoes de acao — fonte unica pra render,
        hover e hit testing."""
        return [
            b for b in (
                self.aim_toggle,
                self.sticky_toggle,
                self.presentation_toggle,
                self.reset_button,
                self.apply_recommended_button,
            ) if b is not None
        ]

    def toggle_buttons(self) -> List[ButtonSpec]:
        """Subconjunto de action_buttons que sao toggles."""
        return [
            b for b in (
                self.aim_toggle,
                self.sticky_toggle,
                self.presentation_toggle,
            ) if b is not None
        ]
    doc_rows: List[DocRow] = field(default_factory=list)
    show_doc: bool = False
    dragging_slider_key: Optional[str] = None
    mouse_x: int = 0
    mouse_y: int = 0


# ===================================================================
# CALLBACKS
# ===================================================================

class UICallbacks:
    def on_slider_change(self, key: str, value: float) -> None:
        pass

    def on_profile_change(self, profile: str) -> None:
        pass

    def on_toggle(self, key: str, value: bool) -> None:
        pass

    def on_reset(self) -> None:
        pass

    def on_apply_recommended(self) -> None:
        pass


# ===================================================================
# RENDERING HELPERS
# ===================================================================

def _draw_translucent_rect(frame, pt1, pt2, color, alpha):
    overlay = frame.copy()
    cv2.rectangle(overlay, pt1, pt2, color, -1)
    cv2.addWeighted(overlay, alpha, frame, 1.0 - alpha, 0, frame)


def _draw_filled_rounded_rect(frame, pt1, pt2, color, radius=4):
    x1, y1 = pt1
    x2, y2 = pt2
    if x2 - x1 < radius * 2 or y2 - y1 < radius * 2:
        cv2.rectangle(frame, pt1, pt2, color, -1)
        return
    cv2.rectangle(frame, (x1 + radius, y1), (x2 - radius, y2), color, -1)
    cv2.rectangle(frame, (x1, y1 + radius), (x2, y2 - radius), color, -1)
    cv2.ellipse(frame, (x1 + radius, y1 + radius), (radius, radius), 180, 0, 90, color, -1)
    cv2.ellipse(frame, (x2 - radius, y1 + radius), (radius, radius), 270, 0, 90, color, -1)
    cv2.ellipse(frame, (x1 + radius, y2 - radius), (radius, radius), 90, 0, 90, color, -1)
    cv2.ellipse(frame, (x2 - radius, y2 - radius), (radius, radius), 0, 0, 90, color, -1)


def _put_text(frame, text, pos, color, size=FONT_SIZE_LABEL, thickness=FONT_THICKNESS):
    cv2.putText(frame, text, pos, FONT, size, color, thickness, cv2.LINE_AA)


def _text_size(text, size=FONT_SIZE_LABEL, thickness=FONT_THICKNESS):
    (w, h), _ = cv2.getTextSize(text, FONT, size, thickness)
    return w, h


# ===================================================================
# COMPONENTES
# ===================================================================

class TopBar:
    @staticmethod
    def render(frame, fps, shape_name, dpi, is_dragging=False,
               aim_assist_active=False, sticky_active=False, anchor_frozen=False):
        h, w = frame.shape[:2]
        bar_h = 38
        _draw_translucent_rect(frame, (0, 0), (w, bar_h), (15, 15, 15), 0.65)

        x = 14
        y_text = 24
        _put_text(frame, f"FPS {fps:.0f}", (x, y_text),
                  (134, 239, 140), FONT_SIZE_TOPBAR)
        x += _text_size(f"FPS {fps:.0f}", FONT_SIZE_TOPBAR)[0] + 18

        _put_text(frame, f"SHAPE {shape_name.upper()}", (x, y_text),
                  (252, 180, 192), FONT_SIZE_TOPBAR)
        x += _text_size(f"SHAPE {shape_name.upper()}", FONT_SIZE_TOPBAR)[0] + 18

        _put_text(frame, f"DPI {dpi:.2f}x", (x, y_text),
                  (39, 191, 251), FONT_SIZE_TOPBAR)

        # Badges
        badges = []
        if is_dragging:
            badges.append(("DRAG", COLOR_BADGE_DRAG_BG, COLOR_BADGE_DRAG_FG))
        if aim_assist_active:
            badges.append(("PRECISION", COLOR_BADGE_PRECISION_BG, COLOR_BADGE_PRECISION_FG))
        if sticky_active:
            badges.append(("STICKY", COLOR_BADGE_STICKY_BG, COLOR_BADGE_STICKY_FG))
        if anchor_frozen:
            badges.append(("FROZEN", COLOR_BADGE_FROZEN_BG, COLOR_BADGE_FROZEN_FG))

        bx = w - 12
        for label, bg, fg in reversed(badges):
            tw, th = _text_size(label, FONT_SIZE_BADGE)
            badge_w = tw + 16
            badge_h = 18
            badge_x1 = bx - badge_w
            badge_y1 = (bar_h - badge_h) // 2
            _draw_filled_rounded_rect(frame, (badge_x1, badge_y1),
                                       (bx, badge_y1 + badge_h), bg, radius=3)
            _put_text(frame, label, (badge_x1 + 8, badge_y1 + badge_h - 5),
                      fg, FONT_SIZE_BADGE)
            bx = badge_x1 - 6


class HintBar:
    @staticmethod
    def render(frame, panel_visible):
        h, w = frame.shape[:2]
        if panel_visible:
            hints = "ESC quit  *  arrows nav  *  +/- adjust  *  1-4 profile  *  R reset  *  D doc  *  S close"
        else:
            hints = "ESC quit  *  Open=move  *  Pinch=click  *  Pinch 2s=drag  *  Peace=2click  *  S settings"

        bar_h = 22
        _draw_translucent_rect(frame, (0, h - bar_h), (w, h), (15, 15, 15), 0.55)
        _put_text(frame, hints, (12, h - 7), COLOR_TEXT_HINT, FONT_SIZE_HINT)


class Slider:
    @staticmethod
    def render(frame, spec, x, y, width):
        # Linha 1: Label e Valor
        label_color = COLOR_TEXT_PRIMARY if spec._focused else COLOR_TEXT_SECONDARY
        _put_text(frame, spec.label, (x, y + 10), label_color, FONT_SIZE_LABEL)

        value_text = spec.display_value or f"{spec.value:.2f}"
        tw, _ = _text_size(value_text, FONT_SIZE_VALUE)
        _put_text(frame, value_text, (x + width - tw, y + 10),
                  spec.color, FONT_SIZE_VALUE)

        # Track
        track_y = y + 22
        cv2.rectangle(frame, (x, track_y), (x + width, track_y + SLIDER_HEIGHT),
                      (50, 50, 50), -1)

        # Fill
        fill_w = int(width * spec.value)
        if fill_w > 0:
            cv2.rectangle(frame, (x, track_y), (x + fill_w, track_y + SLIDER_HEIGHT),
                          spec.color, -1)

        # Thumb
        thumb_x = x + fill_w
        thumb_y = track_y + SLIDER_HEIGHT // 2
        cv2.circle(frame, (thumb_x, thumb_y), SLIDER_THUMB_RADIUS, spec.color, -1)
        cv2.circle(frame, (thumb_x, thumb_y), SLIDER_THUMB_RADIUS, (24, 24, 24), 2)

        if spec._focused:
            cv2.circle(frame, (thumb_x, thumb_y), SLIDER_THUMB_RADIUS + 3,
                       COLOR_TEXT_PRIMARY, 1)

        spec._bbox = (x, track_y - 8, width, SLIDER_HEIGHT + 16)


class Button:
    @staticmethod
    def render(frame, spec, x, y, width, height=BUTTON_HEIGHT):
        if spec.active:
            cv2.rectangle(frame, (x, y), (x + width, y + height),
                          COLOR_BTN_ACTIVE_BG, -1)
        elif spec._hover:
            cv2.rectangle(frame, (x, y), (x + width, y + height),
                          COLOR_BTN_HOVER_BG, -1)

        border = spec.color if spec.active else COLOR_BTN_NORMAL_BORDER
        cv2.rectangle(frame, (x, y), (x + width, y + height), border, 1)

        fg = COLOR_BTN_ACTIVE_FG if spec.active else COLOR_BTN_NORMAL_FG
        tw, th = _text_size(spec.label, FONT_SIZE_BUTTON)
        text_x = x + (width - tw) // 2
        text_y = y + (height + th) // 2 - 2
        _put_text(frame, spec.label, (text_x, text_y), fg, FONT_SIZE_BUTTON)

        spec._bbox = (x, y, width, height)


class DocSection:
    """Tabela 'Recommended vs Current' — para nao perder os defaults."""

    @staticmethod
    def render(frame, doc_rows: List[DocRow], x: int, y: int, width: int) -> int:
        """Desenha a tabela e retorna a altura usada."""
        # Fundo da seccao
        n_rows = len(doc_rows)
        section_h = 18 + n_rows * DOC_ROW_HEIGHT + 8

        _draw_filled_rounded_rect(frame, (x, y),
                                   (x + width, y + section_h),
                                   COLOR_BG_DOC, radius=4)

        # Header
        _put_text(frame, "RECOMMENDED", (x + 8, y + 12),
                  COLOR_TEXT_TERTIARY, FONT_SIZE_HINT)
        cur_text = "CURRENT"
        cw, _ = _text_size(cur_text, FONT_SIZE_HINT)
        _put_text(frame, cur_text, (x + width - cw - 8, y + 12),
                  COLOR_TEXT_TERTIARY, FONT_SIZE_HINT)

        # Linha separadora
        cv2.line(frame, (x + 8, y + 17), (x + width - 8, y + 17),
                 (50, 50, 50), 1)

        # Linhas
        cy = y + 18 + 10
        for row in doc_rows:
            # Label
            _put_text(frame, row.label, (x + 8, cy),
                      COLOR_TEXT_SECONDARY, FONT_SIZE_DOC)

            # Recommended (cinza, meio)
            label_w, _ = _text_size(row.label, FONT_SIZE_DOC)
            rec_x = x + 8 + label_w + 8
            _put_text(frame, row.recommended, (rec_x, cy),
                      COLOR_TEXT_TERTIARY, FONT_SIZE_DOC)

            # Setinha
            arrow_x = rec_x + _text_size(row.recommended, FONT_SIZE_DOC)[0] + 4
            _put_text(frame, ">", (arrow_x, cy),
                      COLOR_TEXT_TERTIARY, FONT_SIZE_DOC)

            # Current (amarelo se diferente, cinza se igual)
            cur_color = COLOR_TEXT_DIFF if row.is_different else COLOR_TEXT_SAME
            cw, _ = _text_size(row.current, FONT_SIZE_DOC)
            _put_text(frame, row.current, (x + width - cw - 8, cy),
                      cur_color, FONT_SIZE_DOC)

            cy += DOC_ROW_HEIGHT

        return section_h


class SettingsPanel:
    @staticmethod
    def render(frame, state: UIState, active_profile: str):
        h, w = frame.shape[:2]

        # Calcula altura dinamica
        n_sliders = len(state.sliders)
        sliders_h = n_sliders * SLIDER_BLOCK_HEIGHT

        profile_block_h = (BUTTON_HEIGHT * 2) + BUTTON_GAP

        toggles_h = len(state.action_buttons()) * (BUTTON_HEIGHT + BUTTON_GAP)

        header_h = 20
        section_label_h = 14

        doc_h = 0
        if state.show_doc and state.doc_rows:
            doc_h = 18 + len(state.doc_rows) * DOC_ROW_HEIGHT + 8 + SECTION_GAP

        total_h = (
            header_h
            + sliders_h
            + SECTION_GAP
            + section_label_h
            + profile_block_h
            + SECTION_GAP
            + toggles_h
            + doc_h
            + PANEL_PADDING * 2
        )

        # Posicao
        panel_x1 = w - PANEL_WIDTH - PANEL_RIGHT_MARGIN
        panel_y1 = PANEL_TOP_MARGIN + 38
        panel_x2 = panel_x1 + PANEL_WIDTH

        # Limita altura ao frame disponivel
        max_h = h - panel_y1 - 30  # deixa espaco pra hint bar
        actual_h = min(total_h, max_h)
        panel_y2 = panel_y1 + actual_h

        # Fundo
        _draw_translucent_rect(frame, (panel_x1, panel_y1),
                               (panel_x2, panel_y2),
                               COLOR_BG_PANEL, COLOR_BG_PANEL_ALPHA)
        cv2.rectangle(frame, (panel_x1, panel_y1),
                      (panel_x2, panel_y2), COLOR_BORDER, 1)

        content_x = panel_x1 + PANEL_PADDING
        content_y = panel_y1 + PANEL_PADDING
        content_w = PANEL_WIDTH - PANEL_PADDING * 2

        # Header
        _put_text(frame, "SETTINGS", (content_x, content_y + 12),
                  COLOR_TEXT_PRIMARY, FONT_SIZE_TITLE)
        hint = "S close"
        hw, _ = _text_size(hint, FONT_SIZE_HINT)
        _put_text(frame, hint, (content_x + content_w - hw, content_y + 12),
                  COLOR_TEXT_TERTIARY, FONT_SIZE_HINT)

        cy = content_y + header_h

        # Sliders
        for i, slider in enumerate(state.sliders):
            slider._focused = (i == state.focused_slider_index)
            Slider.render(frame, slider, content_x, cy, content_w)
            cy += SLIDER_BLOCK_HEIGHT

        cy += SECTION_GAP

        # PROFILE label
        _put_text(frame, "PROFILE", (content_x, cy + 8),
                  COLOR_TEXT_TERTIARY, FONT_SIZE_HINT)
        cy += section_label_h

        # 2x2 grid
        col_w = (content_w - BUTTON_GAP) // 2
        for i, btn in enumerate(state.profile_buttons):
            btn.active = (btn.key == active_profile)
            row = i // 2
            col = i % 2
            bx = content_x + col * (col_w + BUTTON_GAP)
            by = cy + row * (BUTTON_HEIGHT + BUTTON_GAP)
            Button.render(frame, btn, bx, by, col_w)
        cy += profile_block_h + SECTION_GAP

        # Toggles + reset + apply (ordem canonica em state.action_buttons)
        for btn in state.action_buttons():
            Button.render(frame, btn, content_x, cy, content_w)
            cy += BUTTON_HEIGHT + BUTTON_GAP

        cy += SECTION_GAP - BUTTON_GAP

        # Doc section
        if state.show_doc and state.doc_rows:
            DocSection.render(frame, state.doc_rows, content_x, cy, content_w)


# ===================================================================
# MAIN OVERLAY MANAGER
# ===================================================================

class UIOverlay:
    def __init__(self, callbacks: UICallbacks):
        self.callbacks = callbacks
        self.state = UIState()
        self._current_profile = "smooth"
        self._init_components()

    def _init_components(self):
        self.state.sliders = [
            SliderSpec(key="sensitivity", label="Sensitivity",
                       value=0.5, color=COLOR_AMBER,
                       min_label="slow", max_label="fast"),
            SliderSpec(key="aim_assist", label="Aim assist",
                       value=0.6, color=COLOR_GREEN,
                       min_label="off", max_label="strong"),
            SliderSpec(key="smoothness", label="Smoothness",
                       value=0.7, color=COLOR_PURPLE,
                       min_label="raw", max_label="smooth"),
            SliderSpec(key="pinch", label="Pinch sensitivity",
                       value=0.5, color=COLOR_CYAN,
                       min_label="strict", max_label="loose"),
            SliderSpec(key="sticky", label="Sticky strength",
                       value=0.4, color=COLOR_PINK,
                       min_label="off", max_label="strong"),
            SliderSpec(key="anchor_freeze", label="Anchor freeze",
                       value=0.3, color=COLOR_ORANGE,
                       min_label="off", max_label="long"),
        ]

        self.state.profile_buttons = [
            ButtonSpec(key="smooth", label="smooth", color=COLOR_AMBER),
            ButtonSpec(key="precise", label="precise", color=COLOR_AMBER),
            ButtonSpec(key="responsive", label="snappy", color=COLOR_AMBER),
            ButtonSpec(key="stable", label="stable", color=COLOR_AMBER),
        ]

        self.state.aim_toggle = ButtonSpec(
            key="aim_toggle", label="Aim assist: ON", active=True,
            color=COLOR_GREEN,
        )
        self.state.sticky_toggle = ButtonSpec(
            key="sticky_toggle", label="Sticky: ON", active=True,
            color=COLOR_PINK,
        )
        self.state.presentation_toggle = ButtonSpec(
            key="presentation_toggle", label="Apresentacao: OFF",
            active=False, color=COLOR_AMBER,
        )
        self.state.reset_button = ButtonSpec(
            key="reset", label="Reset profile",
            color=COLOR_TEXT_TERTIARY,
        )
        self.state.apply_recommended_button = ButtonSpec(
            key="apply_recommended", label="Apply recommended",
            color=COLOR_AMBER,
        )

    # API publica

    def set_panel_visible(self, visible: bool):
        self.state.panel_visible = visible

    def toggle_doc(self):
        self.state.show_doc = not self.state.show_doc

    def set_slider_value(self, key: str, value: float):
        for s in self.state.sliders:
            if s.key == key:
                s.value = max(0.0, min(1.0, value))

    def set_slider_display(self, key: str, display: str):
        for s in self.state.sliders:
            if s.key == key:
                s.display_value = display

    def set_aim_active(self, active: bool):
        if self.state.aim_toggle:
            self.state.aim_toggle.active = active
            self.state.aim_toggle.label = f"Aim assist: {'ON' if active else 'OFF'}"

    def set_sticky_active(self, active: bool):
        if self.state.sticky_toggle:
            self.state.sticky_toggle.active = active
            self.state.sticky_toggle.label = f"Sticky: {'ON' if active else 'OFF'}"

    def set_presentation_active(self, active: bool):
        if self.state.presentation_toggle:
            self.state.presentation_toggle.active = active
            self.state.presentation_toggle.label = (
                f"Apresentacao: {'ON' if active else 'OFF'}"
            )

    def set_profile(self, profile: str):
        self._current_profile = profile

    def set_doc_rows(self, rows: List[DocRow]):
        self.state.doc_rows = rows

    # Render principal

    def render(self, frame, fps, shape_name, dpi,
               is_dragging=False, aim_assist_active=False,
               sticky_active=False, anchor_frozen=False):
        TopBar.render(frame, fps, shape_name, dpi,
                      is_dragging, aim_assist_active,
                      sticky_active, anchor_frozen)

        if self.state.panel_visible:
            SettingsPanel.render(frame, self.state, self._current_profile)

        HintBar.render(frame, self.state.panel_visible)

    # Keyboard

    def handle_key(self, key: int) -> bool:
        if key == ord('s') or key == ord('S'):
            self.state.panel_visible = not self.state.panel_visible
            return True

        if not self.state.panel_visible:
            return False

        if key == ord('1'):
            self._set_profile_via_button(0); return True
        if key == ord('2'):
            self._set_profile_via_button(1); return True
        if key == ord('3'):
            self._set_profile_via_button(2); return True
        if key == ord('4'):
            self._set_profile_via_button(3); return True

        if key == ord('r') or key == ord('R'):
            self.callbacks.on_reset(); return True

        if key == ord('d') or key == ord('D'):
            self.toggle_doc(); return True

        if key == ord('a') or key == ord('A'):
            new_state = not (self.state.aim_toggle.active
                             if self.state.aim_toggle else True)
            self.set_aim_active(new_state)
            self.callbacks.on_toggle("aim", new_state)
            return True

        if key == ord('k') or key == ord('K'):
            new_state = not (self.state.sticky_toggle.active
                             if self.state.sticky_toggle else True)
            self.set_sticky_active(new_state)
            self.callbacks.on_toggle("sticky", new_state)
            return True

        # Setas (Linux/Windows)
        if key in (82, 2490368, ord('w'), ord('W')):
            self._move_focus(-1); return True
        if key in (84, 2621440):
            self._move_focus(1); return True
        if key in (81, 2424832, ord('-')):
            self._adjust_focused_slider(-0.05); return True
        if key in (83, 2555904, ord('+'), ord('=')):
            self._adjust_focused_slider(0.05); return True

        return False

    def _move_focus(self, delta):
        n = len(self.state.sliders)
        if n == 0:
            return
        self.state.focused_slider_index = (
            (self.state.focused_slider_index + delta) % n
        )

    def _adjust_focused_slider(self, delta):
        if not self.state.sliders:
            return
        slider = self.state.sliders[self.state.focused_slider_index]
        new_val = max(0.0, min(1.0, slider.value + delta))
        slider.value = new_val
        self.callbacks.on_slider_change(slider.key, new_val)

    def _set_profile_via_button(self, index):
        if 0 <= index < len(self.state.profile_buttons):
            profile = self.state.profile_buttons[index].key
            self._current_profile = profile
            self.callbacks.on_profile_change(profile)

    # Mouse

    def handle_mouse(self, event: int, x: int, y: int) -> bool:
        self.state.mouse_x = x
        self.state.mouse_y = y

        if event == cv2.EVENT_MOUSEMOVE and self.state.dragging_slider_key:
            slider = self._find_slider(self.state.dragging_slider_key)
            if slider and slider._bbox:
                bx, by, bw, bh = slider._bbox
                rel = (x - bx) / max(1, bw)
                new_val = max(0.0, min(1.0, rel))
                slider.value = new_val
                self.callbacks.on_slider_change(slider.key, new_val)
            return True

        if event == cv2.EVENT_LBUTTONUP:
            if self.state.dragging_slider_key:
                self.state.dragging_slider_key = None
                return True

        if event == cv2.EVENT_MOUSEMOVE and self.state.panel_visible:
            self._update_hovers(x, y)

        if event == cv2.EVENT_LBUTTONDOWN and self.state.panel_visible:
            for i, slider in enumerate(self.state.sliders):
                if slider._bbox and self._hit(slider._bbox, x, y):
                    bx, by, bw, bh = slider._bbox
                    rel = (x - bx) / max(1, bw)
                    new_val = max(0.0, min(1.0, rel))
                    slider.value = new_val
                    self.state.focused_slider_index = i
                    self.state.dragging_slider_key = slider.key
                    self.callbacks.on_slider_change(slider.key, new_val)
                    return True

            for btn in self.state.profile_buttons:
                if btn._bbox and self._hit(btn._bbox, x, y):
                    self._current_profile = btn.key
                    self.callbacks.on_profile_change(btn.key)
                    return True

            for btn in self.state.toggle_buttons():
                if btn._bbox and self._hit(btn._bbox, x, y):
                    new_state = not btn.active
                    btn.active = new_state
                    if btn.key == "aim_toggle":
                        self.set_aim_active(new_state)
                        self.callbacks.on_toggle("aim", new_state)
                    elif btn.key == "sticky_toggle":
                        self.set_sticky_active(new_state)
                        self.callbacks.on_toggle("sticky", new_state)
                    elif btn.key == "presentation_toggle":
                        self.set_presentation_active(new_state)
                        self.callbacks.on_toggle("presentation", new_state)
                    return True

            if self.state.reset_button and self.state.reset_button._bbox:
                if self._hit(self.state.reset_button._bbox, x, y):
                    self.callbacks.on_reset()
                    return True

            if self.state.apply_recommended_button and self.state.apply_recommended_button._bbox:
                if self._hit(self.state.apply_recommended_button._bbox, x, y):
                    self.callbacks.on_apply_recommended()
                    return True

        return False

    def _hit(self, bbox, x, y):
        bx, by, bw, bh = bbox
        return bx <= x <= bx + bw and by <= y <= by + bh

    def _find_slider(self, key):
        for s in self.state.sliders:
            if s.key == key:
                return s
        return None

    def _update_hovers(self, x, y):
        for btn in self.state.profile_buttons:
            if btn._bbox:
                btn._hover = self._hit(btn._bbox, x, y)
        for btn in self.state.action_buttons():
            if btn._bbox:
                btn._hover = self._hit(btn._bbox, x, y)