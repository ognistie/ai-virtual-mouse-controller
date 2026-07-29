"""
services.virtual_mouse_service
==============================

Orquestra: camera + tracker + gesture detector + cursor + UI Overlay.

Responsabilidades:
- Construir todas as pecas a partir de config.py + RuntimeSettings
- Rodar o loop principal: read frame → detectar mao → processar gestos
  → mover cursor / disparar acoes → renderizar UI
- Gerenciar a janela (tamanho compacto/expandido, posicao, always-on-top)
- Conectar callbacks da UI com mudancas em tempo real do detector
"""

from __future__ import annotations

import logging
import signal
import sys
from typing import Optional

import cv2
import numpy as np

import config
from core.camera import ThreadedCamera
from core.cursor_controller import CursorController
from core.gesture_detector import Gesture, GestureDetector
from core.hand_tracker import HandTracker
from core.hologram_overlay import HologramOverlay
from core.perf_telemetry import configure as configure_perf, get_profiler
from core.presentation_controller import PresentationController
from core.runtime_settings import RuntimeSettings
from core.settings_panel import SettingsPanel
from core.smoothing import make_smoother
from core.ui_overlay import UICallbacks, UIOverlay
from core.utils import FPSCounter


logger = logging.getLogger(__name__)


# ===================================================================
# UI CALLBACK BRIDGE
# ===================================================================

class _ServiceUICallbacks(UICallbacks):
    """Bridge entre eventos da UI e o servico."""

    def __init__(self, service: "VirtualMouseService"):
        self._service = service

    def on_slider_change(self, key: str, value: float) -> None:
        self._service._on_slider_change(key, value)

    def on_profile_change(self, profile: str) -> None:
        self._service._on_profile_change(profile)

    def on_toggle(self, key: str, value: bool) -> None:
        self._service._on_toggle(key, value)

    def on_reset(self) -> None:
        self._service._on_reset()

    def on_apply_recommended(self) -> None:
        self._service._on_apply_recommended()


# ===================================================================
# MAIN SERVICE
# ===================================================================

