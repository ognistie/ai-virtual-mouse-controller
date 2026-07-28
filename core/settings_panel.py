"""
core.settings_panel
====================

Painel de ajustes nativo em PySide6 — visual clean/moderno acessivel por
um handle lateral discreto na borda da tela.

Por que Qt e nao OpenCV:
- O painel legado (core.ui_overlay) e' desenhado dentro da janela de
  preview via OpenCV: sem blur, sem tipografia decente, sem animacao.
- PySide6 ja e' dependencia (holograma). Reaproveitamos a MESMA
  QApplication e o mesmo padrao de pump manual — zero custo novo.

Design (igual ao holograma):
- EXPERIENCIA SECUNDARIA. Se PySide6 falhar, `available` fica False e o
  projeto roda normal (a tecla S + painel OpenCV seguem como fallback).
- Nada na arquitetura base depende deste modulo.

API publica:
    SettingsPanel(callbacks=..., width=320, side="right")
    .available
    .bind_callbacks(cb)
    .sync(sliders, displays, aim, sticky, profile)
    .pump()          # chamado a cada tick (drena eventos Qt)
    .toggle()        # abre/fecha o painel
    .close()

`callbacks` segue o contrato de core.ui_overlay.UICallbacks:
    on_slider_change(key, value)   value 0-1
    on_profile_change(profile)
    on_toggle(key, value)          key in {"aim", "sticky"}
    on_reset()
    on_apply_recommended()
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, cast

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────── PySide6 (soft import)

try:
    from PySide6.QtCore import (
        QEasingCurve, QPropertyAnimation, QRect, Qt,
    )
    from PySide6.QtGui import QColor, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
        QVBoxLayout, QWidget,
    )
    _PYSIDE_OK = True
    _IMPORT_ERROR: Optional[str] = None
except ImportError as e:  # pragma: no cover
    _PYSIDE_OK = False
    _IMPORT_ERROR = str(e)


# ─────────────────────────────────────────────── conteudo (fonte unica)

# (chave do slider no RuntimeSettings, rotulo exibido)
_SLIDERS = [
    ("sensitivity", "Sensibilidade"),
    ("aim_assist", "Assistência de mira"),
    ("smoothness", "Suavização"),
    ("pinch", "Pinça"),
    ("sticky", "Aderência"),
    ("anchor_freeze", "Congelar âncora"),
]

# (chave do profile, rotulo)
_PROFILES = [
    ("smooth", "Suave"),
    ("precise", "Preciso"),
    ("responsive", "Ágil"),
    ("stable", "Estável"),
]

# Paleta monocromatica — preto/branco, sem cor de destaque. A enfase
# vem de contraste e peso, nao de matiz (visual mais calmo e limpo).
_BG = "#161616"
_ELEV = "#242424"
_INK = "#f7f7f7"
_INK2 = "#cfcfcf"
_MUTE = "#8d8d8d"
_ACCENT = "#f7f7f7"
_KNOB_DARK = "#161616"
_LINE = "rgba(255, 255, 255, 0.07)"
_LINE2 = "rgba(255, 255, 255, 0.14)"


if _PYSIDE_OK:

    class _Switch(QWidget):
        """Toggle estilo iOS — pinta um trilho + knob que anima ao clicar."""

        def __init__(self, on_change) -> None:
            super().__init__()
            self._on = False
            self._on_change = on_change
            self.setFixedSize(40, 22)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        def is_on(self) -> bool:
            return self._on

        def set_on(self, value: bool, *, notify: bool = False) -> None:
            value = bool(value)
            if value == self._on:
                return
            self._on = value
            self.update()
            if notify:
                self._on_change(value)

        def mousePressEvent(self, event) -> None:
            self.set_on(not self._on, notify=True)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            # ON: trilho branco + knob escuro. OFF: trilho cinza + knob
            # claro. Contraste carrega o estado sem depender de cor.
            track = QColor(_INK) if self._on else QColor(64, 64, 64)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(0, 0, 40, 22, 11, 11)
            knob_x = 20 if self._on else 2
            p.setBrush(QColor(_KNOB_DARK) if self._on else QColor(_INK))
            p.drawEllipse(knob_x, 2, 18, 18)

    class _PanelWindow(QWidget):
        """Janela deslizante com sliders, perfis, toggles e acoes."""

        def __init__(self, owner: "SettingsPanel", width: int, side: str) -> None:
            super().__init__()
            self._owner = owner
            self._width = width
            self._side = side
            self._sliders: Dict[str, QSlider] = {}
            self._slider_vals: Dict[str, QLabel] = {}
            self._profile_btns: Dict[str, QPushButton] = {}

            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.NoDropShadowWindowHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

            self._build_ui()
            self._pending_hide = False
            self._anim = QPropertyAnimation(self, b"geometry")
            self._anim.setDuration(200)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.finished.connect(self._on_anim_finished)

        # -------------------------------------------------- construcao
        def _build_ui(self) -> None:
            self.setStyleSheet(_QSS)
            root = QFrame(self)
            root.setObjectName("card")
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(root)

            col = QVBoxLayout(root)
            col.setContentsMargins(22, 22, 22, 22)
            col.setSpacing(18)

            # Cabecalho
            header = QHBoxLayout()
            title = QLabel("Ajustes")
            title.setObjectName("title")
            header.addWidget(title)
            header.addStretch(1)
            close = QPushButton("✕")
            close.setObjectName("close")
            close.setFixedSize(28, 28)
            close.clicked.connect(self._owner.hide_panel)
            header.addWidget(close)
            col.addLayout(header)

            # Sliders
            for key, label in _SLIDERS:
                col.addLayout(self._make_slider(key, label))

            col.addWidget(self._divider())

            # Perfis (segmented)
            col.addWidget(self._section_label("PERFIL"))
            grid = QHBoxLayout()
            grid.setSpacing(8)
            for key, label in _PROFILES:
                btn = QPushButton(label)
                btn.setObjectName("profile")
                btn.setCheckable(True)
                btn.clicked.connect(lambda _=False, k=key: self._pick_profile(k))
                self._profile_btns[key] = btn
                grid.addWidget(btn)
            col.addLayout(grid)

            col.addWidget(self._divider())

            # Toggles
            self._aim_switch = self._make_toggle(
                col, "Assistência de mira", "aim",
            )
            self._sticky_switch = self._make_toggle(
                col, "Aderência a alvos", "sticky",
            )

            col.addWidget(self._divider())

            # Acoes
            actions = QHBoxLayout()
            actions.setSpacing(8)
            reset = QPushButton("Restaurar perfil")
            reset.setObjectName("ghost")
            reset.clicked.connect(self._owner.on_reset)
            rec = QPushButton("Recomendado")
            rec.setObjectName("solid")
            rec.clicked.connect(self._owner.on_apply_recommended)
            actions.addWidget(reset)
            actions.addWidget(rec)
            col.addLayout(actions)

        def _make_slider(self, key: str, label: str):
            box = QVBoxLayout()
            box.setSpacing(6)
            row = QHBoxLayout()
            name = QLabel(label)
            name.setObjectName("slabel")
            val = QLabel("")
            val.setObjectName("svalue")
            row.addWidget(name)
            row.addStretch(1)
            row.addWidget(val)
            box.addLayout(row)

            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(0, 1000)
            s.valueChanged.connect(
                lambda v, k=key: self._owner.on_slider(k, v / 1000.0)
            )
            box.addWidget(s)
            self._sliders[key] = s
            self._slider_vals[key] = val
            return box

        def _make_toggle(self, col, label: str, key: str) -> "_Switch":
            row = QHBoxLayout()
            name = QLabel(label)
            name.setObjectName("slabel")
            sw = _Switch(lambda v, k=key: self._owner.on_toggle(k, v))
            row.addWidget(name)
            row.addStretch(1)
            row.addWidget(sw)
            col.addLayout(row)
            return sw

        def _section_label(self, text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("section")
            return lbl

        def _divider(self) -> QFrame:
            line = QFrame()
            line.setObjectName("divider")
            line.setFixedHeight(1)
            return line

        # -------------------------------------------------- interacao
        def _pick_profile(self, key: str) -> None:
            for k, btn in self._profile_btns.items():
                btn.setChecked(k == key)
            self._owner.on_profile_change(key)

        # -------------------------------------------------- sync/estado
        def apply_snapshot(
            self,
            sliders: Dict[str, float],
            displays: Dict[str, str],
            aim: bool,
            sticky: bool,
            profile: str,
        ) -> None:
            for key, s in self._sliders.items():
                s.blockSignals(True)
                s.setValue(round(sliders.get(key, 0.5) * 1000))
                s.blockSignals(False)
                self._slider_vals[key].setText(displays.get(key, ""))
            for k, btn in self._profile_btns.items():
                btn.setChecked(k == profile)
            self._aim_switch.set_on(aim)
            self._sticky_switch.set_on(sticky)

        def set_slider_display(self, key: str, text: str) -> None:
            if key in self._slider_vals:
                self._slider_vals[key].setText(text)

        # -------------------------------------------------- animacao
        def _target_rect(self, screen: QRect) -> QRect:
            h = min(screen.height() - 80, 640)
            y = screen.y() + (screen.height() - h) // 2
            if self._side == "left":
                x = screen.x() + 16
            else:
                x = screen.x() + screen.width() - self._width - 16
            return QRect(x, y, self._width, h)

        def _hidden_rect(self, target: QRect, screen: QRect) -> QRect:
            off = self._width + 40
            dx = -off if self._side == "left" else off
            return QRect(target.x() + dx, target.y(), target.width(), target.height())

        def slide_in(self, screen: QRect) -> None:
            target = self._target_rect(screen)
            self._pending_hide = False
            self.setGeometry(self._hidden_rect(target, screen))
            self.show()
            self.raise_()
            self._anim.stop()
            self._anim.setStartValue(self.geometry())
            self._anim.setEndValue(target)
            self._anim.start()

        def slide_out(self, screen: QRect) -> None:
            target = self._target_rect(screen)
            self._pending_hide = True
            self._anim.stop()
            self._anim.setStartValue(self.geometry())
            self._anim.setEndValue(self._hidden_rect(target, screen))
            self._anim.start()

        def _on_anim_finished(self) -> None:
            # Esconde a janela so quando a animacao de SAIDA termina.
            if self._pending_hide:
                self.hide()

        def keyPressEvent(self, event) -> None:
            if event.key() == Qt.Key.Key_Escape:
                self._owner.hide_panel()
            else:
                super().keyPressEvent(event)

    class _EdgeHandle(QWidget):
        """Aba na borda da tela — clique abre o painel.

        Dimensionada pra ser encontravel sem atrapalhar: alta o
        suficiente pra chamar o olho, estreita pra nao roubar area util.
        """

        _W = 34
        _H = 92

        def __init__(self, owner: "SettingsPanel", side: str) -> None:
            super().__init__()
            self._owner = owner
            self._side = side
            self._hover = False
            self.setFixedSize(self._W, self._H)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Ajustes (S)")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.NoDropShadowWindowHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setMouseTracking(True)

        def place(self, screen: QRect) -> None:
            y = screen.y() + (screen.height() - self._H) // 2
            x = (
                screen.x() if self._side == "left"
                else screen.x() + screen.width() - self._W
            )
            self.move(x, y)

        def enterEvent(self, event) -> None:
            self._hover = True
            self.update()

        def leaveEvent(self, event) -> None:
            self._hover = False
            self.update()

        def mousePressEvent(self, event) -> None:
            self._owner.toggle()

        def paintEvent(self, event) -> None:
            import math

            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            bg = QColor(_BG)
            bg.setAlpha(255 if self._hover else 215)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(bg)
            # Cantos arredondados apenas do lado voltado pra tela
            if self._side == "left":
                p.drawRoundedRect(-self._W, 1, self._W * 2 - 2, self._H - 2, 16, 16)
            else:
                p.drawRoundedRect(1, 1, self._W * 2, self._H - 2, 16, 16)

            # Engrenagem: aro + 8 dentes + miolo. Branco puro no hover.
            cx = self._W // 2 + (3 if self._side == "left" else -3)
            cy = self._H // 2
            color = QColor(_INK if self._hover else _INK2)
            pen = QPen(color)
            pen.setWidth(2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            r_ring = 8
            p.drawEllipse(cx - r_ring, cy - r_ring, r_ring * 2, r_ring * 2)
            for i in range(8):
                ang = math.pi * i / 4.0
                x0 = cx + math.cos(ang) * (r_ring + 1)
                y0 = cy + math.sin(ang) * (r_ring + 1)
                x1 = cx + math.cos(ang) * (r_ring + 4)
                y1 = cy + math.sin(ang) * (r_ring + 4)
                p.drawLine(int(x0), int(y0), int(x1), int(y1))
            p.setBrush(color)
            p.drawEllipse(cx - 2, cy - 2, 4, 4)


# QSS aplicado ao painel — CSS do Qt. So existe se PySide6 carregou.
_QSS = f"""
#card {{
    background: {_BG};
    border: 1px solid {_LINE2};
    border-radius: 20px;
}}
QLabel {{ color: {_INK}; font-size: 13px; }}
#title {{ font-size: 17px; font-weight: 600; color: {_INK}; }}
#section {{ color: {_MUTE}; font-size: 10px; font-weight: 600;
           letter-spacing: 2px; }}
