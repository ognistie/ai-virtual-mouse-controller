"""
core/hologram_overlay.py
========================

Overlay holografico. Janela Tk fullscreen, transparente, sempre no topo,
sem capturar cliques (passa para as janelas debaixo). Desenha a mao
seguindo o cursor.

Design pensado pra zero acoplamento com o resto do projeto:

- Inicializacao falha *com graca*: se o sistema nao for Windows ou se algum
  passo de transparencia falhar, o overlay simplesmente fica desligado e
  loga warning. O programa principal continua funcionando.

- update_pose() guarda landmarks + posicao. Nao desenha nada.
  E' barato e pode ser chamado em todo frame de deteccao (60Hz).

- pump() processa eventos pendentes do Tk e redesenha (se passou tempo
  suficiente desde o ultimo redraw). Deve ser chamado regularmente pelo
  service. Sem isso, a janela congela.

- set_enabled(False) esconde a janela mas mantem o objeto. Toggle barato.

Por que NAO uma thread separada: Tkinter nao gosta de ser chamado de
threads que nao criaram o root. Mantemos tudo no thread principal e
"bombamos" o mainloop manualmente com update() (mais previsivel e debugavel).
"""

from __future__ import annotations

import logging
import sys
import time
import tkinter as tk
from typing import Optional, Sequence, Tuple

from .hand_renderer import compute_hand_primitives

logger = logging.getLogger(__name__)