class VirtualMouseService:
    """Servico principal com UI moderna integrada."""

    def __init__(
        self,
        camera: ThreadedCamera,
        tracker: HandTracker,
        gesture_detector: GestureDetector,
        cursor: CursorController,
        smoother,
        runtime_settings: RuntimeSettings,
        *,
        enable_preview: bool = True,
        draw_landmarks: bool = True,
        window_name: str = "AI Virtual Mouse",
    ) -> None:
        self.camera = camera
        self.tracker = tracker
        self.gesture_detector = gesture_detector
        self.cursor = cursor
        self.smoother = smoother
        self.runtime_settings = runtime_settings

        self.enable_preview = enable_preview
        self.draw_landmarks = draw_landmarks
        self.window_name = window_name

        self._fps = FPSCounter(window=30)
        self._motion_debug_tick = 0
        self._should_stop = False
        self._hand_lost_frames = 0
        self._always_on_top: bool = getattr(config, "DISPLAY_ALWAYS_ON_TOP", True)

        # UI Overlay
        self._ui_callbacks = _ServiceUICallbacks(self)
        self.ui = UIOverlay(self._ui_callbacks)
        self._sync_ui_from_settings()

        # Perf telemetry: configura singleton com defaults do config.
        # Zero overhead quando desabilitado.
        configure_perf(
            window_size=getattr(config, "PERF_TELEMETRY_WINDOW", 120),
            report_every=getattr(config, "PERF_TELEMETRY_REPORT_EVERY", 120),
            enabled=getattr(config, "PERF_TELEMETRY_ENABLED", True),
        )
        self._perf = get_profiler()

        # Pre-resize antes do inference (opt-in, perf)
        self._pre_resize_enabled: bool = getattr(
            config, "INFERENCE_PRE_RESIZE_ENABLED", False,
        )
        self._pre_resize_width: int = max(64, int(getattr(
            config, "INFERENCE_PRE_RESIZE_WIDTH", 320,
        )))

        # cv2.pollKey() em vez de waitKeyEx(1) — corta cap timer Windows.
        # Detecta uma vez no startup. Fallback automatico se nao existir.
        self._use_pollkey: bool = (
            bool(getattr(config, "CV2_USE_POLLKEY", True))
            and hasattr(cv2, "pollKey")
        )
        if self._use_pollkey:
            logger.info("Usando cv2.pollKey() (non-blocking)")
        else:
            logger.info("Usando cv2.waitKeyEx(1) (fallback)")

        self._presentation_mode: bool = False
        self._presentation_toggle_key: str = (
            getattr(config, "PRESENTATION_TOGGLE_KEY", "z") or "z"
        ).lower()
        self._presentation_next_key: str = getattr(
            config, "PRESENTATION_NEXT_KEY", "right"
        )
        self._presentation_prev_key: str = getattr(
            config, "PRESENTATION_PREV_KEY", "left"
        )
        self._presentation = PresentationController(
            dead_zone=getattr(config, "PRESENTATION_DEAD_ZONE", 0.20),
            cooldown_s=getattr(config, "PRESENTATION_COOLDOWN_S", 0.5),
            extension_threshold=getattr(
                config, "PRESENTATION_EXTENSION_THRESHOLD", 0.80
            ),
        )

        # Hologram overlay (mao virtual desenhada sobre o desktop).
        # Falha silenciosamente se a plataforma nao suportar - o resto do
        # programa nao depende dele.
        self.hologram: Optional[HologramOverlay] = None
        self._hologram_toggle_key: str = (
            getattr(config, "HOLOGRAM_TOGGLE_KEY", "h") or "h"
        ).lower()
        try:
            self.hologram = HologramOverlay(
                hand_size_px=getattr(config, "HOLOGRAM_HAND_SIZE_PX", 180),
                opacity=getattr(config, "HOLOGRAM_OPACITY", 0.40),
                target_fps=getattr(config, "HOLOGRAM_FPS", 30),
                bone_color=getattr(config, "HOLOGRAM_COLOR_BONE", "#d92626"),
                point_color=getattr(config, "HOLOGRAM_COLOR_POINT", "#f6efe2"),
                transparent_color=getattr(
                    config, "HOLOGRAM_TRANSPARENT_COLOR", "#010203"
                ),
                view_dorsal=getattr(config, "HOLOGRAM_VIEW_DORSAL", True),
                particles_enabled=getattr(
                    config, "HOLOGRAM_PARTICLES_ENABLED", True,
                ),
                particle_count=getattr(
                    config, "HOLOGRAM_PARTICLE_COUNT", 180,
                ),
            )
            print(
                f"[AVM] HologramOverlay criado: available={self.hologram.available}, "
                f"click_through={self.hologram.click_through_active}, "
                f"tela={self.hologram.screen_size[0]}x{self.hologram.screen_size[1]} | "
                f"aperte '{self._hologram_toggle_key.upper()}' pra ligar/desligar",
                flush=True,
            )
            if getattr(config, "HOLOGRAM_ENABLED", False) and self.hologram.available:
                self.hologram.set_enabled(True)
                print(
                    "[AVM] Hologram ATIVO no startup", flush=True,
                )
        except Exception as e:
            print(f"[AVM] Falha ao iniciar HologramOverlay: {e}", flush=True)
            logger.warning("Falha ao iniciar HologramOverlay: %s", e)
            self.hologram = None

        # Painel de ajustes Qt (handle lateral). Compartilha a mesma
        # QApplication do holograma. Degrada sozinho sem PySide6 — a
        # tecla S + painel OpenCV seguem como fallback.
        self.settings_panel: Optional[SettingsPanel] = None
        if getattr(config, "SETTINGS_PANEL_QT_ENABLED", True):
            try:
                self.settings_panel = SettingsPanel(
                    callbacks=self._ui_callbacks,
                    width=getattr(config, "SETTINGS_PANEL_WIDTH", 320),
                    side=getattr(config, "SETTINGS_PANEL_SIDE", "right"),
                )
                if self.settings_panel.available:
                    self._sync_settings_panel()
                    print("[AVM] Painel de ajustes Qt ativo (handle lateral)",
                          flush=True)
            except Exception as e:
                logger.warning("Falha ao iniciar SettingsPanel: %s", e)
                self.settings_panel = None

    # -----------------------------------------------------------------
    # Factory (todas as chamadas verificadas contra core/)
    # -----------------------------------------------------------------

    @classmethod
    def from_config(cls) -> "VirtualMouseService":
        # 1. Runtime settings (perfil inicial)
        initial_profile = getattr(config, "PROFILE", "smooth")
        runtime = RuntimeSettings(initial_profile=initial_profile)

        # 2. Camera (API real: fps, .open())
        camera = ThreadedCamera(
            index=config.CAMERA_INDEX,
            width=config.CAMERA_WIDTH,
            height=config.CAMERA_HEIGHT,
            fps=config.CAMERA_FPS_TARGET,
        )
        camera.open()

        # 3. Hand tracker
        tracker = HandTracker(
            max_num_hands=config.MAX_NUM_HANDS,
            min_detection_confidence=config.MIN_DETECTION_CONFIDENCE,
            min_tracking_confidence=config.MIN_TRACKING_CONFIDENCE,
            model_complexity=config.MODEL_COMPLEXITY,
        )

        # 4. Gesture detector — usa valores do runtime
        gesture_detector = cls._build_detector(runtime)

        # 5. Cursor — margens assimetricas (X != Y) com fallback isotropico
        cursor = CursorController(
            screen_margin_percentage=config.SCREEN_MARGIN_PERCENTAGE,
            screen_margin_x=getattr(config, "SCREEN_MARGIN_X", None),
            screen_margin_top=getattr(config, "SCREEN_MARGIN_TOP", None),
            screen_margin_bottom=getattr(config, "SCREEN_MARGIN_BOTTOM", None),
            dead_zone_pixels=config.DEAD_ZONE_PIXELS,
            failsafe=config.PYAUTOGUI_FAILSAFE,
            pyautogui_pause=config.PYAUTOGUI_PAUSE,
            click_freeze_seconds=getattr(
                config, "CLICK_FREEZE_SECONDS", 0.12,
            ),
            drag_precision_factor=getattr(
                config, "DRAG_PRECISION_FACTOR", 0.55,
            ),
            y_bottom_boost_enabled=getattr(
                config, "CURSOR_Y_BOTTOM_BOOST_ENABLED", False,
            ),
            y_bottom_boost_knee=getattr(
                config, "CURSOR_Y_BOTTOM_BOOST_KNEE", 0.72,
            ),
            y_bottom_boost_power=getattr(
                config, "CURSOR_Y_BOTTOM_BOOST_POWER", 1.6,
            ),
            edge_snap_px=getattr(config, "CURSOR_EDGE_SNAP_PX", 0),
            edge_creep_enabled=getattr(
                config, "CURSOR_EDGE_CREEP_ENABLED", False,
            ),
            edge_creep_band_px=getattr(
                config, "CURSOR_EDGE_CREEP_BAND_PX", 72,
            ),
            edge_creep_delay_s=getattr(
                config, "CURSOR_EDGE_CREEP_DELAY_S", 0.15,
            ),
            edge_creep_rate_px_s=getattr(
                config, "CURSOR_EDGE_CREEP_RATE_PX_S", 320.0,
            ),
        )

        # 6. Smoother (API real: kwargs alpha, freq, min_cutoff, beta, d_cutoff)
        smoother = make_smoother(
            config.SMOOTHING_STRATEGY,
            alpha=config.SMOOTHING_FACTOR,
            freq=config.ONE_EURO_FREQ,
            min_cutoff=runtime.get("one_euro_min_cutoff"),
            beta=runtime.get("one_euro_beta"),
            d_cutoff=config.ONE_EURO_D_CUTOFF,
        )

        return cls(
            camera=camera,
            tracker=tracker,
            gesture_detector=gesture_detector,
            cursor=cursor,
            smoother=smoother,
            runtime_settings=runtime,
            enable_preview=config.ENABLE_PREVIEW,
            draw_landmarks=config.DRAW_LANDMARKS,
            window_name=config.WINDOW_NAME,
        )

    @staticmethod
    def _build_detector(runtime: RuntimeSettings) -> GestureDetector:
        """Cria GestureDetector com valores atuais do runtime."""
        return GestureDetector(
            anchor_landmark=config.CURSOR_ANCHOR_LANDMARK,
            pinch_threshold=runtime.get("pinch_distance_threshold"),
            pinch_threshold_floor=config.PINCH_THRESHOLD_FLOOR,
            pinch_middle_threshold=config.PINCH_MIDDLE_THRESHOLD,
            pinch_middle_index_guard=config.PINCH_MIDDLE_INDEX_GUARD,
            pinch_min_hold_seconds=config.PINCH_MIN_HOLD_SECONDS,
            pinch_adaptive=config.PINCH_ADAPTIVE_TO_HAND_SIZE,
            dpi_adaptive_enabled=config.DPI_ADAPTIVE_ENABLED,
            hand_size_reference=config.HAND_SIZE_REFERENCE,
            dpi_min=config.DPI_MULTIPLIER_MIN,
            dpi_max=config.DPI_MULTIPLIER_MAX,
            dpi_fixed=runtime.get("dpi_fixed_multiplier"),
            drag_hold_seconds=config.DRAG_HOLD_SECONDS,
            click_cooldown=config.CLICK_COOLDOWN_SECONDS,
            double_click_cooldown=config.DOUBLE_CLICK_COOLDOWN_SECONDS,
            double_click_window=config.DOUBLE_CLICK_WINDOW_SECONDS,
            debounce_frames=config.GESTURE_DEBOUNCE_FRAMES,
            exit_frames=config.GESTURE_EXIT_FRAMES,
            position_hold_frames=config.POSITION_HOLD_FRAMES,
            aim_assist_enabled=runtime.aim_enabled,
            aim_assist_slowdown_factor=runtime.get("aim_assist_slowdown_factor"),
            aim_assist_trigger_shapes=config.AIM_ASSIST_TRIGGER_SHAPES,
            aim_assist_pre_activation=config.AIM_ASSIST_PRE_ACTIVATION,
            aim_assist_pre_pinch_threshold=config.AIM_ASSIST_PRE_PINCH_THRESHOLD,
            aim_assist_holdover_seconds=config.AIM_ASSIST_HOLDOVER_SECONDS,
            sticky_targeting_enabled=runtime.sticky_enabled,
            sticky_deceleration_threshold=config.STICKY_DECELERATION_THRESHOLD,
            sticky_friction_factor=runtime.get("sticky_friction_factor"),
            sticky_min_velocity=config.STICKY_MIN_VELOCITY,
            velocity_curve_enabled=config.VELOCITY_CURVE_ENABLED,
            velocity_tremor_threshold=config.VELOCITY_TREMOR_THRESHOLD,
            velocity_precision_zone=config.VELOCITY_PRECISION_ZONE,
            velocity_fast_threshold=config.VELOCITY_FAST_THRESHOLD,
            velocity_slow_factor=config.VELOCITY_SLOW_FACTOR,
            velocity_fast_factor=config.VELOCITY_FAST_FACTOR,
            pinch_dual_detection=config.PINCH_DUAL_DETECTION,
            pinch_velocity_threshold=config.PINCH_VELOCITY_THRESHOLD,
            pinch_middle_index_extension_min=getattr(
                config, "PINCH_MIDDLE_INDEX_EXTENSION_MIN", 0.88,
            ),
            pinch_middle_middle_extension_max=getattr(
                config, "PINCH_MIDDLE_MIDDLE_EXTENSION_MAX", 0.92,
            ),
            # Movimento adaptativo (core/cursor_motion.py)
            distance_gain_enabled=getattr(
                config, "DPI_ADAPTIVE_ENABLED", True,
            ),
            distance_gain_far=getattr(config, "DISTANCE_GAIN_FAR", 1.40),
            distance_gain_near=getattr(config, "DISTANCE_GAIN_NEAR", 0.75),
            distance_scale_far_ratio=getattr(
                config, "DISTANCE_SCALE_FAR_RATIO", 0.60,
            ),
            distance_scale_near_ratio=getattr(
                config, "DISTANCE_SCALE_NEAR_RATIO", 1.55,
            ),
            distance_scale_filter_hz=getattr(
                config, "DISTANCE_SCALE_FILTER_HZ", 1.2,
            ),
            distance_gain_rate_per_second=getattr(
                config, "DISTANCE_GAIN_RATE_PER_SECOND", 1.5,
            ),
            precision_attack_seconds=getattr(
                config, "PRECISION_ATTACK_SECONDS", 0.10,
            ),
            precision_release_seconds=getattr(
                config, "PRECISION_RELEASE_SECONDS", 0.22,
            ),
            bottom_assist_enabled=getattr(
                config, "BOTTOM_ASSIST_ENABLED", True,
            ),
            # A borda inferior no espaco da ANCORA e' exatamente onde o
            # CursorController mapeia o ultimo pixel da tela.
            bottom_assist_edge=1.0 - getattr(
                config, "SCREEN_MARGIN_BOTTOM",
                config.SCREEN_MARGIN_PERCENTAGE,
            ),
            bottom_assist_band=getattr(config, "BOTTOM_ASSIST_BAND", 0.22),
            bottom_assist_max_gain=getattr(
                config, "BOTTOM_ASSIST_MAX_GAIN", 3.2,
            ),
            bottom_assist_max_extra_rate=getattr(
                config, "BOTTOM_ASSIST_MAX_EXTRA_RATE", 0.60,
            ),
            bottom_assist_intent_speed=getattr(
                config, "BOTTOM_ASSIST_INTENT_SPEED", 0.12,
            ),
            followthrough_frames=getattr(
                config, "CURSOR_FOLLOWTHROUGH_FRAMES", 6,
            ),
            followthrough_damping=getattr(
                config, "CURSOR_FOLLOWTHROUGH_DAMPING", 0.82,
            ),
        )

    # -----------------------------------------------------------------
    # Run loop
    # -----------------------------------------------------------------

    def run(self) -> int:
        self._setup_signal_handlers()

        if self.enable_preview:
            # WINDOW_NORMAL (em vez de AUTOSIZE) permite resize manual
            # via cv2.resizeWindow conforme o modo (compacto/expandido).
            cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
            cv2.setMouseCallback(self.window_name, self._on_mouse_event)
            self._apply_window_size(panel_visible=False)
            if getattr(config, "DISPLAY_POSITION_BOTTOM_RIGHT", False):
                self._position_window_bottom_right(
                    config.DISPLAY_COMPACT_WIDTH,
                    config.DISPLAY_COMPACT_HEIGHT,
                )
            self._apply_always_on_top()

        try:
            while not self._should_stop:
                self._tick()
        except KeyboardInterrupt:
            pass
        except Exception:
            logger.exception("Erro no loop principal")
            return 1
        finally:
            self._cleanup()
        return 0

    def stop(self) -> None:
        self._should_stop = True

    # -----------------------------------------------------------------
    # Window sizing — UX webcam de streamer
    # -----------------------------------------------------------------

    def _apply_window_size(self, panel_visible: bool) -> None:
        """
        Redimensiona a janela conforme o modo (compacto x expandido).
        Chamado no inicio e quando o usuario abre/fecha o painel com S.
        """
        if panel_visible:
            w = getattr(config, "DISPLAY_EXPANDED_WIDTH", 760)
            h = getattr(config, "DISPLAY_EXPANDED_HEIGHT", 540)
        else:
            w = getattr(config, "DISPLAY_COMPACT_WIDTH", 480)
            h = getattr(config, "DISPLAY_COMPACT_HEIGHT", 270)
        try:
            cv2.resizeWindow(self.window_name, w, h)
        except Exception as e:
            logger.debug("Falha ao redimensionar janela: %s", e)
        # Re-aplica always-on-top (alguns versions de OpenCV resetam apos resize)
        self._apply_always_on_top()

    def _position_window_bottom_right(self, win_w: int, win_h: int) -> None:
        """Posiciona a janela no canto inferior direito da tela primaria."""
        margin = getattr(config, "DISPLAY_POSITION_MARGIN", 20)
        screen_w, screen_h = self.cursor.screen_width, self.cursor.screen_height
        x = max(0, screen_w - win_w - margin)
        # Subtrai um extra pra nao colidir com a barra de tarefas (~50px no Windows)
        y = max(0, screen_h - win_h - margin - 50)
        try:
            cv2.moveWindow(self.window_name, x, y)
        except Exception as e:
            logger.debug("Falha ao posicionar janela: %s", e)

    def _apply_always_on_top(self, on: Optional[bool] = None) -> None:
        """
        Mantem a janela sempre visivel acima de outras.

        Args:
            on: True liga, False desliga, None usa self._always_on_top.
        """
        if on is not None:
            self._always_on_top = on
        try:
            value = 1.0 if self._always_on_top else 0.0
            cv2.setWindowProperty(
                self.window_name, cv2.WND_PROP_TOPMOST, value
            )
        except Exception as e:
            logger.debug("Falha ao definir always-on-top: %s", e)

    # -----------------------------------------------------------------
    # Frame tick
    # -----------------------------------------------------------------

    def _tick(self) -> None:
        prof = self._perf
        with prof.stage("camera"):
            frame = self.camera.read(mirror=True)
        if frame is None:
            return

        self._fps.tick()

        with prof.stage("inference"):
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # Pre-resize opt-in: corta memcpy + acelera mediapipe.
            # Landmarks voltam normalizados (0-1) entao escala = mesma.
            if self._pre_resize_enabled:
                h, w = rgb.shape[:2]
                tw = self._pre_resize_width
                if w > tw:
                    th = int(h * tw / w)
                    rgb = cv2.resize(rgb, (tw, th),
                                     interpolation=cv2.INTER_AREA)
            hands, raw_results = self.tracker.process_with_raw(rgb)
        hand = hands[0] if hands else None

        if hand is None:
            self._hand_lost_frames += 1
            if self._hand_lost_frames == 5:
                self.smoother.reset()
        else:
            self._hand_lost_frames = 0

        # Modos sao mutuamente exclusivos — detector de mouse e
        # apresentacao nunca rodam juntos no mesmo frame.
        if self._presentation_mode:
            with prof.stage("gesture"):
                slide_event = self._presentation.update(hand)
            with prof.stage("events"):
                self._handle_slide_event(slide_event)
        else:
            with prof.stage("gesture"):
                events = self.gesture_detector.update(hand)
            with prof.stage("events"):
                self._handle_events(events, hand)

        with prof.stage("hologram"):
            self._update_hologram(hand)

        # Painel Qt: drena eventos (handle + animacoes). Barato quando
        # nada mudou; None-safe quando indisponivel.
        if self.settings_panel is not None:
            self.settings_panel.pump()

        if self.enable_preview:
            with prof.stage("preview"):
                self._draw_overlays(frame, hand, raw_results)
                cv2.imshow(self.window_name, frame)
            with prof.stage("waitkey"):
                key = cv2.pollKey() if self._use_pollkey else cv2.waitKeyEx(1)
            if key == 27:  # ESC
                self.stop()
                return
            if key != -1:
                key_masked = key & 0xFF if 0 <= key < 256 else key
                # Tecla S: painel de ajustes Qt. O painel OpenCV legado
                # so entra como fallback quando PySide6 nao esta
                # disponivel — nunca os dois ao mesmo tempo.
                if key_masked in (ord('s'), ord('S')):
                    if (
                        self.settings_panel is not None
                        and self.settings_panel.available
                    ):
                        self.settings_panel.toggle()
                    else:
                        panel_was = self.ui.state.panel_visible
                        self.ui.handle_key(key_masked)
                        if panel_was != self.ui.state.panel_visible:
                            self._apply_window_size(
                                panel_visible=self.ui.state.panel_visible,
                            )
                    return
                if key_masked in (ord('t'), ord('T')):
                    self._apply_always_on_top(on=not self._always_on_top)
                    logger.info("ALWAYS_ON_TOP = %s", self._always_on_top)
                    return
                pkey = self._presentation_toggle_key
                if key_masked in (ord(pkey), ord(pkey.upper())):
                    self._set_presentation_mode(not self._presentation_mode)
                    return
                hkey = self._hologram_toggle_key
                if key_masked in (ord(hkey), ord(hkey.upper())):
                    if self.hologram is not None and self.hologram.available:
                        new_state = self.hologram.toggle()
                        msg = (
                            f"HOLOGRAM = {new_state} | "
                            f"click_through={self.hologram.click_through_active} | "
                            f"tela={self.hologram.screen_size[0]}x{self.hologram.screen_size[1]} | "
                            f"mao_visivel={hand is not None} | "
                            f"cursor={self.cursor.last_position}"
                        )
                        print(f"[AVM] {msg}", flush=True)
                        logger.info(msg)
                    else:
                        msg = (
                            f"Hologram indisponivel: hologram_obj={self.hologram is not None}, "
                            f"available={self.hologram.available if self.hologram else None}, "
                            f"platform={sys.platform}"
                        )
                        print(f"[AVM] {msg}", flush=True)
                        logger.warning(msg)
                    return
                # Demais teclas do overlay OpenCV (so agem com o painel
                # legado visivel — inertes no fluxo normal com painel Qt)
                self.ui.handle_key(key_masked)

        prof.tick()

    # -----------------------------------------------------------------
    # Hologram (mao virtual na tela)
    # -----------------------------------------------------------------

    def _update_hologram(self, hand) -> None:
        """Atualiza pose + cursor do holograma e bomba eventos do Tk.

        Chamado a cada _tick(). O pump precisa rodar mesmo quando o
        holograma esta off, senao o Windows marca a janela como travada.
        """
        h = self.hologram
        if h is None or not h.available:
            return

        if h.enabled:
            # Prefere posicao real do controller; fallback pyautogui pra
            # desenhar idle ring antes da primeira deteccao de mao.
            sx, sy = self.cursor.last_position
            if sx is None or sy is None:
                try:
                    import pyautogui
                    sx, sy = pyautogui.position()
                except Exception as e:
                    logger.debug("pyautogui.position() falhou: %s", e)
                    sx = self.cursor.screen_width // 2
                    sy = self.cursor.screen_height // 2
            h.update_cursor(sx, sy)
            h.update_pose(hand.landmarks if hand is not None else None, sx, sy)

        h.pump()

    # -----------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------

    def _handle_slide_event(self, slide_event: Optional[Gesture]) -> None:
        if slide_event is Gesture.NEXT_SLIDE:
            self.cursor.press_key(self._presentation_next_key)
        elif slide_event is Gesture.PREV_SLIDE:
            self.cursor.press_key(self._presentation_prev_key)

    def _handle_events(self, events, hand=None) -> None:
        move_event = None
        action_events = []
        for ev in events:
            if ev.gesture == Gesture.MOVE:
                move_event = ev
            else:
                action_events.append(ev)

        if move_event is not None and move_event.position is not None:
            x, y = move_event.position
            x_smooth, y_smooth = self.smoother(x, y)
            self.cursor.move(x_smooth, y_smooth)

        # Cliques disparam na posicao atual do cursor (ja suavizada pelo
        # OneEuroFilter), nao no pinch point — garante que o click caia
        # onde o cursor esta visivel. O burst no holograma e' so visual.
        for ev in action_events:
            if ev.gesture == Gesture.CLICK:
                self.cursor.click()
                self._fire_hologram_burst("click")
            elif ev.gesture == Gesture.DOUBLE_CLICK:
                self.cursor.double_click()
                self._fire_hologram_burst("double")
            elif ev.gesture == Gesture.RIGHT_CLICK:
                self.cursor.right_click()
                self._fire_hologram_burst("right")
            elif ev.gesture == Gesture.DRAG_START:
                self.cursor.drag_start()
                self._fire_hologram_burst("drag_start")
            elif ev.gesture == Gesture.DRAG_END:
                self.cursor.drag_end()
                self._fire_hologram_burst("drag_end")
            elif ev.gesture == Gesture.PAUSE:
                pass

    def _compute_pinch_screen(self, hand, gesture):
        """
        Calcula o ponto de pinch em screen coords para o gesto especifico.

        - CLICK / DRAG_START: midpoint(polegar=4, indicador=8)
        - RIGHT_CLICK:        midpoint(polegar=4, medio=12)
        - DOUBLE_CLICK:       midpoint(indicador=8, medio=12) [peace sign]

        Returns None se nao foi possivel calcular (sem mao ou landmarks
        faltando).
        """
        if hand is None:
            return None
        landmarks = getattr(hand, "landmarks", None)
        if landmarks is None or len(landmarks) < 21:
            return None

        if gesture == Gesture.RIGHT_CLICK:
            a, b = 4, 12
        elif gesture == Gesture.DOUBLE_CLICK:
            a, b = 8, 12
        else:
            a, b = 4, 8

        try:
            ax, ay = landmarks[a][0], landmarks[a][1]
            bx, by = landmarks[b][0], landmarks[b][1]
            nx = (ax + bx) / 2.0
            ny = (ay + by) / 2.0
            return self.cursor.map_to_screen(nx, ny)
        except Exception:
            return None

    def _fire_hologram_burst(self, kind: str) -> None:
        """Dispara burst visual no holograma se ele estiver ativo."""
        if self.hologram is not None and self.hologram.available and self.hologram.enabled:
            try:
                self.hologram.fire_burst(kind)
            except Exception as e:
                logger.debug("hologram.fire_burst(%s) falhou: %s", kind, e)

    # -----------------------------------------------------------------
    # Mouse callback
    # -----------------------------------------------------------------

    def _on_mouse_event(self, event: int, x: int, y: int, flags: int, param) -> None:
        try:
            self.ui.handle_mouse(event, x, y)
        except Exception:
            logger.exception("Erro no mouse callback")

    # -----------------------------------------------------------------
    # UI callbacks
    # -----------------------------------------------------------------

    def _on_slider_change(self, key: str, value: float) -> None:
        updates = self.runtime_settings.set_slider(key, value)
        for setting_key, real_val in updates.items():
            self._apply_setting_live(setting_key, real_val)
        display = self.runtime_settings.get_slider_display(key)
        self.ui.set_slider_display(key, display)
        self.ui.set_doc_rows(self.runtime_settings.get_doc_rows())
        if self.settings_panel is not None and self.settings_panel.available:
            self.settings_panel.set_slider_display(key, display)

    def _apply_setting_live(self, key: str, value: float) -> None:
        """Aplica setting individual no detector/smoother sem restart.

        Usa setattr seguro: se o detector nao tem o atributo (ex:
        anchor_freeze), a operacao e ignorada silenciosamente.
        """
        d = self.gesture_detector

        if key == "dpi_fixed_multiplier":
            d.dpi_fixed = value
        elif key == "aim_assist_slowdown_factor":
            d.aim_assist_slowdown_factor = value
        elif key == "one_euro_min_cutoff":
            self._apply_smoothing_change("min_cutoff", value)
        elif key == "one_euro_beta":
            self._apply_smoothing_change("beta", value)
        elif key == "pinch_distance_threshold":
            d.pinch_threshold = value
        elif key == "sticky_friction_factor":
            d.sticky_friction_factor = value
        elif key == "anchor_freeze_duration_ms":
            # Detector pode nao ter essa feature — ignora silenciosamente
            if hasattr(d, "anchor_freeze_duration_seconds"):
                d.anchor_freeze_duration_seconds = value / 1000.0

    def _apply_smoothing_change(self, attr: str, value: float) -> None:
        """Atualiza OneEuroSmoother2D ao vivo.

        OneEuroSmoother2D usa atributos internos _fx e _fy (nao _x/_y).
        Cada um e um _OneEuroAxis com .min_cutoff e .beta como campos.
        """
        sm = self.smoother
        if hasattr(sm, "_fx") and hasattr(sm._fx, attr):
            setattr(sm._fx, attr, value)
        if hasattr(sm, "_fy") and hasattr(sm._fy, attr):
            setattr(sm._fy, attr, value)
        # EMA: nao tem esses atributos — ignora

    def _on_profile_change(self, profile: str) -> None:
        logger.info("PROFILE -> %s", profile)
        self.runtime_settings.apply_profile(profile)
        self._sync_detector_from_settings()
        self._sync_ui_from_settings()

    def _on_toggle(self, key: str, value: bool) -> None:
        if key == "aim":
            self.runtime_settings.set_aim_enabled(value)
            self.gesture_detector.aim_assist_enabled = value
        elif key == "sticky":
            self.runtime_settings.set_sticky_enabled(value)
            self.gesture_detector.sticky_targeting_enabled = value
        elif key == "presentation":
            self._set_presentation_mode(value)
        logger.info("TOGGLE %s = %s", key, value)

    def _set_presentation_mode(self, active: bool) -> None:
        """Liga/desliga modo apresentacao e sincroniza UI + estado interno."""
        self._presentation_mode = active
        self._presentation.reset()
        if active:
            self.cursor.force_release()
        self.ui.set_presentation_active(active)
        msg = (
            f"PRESENTATION_MODE = {active} | "
            f"next='{self._presentation_next_key}' | "
            f"prev='{self._presentation_prev_key}'"
        )
        print(f"[AVM] {msg}", flush=True)
        logger.info(msg)

    def _on_reset(self) -> None:
        logger.info("RESET profile")
        self.runtime_settings.reset_profile()
        self._sync_detector_from_settings()
        self._sync_ui_from_settings()

    def _on_apply_recommended(self) -> None:
        logger.info("APPLY recommended")
        self.runtime_settings.apply_recommended()
        self._sync_detector_from_settings()
        self._sync_ui_from_settings()

    # -----------------------------------------------------------------
    # Sync helpers
    # -----------------------------------------------------------------

    def _sync_detector_from_settings(self) -> None:
        s = self.runtime_settings
        d = self.gesture_detector

        d.dpi_fixed = s.get("dpi_fixed_multiplier")
        d.aim_assist_slowdown_factor = s.get("aim_assist_slowdown_factor")
        d.pinch_threshold = s.get("pinch_distance_threshold")
        d.sticky_friction_factor = s.get("sticky_friction_factor")
        d.aim_assist_enabled = s.aim_enabled
        d.sticky_targeting_enabled = s.sticky_enabled

        # Anchor freeze: so se detector suportar
        if hasattr(d, "anchor_freeze_duration_seconds"):
            d.anchor_freeze_duration_seconds = s.get("anchor_freeze_duration_ms") / 1000.0

        self._apply_smoothing_change("min_cutoff", s.get("one_euro_min_cutoff"))
        self._apply_smoothing_change("beta", s.get("one_euro_beta"))

    _SLIDER_KEYS = (
        "sensitivity", "aim_assist", "smoothness",
        "pinch", "sticky", "anchor_freeze",
    )

    def _sync_ui_from_settings(self) -> None:
        s = self.runtime_settings
        for slider_key in self._SLIDER_KEYS:
            self.ui.set_slider_value(slider_key, s.get_slider_value(slider_key))
            self.ui.set_slider_display(slider_key, s.get_slider_display(slider_key))
        self.ui.set_aim_active(s.aim_enabled)
        self.ui.set_sticky_active(s.sticky_enabled)
        self.ui.set_profile(s.current_profile)
        self.ui.set_doc_rows(s.get_doc_rows())
        self._sync_settings_panel()

    def _sync_settings_panel(self) -> None:
        """Empurra o estado atual pro painel Qt (se disponivel)."""
        panel = getattr(self, "settings_panel", None)
        if panel is None or not panel.available:
            return
        s = self.runtime_settings
        sliders = {k: s.get_slider_value(k) for k in self._SLIDER_KEYS}
        displays = {k: s.get_slider_display(k) for k in self._SLIDER_KEYS}
        panel.sync(sliders, displays, s.aim_enabled, s.sticky_enabled,
                   s.current_profile)

    # -----------------------------------------------------------------
    # Overlay rendering
    # -----------------------------------------------------------------

    def _log_motion_debug(self) -> None:
        """Estado do pipeline de movimento no log, em modo debug.

        Sai por `logger.debug` e so quando MOTION_DEBUG_ENABLED esta
        ligado. Nao salva frames da webcam, nao escreve imagem, nao
        manda nada pra fora da maquina.
        """
        if not getattr(config, "MOTION_DEBUG_ENABLED", False):
            return
        every = max(1, int(getattr(config, "MOTION_DEBUG_EVERY_N_FRAMES", 30)))
        self._motion_debug_tick += 1
        if self._motion_debug_tick % every:
            return
        motion = getattr(self.gesture_detector, "motion", None)
        if motion is None:
            return
        s = motion.debug_snapshot()
        logger.debug(
            "motion | scale raw=%.4f filt=%.4f | gain dist=%.3f total=%.3f | "
            "precision=%.2f sticky=%.2f | bottom=%.2f | speed=%.3f/s | "
            "reanchored=%s",
            s.scale_raw, s.scale_filtered, s.distance_gain, s.total_gain,
            s.precision_weight, s.sticky_weight, s.bottom_strength,
            s.speed, s.reanchored,
        )

    def _draw_overlays(self, frame: np.ndarray, hand, raw_results) -> None:
        # Landmarks (HandTracker.draw aceita raw_results)
        if self.draw_landmarks and raw_results is not None:
            try:
                self.tracker.draw(frame, raw_results)
            except Exception as e:
                logger.debug("tracker.draw falhou: %s", e)

        d = self.gesture_detector
        shape_name = d.current_shape.name if d.current_shape else "UNKNOWN"

        # Properties: getattr defensivo (anchor_frozen pode nao existir)
        anchor_frozen = getattr(d, "anchor_frozen", False)

        self.ui.render(
            frame,
            fps=self._fps.value,  # FPSCounter usa .value, nao .fps
            shape_name=shape_name,
            dpi=d.dpi_multiplier,
            is_dragging=d.is_dragging,
            aim_assist_active=d.aim_assist_active,
            sticky_active=d.sticky_active,
            anchor_frozen=anchor_frozen,
        )

        self._log_motion_debug()

        # Indicador de estado do holograma no preview cv2 — garante
        # que o usuario veja se o toggle por tecla H funcionou, mesmo
        # se a janela transparente nao estiver visivel.
        if self.hologram is not None and self.hologram.available:
            label = "HOLO ON" if self.hologram.enabled else "HOLO OFF"
            color = (0, 0, 255) if self.hologram.enabled else (120, 120, 120)
            try:
                cv2.putText(
                    frame, label, (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA,
                )
            except Exception as e:
                logger.debug("Falha ao renderizar label HOLO: %s", e)

        if self._presentation_mode:
            try:
                self._draw_presentation_overlay(frame)
            except Exception as e:
                logger.debug("Falha ao renderizar overlay apresentacao: %s", e)

    _PRESENTATION_ORANGE = (0, 140, 255)

    def _draw_presentation_overlay(self, frame: np.ndarray) -> None:
        """Banner do modo apresentacao + hint de teclas. Mostra dica de
        correcao apenas quando a mao nao esta detectada ou aberta."""
        h, w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        title = "MODO APRESENTACAO"
        (tw, th), _ = cv2.getTextSize(title, font, 0.7, 2)
        pad = 10
        x0 = (w - tw) // 2 - pad
        y0 = 8
        x1 = x0 + tw + pad * 2
        y1 = y0 + th + pad * 2

        cv2.rectangle(frame, (x0, y0), (x1, y1), self._PRESENTATION_ORANGE, -1)
        cv2.rectangle(frame, (x0, y0), (x1, y1), (255, 255, 255), 2)
        cv2.putText(frame, title, (x0 + pad, y0 + th + pad - 2),
                    font, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

        hint1 = (
            f"mao aberta: meio=neutro  |  "
            f"esq=<-{self._presentation_prev_key}  dir=->{self._presentation_next_key}"
        )
        hint2 = "Z = sair do modo apresentacao"
        cv2.putText(frame, hint1, (x0, y1 + 18),
                    font, 0.40, self._PRESENTATION_ORANGE, 1, cv2.LINE_AA)
        cv2.putText(frame, hint2, (x0, y1 + 34),
                    font, 0.40, self._PRESENTATION_ORANGE, 1, cv2.LINE_AA)

        warning = self._presentation_warning()
        if warning is not None:
            cv2.putText(frame, warning, (x0, y1 + 54),
                        font, 0.45, (80, 80, 255), 1, cv2.LINE_AA)

    def _presentation_warning(self) -> Optional[str]:
        """Dica de correcao pro usuario, ou None quando o gesto esta OK
        (zero clutter visual no caso comum)."""
        dbg = self._presentation.debug
        if not dbg.has_hand:
            return "posicione a mao na frente da camera"
        if not dbg.open_hand:
            return "abra a mao (5 dedos extendidos)"
        return None

    # -----------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            logger.info("Signal %s, parando...", signum)
            self.stop()
        signal.signal(signal.SIGINT, _handler)
        try:
            # SIGTERM nao existe em alguns runtimes do Windows
            signal.signal(signal.SIGTERM, _handler)
        except Exception as e:
            logger.debug("SIGTERM nao registrado: %s", e)

    def _cleanup(self) -> None:
        for label, fn in (
            ("cursor.force_release", self.cursor.force_release),
            ("tracker.close", self.tracker.close),
            ("camera.close", self.camera.close),
        ):
            try:
                fn()
            except Exception as e:
                logger.debug("Cleanup %s falhou: %s", label, e)
        if self.enable_preview:
            try:
                cv2.destroyAllWindows()
                for _ in range(3):
                    cv2.waitKey(1)
            except Exception as e:
                logger.debug("cv2.destroyAllWindows falhou: %s", e)
        if self.hologram is not None:
            try:
                self.hologram.close()
            except Exception as e:
                logger.debug("hologram.close falhou: %s", e)
        if self.settings_panel is not None:
            try:
                self.settings_panel.close()
            except Exception as e:
                logger.debug("settings_panel.close falhou: %s", e)