#slabel {{ color: {_INK2}; font-size: 13px; }}
#svalue {{ color: {_INK}; font-size: 12px; }}
#divider {{ background: {_LINE}; border: none; }}
#close {{
    color: {_MUTE}; background: transparent; border: none;
    border-radius: 14px; font-size: 14px;
}}
#close:hover {{ color: {_INK}; background: {_ELEV}; }}
QPushButton#profile {{
    color: {_INK2}; background: transparent;
    border: 1px solid {_LINE2}; border-radius: 999px;
    padding: 7px 0; font-size: 12px;
}}
QPushButton#profile:hover {{ border-color: {_INK2}; }}
QPushButton#profile:checked {{
    color: {_KNOB_DARK}; background: {_INK}; border-color: {_INK};
    font-weight: 600;
}}
QPushButton#ghost {{
    color: {_INK2}; background: transparent;
    border: 1px solid {_LINE2}; border-radius: 999px;
    padding: 9px 0; font-size: 12px;
}}
QPushButton#ghost:hover {{ border-color: {_INK}; color: {_INK}; }}
QPushButton#solid {{
    color: {_KNOB_DARK}; background: {_INK};
    border: 1px solid {_INK}; border-radius: 999px;
    padding: 9px 0; font-size: 12px; font-weight: 600;
}}
QPushButton#solid:hover {{ background: #dcdcdc; border-color: #dcdcdc; }}
QSlider::groove:horizontal {{
    height: 3px; background: #2e2e2e; border-radius: 1px;
}}
QSlider::sub-page:horizontal {{
    height: 3px; background: {_INK}; border-radius: 1px;
}}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -7px 0;
    border-radius: 8px; background: {_INK};
    border: 3px solid {_BG};
}}
"""


# ─────────────────────────────────────────────────────────── facade


class SettingsPanel:
    """Facade publica do painel de ajustes. Degrada sem PySide6."""

    def __init__(
        self,
        *,
        callbacks=None,
        width: int = 320,
        side: str = "right",
    ) -> None:
        self.available: bool = False
        self._callbacks = callbacks
        self._width = max(260, int(width))
        self._side = "left" if str(side).lower() == "left" else "right"
        self._app: Optional[QApplication] = None
        self._handle: Optional[_EdgeHandle] = None
        self._panel: Optional[_PanelWindow] = None
        self._open = False

        if not _PYSIDE_OK:
            logger.info(
                "SettingsPanel: PySide6 indisponivel (%s). Painel Qt "
                "desativado; tecla S segue como fallback.",
                _IMPORT_ERROR,
            )
            return

        try:
            self._init_qt()
            self.available = True
        except Exception as e:  # pragma: no cover
            logger.warning(
                "SettingsPanel: falha ao inicializar (%s). Desativado; "
                "projeto continua funcional.", e,
            )
            self.available = False

    # ---------------------------------------------------------- setup
    def _init_qt(self) -> None:
        existing_app = QApplication.instance()
        self._app = (
            cast(QApplication, existing_app)
            if existing_app is not None
            else QApplication([])
        )
        self._handle = _EdgeHandle(self, self._side)
        self._panel = _PanelWindow(self, self._width, self._side)
        self._place_handle()
        self._handle.show()

    def _screen_geometry(self) -> "QRect":
        app = self._app
        if app is None:
            return QRect(0, 0, 1920, 1080)
        screen = app.primaryScreen()
        return screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)

    def _place_handle(self) -> None:
        handle = self._handle
        if handle is not None:
            handle.place(self._screen_geometry())

    # ---------------------------------------------------------- API
    def bind_callbacks(self, callbacks) -> None:
        self._callbacks = callbacks

    def sync(
        self,
        sliders: Dict[str, float],
        displays: Dict[str, str],
        aim: bool,
        sticky: bool,
        profile: str,
    ) -> None:
        """Popula o painel com o estado atual do RuntimeSettings."""
        if self._panel is not None:
            self._panel.apply_snapshot(sliders, displays, aim, sticky, profile)

    def set_slider_display(self, key: str, text: str) -> None:
        """Atualiza so o rotulo de valor de um slider (feedback ao vivo
        enquanto o usuario arrasta). Nao mexe na posicao do slider."""
        if self._panel is not None:
            self._panel.set_slider_display(key, text)

    def pump(self) -> None:
        """Drena eventos Qt. Chamado a cada tick (idempotente se ja
        drenado pelo holograma no mesmo frame)."""
        if not self.available or self._app is None:
            return
        try:
            self._app.processEvents()
        except Exception as e:  # pragma: no cover
            logger.debug("SettingsPanel.pump falhou: %s", e)

    def toggle(self) -> None:
        if not self.available:
            return
        self.hide_panel() if self._open else self.show_panel()

    def show_panel(self) -> None:
        panel = self._panel
        if not self.available or self._open or panel is None:
            return
        self._open = True
        panel.slide_in(self._screen_geometry())

    def hide_panel(self) -> None:
        panel = self._panel
        if not self.available or not self._open or panel is None:
            return
        self._open = False
        panel.slide_out(self._screen_geometry())

    def close(self) -> None:
        for w in (self._panel, self._handle):
            if w is not None:
                try:
                    w.close()
                except Exception as e:  # pragma: no cover
                    logger.debug("SettingsPanel.close falhou: %s", e)
        self._panel = None
        self._handle = None
        self._app = None
        self._open = False
        self.available = False

    # ------------------------------------------- callbacks -> service
    # Repassam pro objeto UICallbacks do service (mesmo contrato do
    # painel OpenCV). Guardas evitam crash se callbacks nao setado.
    def on_slider(self, key: str, value: float) -> None:
        # O service aplica a mudanca e empurra o display de volta via
        # set_slider_display() — mantem a fonte de verdade no RuntimeSettings.
        if self._callbacks is not None:
            self._callbacks.on_slider_change(key, value)

    def on_profile_change(self, profile: str) -> None:
        if self._callbacks is not None:
            self._callbacks.on_profile_change(profile)

    def on_toggle(self, key: str, value: bool) -> None:
        if self._callbacks is not None:
            self._callbacks.on_toggle(key, value)

    def on_reset(self) -> None:
        if self._callbacks is not None:
            self._callbacks.on_reset()

    def on_apply_recommended(self) -> None:
        if self._callbacks is not None:
            self._callbacks.on_apply_recommended()