def _apply_click_through_win32(root: tk.Tk) -> bool:
    """
    Aplica WS_EX_LAYERED | WS_EX_TRANSPARENT no HWND do Tk.
    Retorna True se aplicado, False em qualquer falha.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020

        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id())
        if not hwnd:
            return False
        styles = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, styles | WS_EX_LAYERED | WS_EX_TRANSPARENT
        )
        return True
    except Exception as e:  # pragma: no cover
        logger.warning("Falha ao aplicar click-through Win32: %s", e)
        return False


def _make_color_with_alpha(hex_color: str, alpha: float, bg: str) -> str:
    """
    Tk Canvas nao suporta alpha em fill de items individuais (so de imagens).
    Workaround: mistura a cor com o fundo "transparente" pra simular
    semi-transparencia. Funciona porque o fundo magico vai virar invisivel
    via -transparentcolor.

    Args:
        hex_color: cor desejada em #RRGGBB
        alpha: 0.0 (totalmente fundo) a 1.0 (cor original)
        bg: cor de fundo do canvas (sera a "transparent color")

    Returns:
        Hex string da cor misturada.
    """
    def _parse(c: str) -> Tuple[int, int, int]:
        c = c.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    fr, fg, fb = _parse(hex_color)
    br, bg_g, bb = _parse(bg)
    a = max(0.0, min(1.0, alpha))
    r = round(fr * a + br * (1 - a))
    g = round(fg * a + bg_g * (1 - a))
    b = round(fb * a + bb * (1 - a))
    return f"#{r:02x}{g:02x}{b:02x}"


class HologramOverlay:
    """
    Janela transparente que desenha a mao.

    Uso:
        h = HologramOverlay()
        if h.available:
            h.set_enabled(True)
        # No loop:
        h.update_pose(landmarks, screen_x, screen_y)
        h.pump()
        # No fim:
        h.close()
    """

    def __init__(
        self,
        *,
        hand_size_px: int = 180,
        opacity: float = 0.40,
        target_fps: int = 30,
        bone_color: str = "#d92626",
        point_color: str = "#f6efe2",
        transparent_color: str = "#010203",
    ) -> None:
        self.available: bool = False
        self.click_through_active: bool = False
        self._enabled: bool = False

        self._hand_size_px = hand_size_px
        self._opacity = opacity
        self._target_fps = max(10, target_fps)
        self._min_interval = 1.0 / self._target_fps
        self._transparent_color = transparent_color

        # cores pre-misturadas pra simular alpha
        self._bone_color = _make_color_with_alpha(
            bone_color, opacity, transparent_color
        )
        self._point_color = _make_color_with_alpha(
            point_color, opacity, transparent_color
        )

        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._bone_ids: list[int] = []
        self._point_ids: list[int] = []
        self._last_draw_ts: float = 0.0

        # estado da mao atual
        self._pose_landmarks: Optional[
            Sequence[Tuple[float, float, float]]
        ] = None
        self._pose_x: float = 0.0
        self._pose_y: float = 0.0
        self._pose_visible: bool = False

        try:
            self._build_window()
            self.available = True
        except Exception as e:  # pragma: no cover
            logger.warning(
                "HologramOverlay indisponivel (%s). Continuando sem hologramas.",
                e,
            )
            self.available = False

    # ----------------------------------------------------------------- setup

    def _build_window(self) -> None:
        root = tk.Tk()
        root.title("AVM Hologram")
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", self._transparent_color)
        root.configure(bg=self._transparent_color)
        root.overrideredirect(True)

        canvas = tk.Canvas(
            root,
            bg=self._transparent_color,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)

        # cria primitivas vazias que serao atualizadas em vez de recriadas
        self._bone_ids = []
        self._point_ids = []

        root.update_idletasks()
        self.click_through_active = _apply_click_through_win32(root)
        # comeca escondida ate set_enabled(True)
        root.withdraw()

        self._root = root
        self._canvas = canvas

    # ----------------------------------------------------------------- API

    def set_enabled(self, enabled: bool) -> None:
        if not self.available or self._root is None:
            return
        if enabled == self._enabled:
            return
        self._enabled = enabled
        try:
            if enabled:
                self._root.deiconify()
                self._root.lift()
                self._root.attributes("-topmost", True)
            else:
                self._root.withdraw()
                if self._canvas is not None:
                    self._canvas.delete("all")
                    self._bone_ids = []
                    self._point_ids = []
        except tk.TclError as e:
            logger.debug("Falha ao alternar overlay: %s", e)

    def toggle(self) -> bool:
        """Inverte enabled. Retorna o novo estado."""
        self.set_enabled(not self._enabled)
        return self._enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def update_pose(
        self,
        landmarks: Optional[Sequence[Tuple[float, float, float]]],
        screen_x: float,
        screen_y: float,
    ) -> None:
        """
        Atualiza o estado da pose. Nao desenha. Barato.

        Passe landmarks=None pra esconder temporariamente (mao saiu do frame).
        """
        if landmarks is None or len(landmarks) < 21:
            self._pose_visible = False
            self._pose_landmarks = None
            return
        self._pose_visible = True
        self._pose_landmarks = landmarks
        self._pose_x = screen_x
        self._pose_y = screen_y

    def pump(self) -> None:
        """
        Bomba eventos do Tk e redesenha se passou o intervalo do FPS alvo.
        Chamado a cada tick do service.
        """
        if not self.available or not self._enabled or self._root is None:
            return

        # Throttling por FPS alvo
        now = time.perf_counter()
        if now - self._last_draw_ts >= self._min_interval:
            self._redraw()
            self._last_draw_ts = now

        try:
            self._root.update_idletasks()
            self._root.update()
        except tk.TclError:
            # janela foi fechada externamente
            self.available = False
            self._enabled = False

    def close(self) -> None:
        if self._root is not None:
            try:
                self._root.destroy()
            except tk.TclError:
                pass
            self._root = None
            self._canvas = None
        self.available = False
        self._enabled = False

    # ----------------------------------------------------------------- draw

    def _redraw(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return

        if not self._pose_visible or self._pose_landmarks is None:
            # esconde tudo
            for bid in self._bone_ids:
                canvas.itemconfigure(bid, state="hidden")
            for pid in self._point_ids:
                canvas.itemconfigure(pid, state="hidden")
            return

        primitives = compute_hand_primitives(
            self._pose_landmarks,
            self._pose_x,
            self._pose_y,
            self._hand_size_px,
        )

        # Cresce listas se necessario (so na primeira vez por tamanho)
        while len(self._bone_ids) < len(primitives.bones):
            bid = canvas.create_line(
                0, 0, 0, 0,
                fill=self._bone_color,
                width=2,
                capstyle="round",
            )
            self._bone_ids.append(bid)
        while len(self._point_ids) < len(primitives.points):
            pid = canvas.create_oval(
                0, 0, 0, 0,
                fill=self._point_color,
                outline="",
            )
            self._point_ids.append(pid)

        # Atualiza primitivas
        for bid, bone in zip(self._bone_ids, primitives.bones):
            canvas.coords(bid, bone.x1, bone.y1, bone.x2, bone.y2)
            canvas.itemconfigure(bid, width=bone.width, state="normal")
        # esconde bones extras (se houver)
        for bid in self._bone_ids[len(primitives.bones):]:
            canvas.itemconfigure(bid, state="hidden")

        for pid, pt in zip(self._point_ids, primitives.points):
            canvas.coords(
                pid,
                pt.x - pt.radius,
                pt.y - pt.radius,
                pt.x + pt.radius,
                pt.y + pt.radius,
            )
            canvas.itemconfigure(pid, state="normal")
        for pid in self._point_ids[len(primitives.points):]:
            canvas.itemconfigure(pid, state="hidden")
