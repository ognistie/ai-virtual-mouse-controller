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
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

import config
from core.camera import CameraOpenError, ThreadedCamera
from core.cursor_controller import CursorController
from core.gesture_detector import Gesture, GestureDetector
from core.hand_arbiter import LEFT, RIGHT
from core.hand_gate import HandGate
from core.hand_tracker import HandLandmarks, HandTracker
from core.hologram_overlay import HologramOverlay
from core.perf_telemetry import configure as configure_perf, get_profiler
from core.presentation_controller import PresentationController
from core.runtime_settings import RuntimeSettings
from core.settings_panel import SettingsPanel
from core.smoothing import make_smoother
from core.two_hands import DISPLAY_HOLD_FRAMES, HandChannel, TwoHandsController
from core.ui_overlay import UICallbacks, UIOverlay
from core.utils import FPSCounter, disable_power_throttling


logger = logging.getLogger(__name__)


def _open_camera(camera: ThreadedCamera, default_index: int) -> None:
    """Abre a webcam escolhida; se ela nao abrir (ex.: USB desconectada),
    usa a camera padrao em vez de nao iniciar."""
    try:
        camera.open()
    except CameraOpenError:
        if camera.index == default_index:
            raise
        logger.warning(
            "Camera %d nao abriu; usando a camera %d", camera.index, default_index,
        )
        camera.index = default_index
        camera.open()


@dataclass
class _VisionResult:
    """Saida de um frame processado, pra UI desenhar."""

    frame: np.ndarray
    hand: Optional[HandLandmarks]
    hands: List[HandLandmarks]


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
        self._should_stop = False
        preview_fps = float(getattr(config, "PREVIEW_MAX_FPS", 30.0))
        self._preview_interval = 1.0 / preview_fps if preview_fps > 0.0 else 0.0
        self._preview_shown_t = 0.0
        # Visao (camera -> MediaPipe -> gestos -> cursor) separada da UI.
        # O lock protege o rastreador e os gestos de quem troca de modo.
        self._vision_lock = threading.RLock()
        self._vision_failed = False
        self._latest_cond = threading.Condition()
        self._latest: Optional[_VisionResult] = None
        self._hand_gate = HandGate(
            confirm_frames=getattr(config, "HAND_CONFIRM_FRAMES", 2),
        )
        # Ultima mao vista: a mao 3D e o preview nao piscam em falha curta.
        self._last_hand: Optional[HandLandmarks] = None
        self._hand_lost_frames = 0
        # Controle de frames novos da webcam (ver _tick / _idle_tick).
        self._frame_seq = 0
        self._last_frame_t = time.perf_counter()
        self._camera_lost = False
        self._lost_frame: Optional[np.ndarray] = None
        self._frame_shape: Optional[tuple] = None
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
            dead_zone=getattr(config, "PRESENTATION_DEAD_ZONE", 0.75),
            cooldown_s=getattr(config, "PRESENTATION_COOLDOWN_S", 0.5),
            extension_threshold=getattr(
                config, "PRESENTATION_EXTENSION_THRESHOLD", 0.80
            ),
        )

        # Modo duas maos. O segundo canal so existe depois da primeira
        # ativacao: desligado, o caminho e' o de uma mao.
        self._two_hands_mode: bool = False
        self._two_hands: Optional[TwoHandsController] = None
        self._two_hands_toggle_key: str = (
            getattr(config, "TWO_HANDS_TOGGLE_KEY", "b") or "b"
        ).lower()

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
                opacity=getattr(config, "HOLOGRAM_OPACITY", 0.90),
                target_fps=getattr(config, "HOLOGRAM_FPS", 60),
                bone_color=getattr(config, "HOLOGRAM_COLOR_BONE", "#00d4ff"),
                point_color=getattr(config, "HOLOGRAM_COLOR_POINT", "#e0f7ff"),
                transparent_color=getattr(
                    config, "HOLOGRAM_TRANSPARENT_COLOR", "#010203"
                ),
                view_dorsal=getattr(config, "HOLOGRAM_VIEW_DORSAL", False),
                particles_enabled=getattr(
                    config, "HOLOGRAM_PARTICLES_ENABLED", False,
                ),
                particle_count=getattr(
                    config, "HOLOGRAM_PARTICLE_COUNT", 180,
                ),
                style=getattr(config, "HOLOGRAM_STYLE", "quest"),
                frame_aspect=(
                    float(config.CAMERA_WIDTH) / float(config.CAMERA_HEIGHT)
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

        if getattr(config, "TWO_HANDS_ENABLED", False):
            self._set_two_hands_mode(True)

    # -----------------------------------------------------------------
    # Factory (todas as chamadas verificadas contra core/)
    # -----------------------------------------------------------------

    @classmethod
    def from_config(cls, camera_index: Optional[int] = None) -> "VirtualMouseService":
        # 1. Runtime settings (perfil inicial)
        initial_profile = getattr(config, "PROFILE", "smooth")
        runtime = RuntimeSettings(initial_profile=initial_profile)

        # 2. Camera (API real: fps, .open())
        default_index = config.CAMERA_INDEX
        camera = ThreadedCamera(
            index=default_index if camera_index is None else camera_index,
            width=config.CAMERA_WIDTH,
            height=config.CAMERA_HEIGHT,
            fps=config.CAMERA_FPS_TARGET,
        )

        # 3. Hand tracker
        tracker = HandTracker(
            max_num_hands=config.MAX_NUM_HANDS,
            min_detection_confidence=config.MIN_DETECTION_CONFIDENCE,
            min_tracking_confidence=config.MIN_TRACKING_CONFIDENCE,
            model_complexity=config.MODEL_COMPLEXITY,
        )

        # 4. Gesture detector — usa valores do runtime
        gesture_detector = cls._build_detector(runtime)

        # 5. Cursor e suavizacao
        cursor = cls._build_cursor(runtime)
        smoother = cls._build_smoother(runtime)

        # Camera abre por ultimo: se algo antes falhar, nao fica webcam
        # presa (luz acesa) com o processo saindo por erro.
        try:
            _open_camera(camera, default_index)
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
        except BaseException:
            camera.close()
            tracker.close()
            raise

    @staticmethod
    def _build_cursor(runtime: RuntimeSettings) -> CursorController:
        """Margens assimetricas (X != Y) com fallback isotropico."""
        return CursorController(
            screen_margin_percentage=config.SCREEN_MARGIN_PERCENTAGE,
            screen_margin_x=getattr(config, "SCREEN_MARGIN_X", None),
            screen_margin_top=getattr(config, "SCREEN_MARGIN_TOP", None),
            screen_margin_bottom=getattr(config, "SCREEN_MARGIN_BOTTOM", None),
            dead_zone_pixels=config.DEAD_ZONE_PIXELS,
            failsafe=config.PYAUTOGUI_FAILSAFE,
            pyautogui_pause=config.PYAUTOGUI_PAUSE,
            # Trava no clique vem do perfil (slider do painel).
            click_freeze_seconds=(
                runtime.get("anchor_freeze_duration_ms") / 1000.0
            ),
            drag_precision_factor=getattr(
                config, "DRAG_PRECISION_FACTOR", 0.55,
            ),
            y_bottom_boost_enabled=getattr(
                config, "CURSOR_Y_BOTTOM_BOOST_ENABLED", True,
            ),
            y_bottom_boost_knee=getattr(
                config, "CURSOR_Y_BOTTOM_BOOST_KNEE", 0.82,
            ),
            y_bottom_boost_power=getattr(
                config, "CURSOR_Y_BOTTOM_BOOST_POWER", 1.5,
            ),
            edge_snap_px=getattr(config, "CURSOR_EDGE_SNAP_PX", 0),
            edge_creep_enabled=getattr(
                config, "CURSOR_EDGE_CREEP_ENABLED", False,
            ),
            edge_creep_band_px=getattr(
                config, "CURSOR_EDGE_CREEP_BAND_PX", 110,
            ),
            edge_creep_delay_s=getattr(
                config, "CURSOR_EDGE_CREEP_DELAY_S", 0.15,
            ),
            edge_creep_rate_px_s=getattr(
                config, "CURSOR_EDGE_CREEP_RATE_PX_S", 380.0,
            ),
            bottom_reach_enabled=getattr(
                config, "CURSOR_BOTTOM_REACH_ENABLED", True,
            ),
            bottom_reach_band_px=getattr(
                config, "CURSOR_BOTTOM_REACH_BAND_PX", 120,
            ),
            bottom_reach_max_gain=getattr(
                config, "CURSOR_BOTTOM_REACH_MAX_GAIN", 3.0,
            ),
            bottom_reach_max_extra_px=getattr(
                config, "CURSOR_BOTTOM_REACH_MAX_EXTRA_PX", 60,
            ),
        )

    @staticmethod
    def _build_smoother(runtime: RuntimeSettings):
        """API real: kwargs alpha, freq, min_cutoff, beta, d_cutoff."""
        return make_smoother(
            config.SMOOTHING_STRATEGY,
            alpha=config.SMOOTHING_FACTOR,
            freq=config.ONE_EURO_FREQ,
            min_cutoff=runtime.get("one_euro_min_cutoff"),
            beta=runtime.get("one_euro_beta"),
            d_cutoff=config.ONE_EURO_D_CUTOFF,
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
            followthrough_frames=getattr(
                config, "CURSOR_FOLLOWTHROUGH_FRAMES", 6,
            ),
            followthrough_damping=getattr(
                config, "CURSOR_FOLLOWTHROUGH_DAMPING", 0.82,
            ),
            pinch_release_ratio=getattr(config, "PINCH_RELEASE_RATIO", 1.25),
            pinch_open_fingers_min=getattr(config, "PINCH_OPEN_FINGERS_MIN", 0.82),
            pinch_rearm_seconds=getattr(config, "PINCH_REARM_SECONDS", 0.12),
            dpi_size_smoothing_s=getattr(config, "DPI_SIZE_SMOOTHING_S", 0.6),
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

        if getattr(config, "POWER_THROTTLING_OFF", True) and disable_power_throttling():
            logger.info("Economia de energia do Windows desligada pra este app")
        if getattr(config, "OPENCV_SINGLE_THREAD", True):
            cv2.setNumThreads(1)

        self._last_frame_t = time.perf_counter()
        worker: Optional[threading.Thread] = None
        if getattr(config, "VISION_THREAD_ENABLED", True):
            worker = threading.Thread(
                target=self._vision_loop, name="VisionThread", daemon=True,
            )
            worker.start()
        try:
            while not self._should_stop:
                if worker is not None:
                    self._ui_tick()
                else:
                    self._tick()
        except KeyboardInterrupt:
            pass
        except Exception:
            logger.exception("Erro no loop principal")
            return 1
        finally:
            # Espera a visao parar antes de fechar o rastreador que ela usa.
            self._should_stop = True
            if worker is not None:
                worker.join(timeout=self._VISION_JOIN_S)
            vision_stopped = worker is None or not worker.is_alive()
            if not vision_stopped:
                logger.warning("Thread de visao nao parou a tempo")
            self._cleanup(vision_stopped)
        return 1 if self._vision_failed else 0

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

    # Espera maxima por um frame novo antes de rodar um tick ocioso (so
    # bombeia UI + teclado). ~60 Hz mantem painel/holograma fluidos sem
    # reprocessar frames repetidos da webcam.
    _FRAME_WAIT_S = 1.0 / 60.0
    _VISION_WAIT_S = 0.05
    _VISION_JOIN_S = 2.0
    # Sem frame novo por mais que isso = camera perdida (mesmo se o
    # driver nao reportar erro).
    _CAMERA_LOST_AFTER_S = 2.0

    def _tick(self) -> None:
        """Visao e UI em sequencia (VISION_THREAD_ENABLED = False)."""
        result = self._vision_step(self._FRAME_WAIT_S)
        if result is None:
            self._idle_tick()
        else:
            self._ui_step(result)

    def _vision_loop(self) -> None:
        """Thread da visao: cada frame novo vira gesto e cursor na hora."""
        try:
            while not self._should_stop:
                result = self._vision_step(self._VISION_WAIT_S)
                if result is not None:
                    with self._latest_cond:
                        self._latest = result
                        self._latest_cond.notify_all()
        except Exception:
            logger.exception("Erro na thread de visao")
            self._vision_failed = True
            self._should_stop = True

    def _ui_tick(self) -> None:
        """Thread principal: desenha o resultado mais novo, pulando os velhos."""
        with self._latest_cond:
            if self._latest is None:
                self._latest_cond.wait(self._FRAME_WAIT_S)
            result, self._latest = self._latest, None
        if result is None:
            self._idle_tick()
        else:
            self._ui_step(result)

    def _vision_step(self, timeout: float) -> Optional[_VisionResult]:
        prof = self._perf
        with prof.stage("camera"):
            # Inclui a espera pelo proximo frame da webcam.
            frame, seq = self.camera.read_new(
                self._frame_seq, timeout=timeout, mirror=True,
            )
        if frame is None:
            return None

        self._frame_seq = seq
        self._last_frame_t = time.perf_counter()
        if self._camera_lost:
            self._camera_lost = False
            print("[AVM] Webcam de volta", flush=True)
            logger.info("Webcam de volta")

        self._fps.tick()

        with self._vision_lock:
            hands = self._detect_hands(frame)
            two = self._active_two_hands()
            if two is not None:
                hand = self._tick_two_hands(two, hands)
            else:
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

                if hand is not None:
                    self._last_hand = hand
                elif (
                    self._hand_lost_frames <= DISPLAY_HOLD_FRAMES
                    and self._last_hand is not None
                ):
                    hand = self._last_hand
        return _VisionResult(frame, hand, hands)

    def _reset_hand_tracking_state(self) -> None:
        self._hand_gate.reset()
        self._last_hand = None

    def _detect_hands(self, frame: np.ndarray) -> List[HandLandmarks]:
        """MediaPipe + filtro do que nao e' mao (ver core/hand_gate.py)."""
        with self._perf.stage("inference"):
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
            hands, _ = self.tracker.process_with_raw(rgb)
            return self._hand_gate.filter(hands)

    def _ui_step(self, result: _VisionResult) -> None:
        prof = self._perf
        frame, hand = result.frame, result.hand
        self._sync_frame_aspect(frame)

        with prof.stage("hologram"):
            self._update_hologram(hand)

        # Painel Qt: drena eventos (handle + animacoes). Barato quando
        # nada mudou; None-safe quando indisponivel.
        if self.settings_panel is not None:
            with prof.stage("panel"):
                self.settings_panel.pump()

        if self.enable_preview:
            now = time.perf_counter()
            if now - self._preview_shown_t >= self._preview_interval:
                self._preview_shown_t = now
                with prof.stage("preview"):
                    self._draw_overlays(frame, hand, result.hands)
                    cv2.imshow(self.window_name, frame)
            with prof.stage("waitkey"):
                key = self._read_key()
            if self._handle_key(key, hand):
                return

        prof.tick()

    def _active_two_hands(self) -> Optional[TwoHandsController]:
        return self._two_hands if self._two_hands_mode else None

    def _tick_two_hands(self, two: TwoHandsController, hands):
        """Roda os dois canais; devolve a mao dona (usada pelo preview)."""
        prof = self._perf
        with prof.stage("gesture"):
            results = two.update(hands)
        with prof.stage("events"):
            two.apply(results)
        return two.owner.hand

    def _read_key(self) -> int:
        return cv2.pollKey() if self._use_pollkey else cv2.waitKeyEx(1)

    def _handle_key(self, key: int, hand) -> bool:
        """Trata uma tecla do preview. True = tecla consumida (o tick
        termina sem registrar telemetria, como antes)."""
        if key == 27:  # ESC
            self.stop()
            return True
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
                return True
            if key_masked in (ord('t'), ord('T')):
                self._apply_always_on_top(on=not self._always_on_top)
                logger.info("ALWAYS_ON_TOP = %s", self._always_on_top)
                return True
            pkey = self._presentation_toggle_key
            if key_masked in (ord(pkey), ord(pkey.upper())):
                self._set_presentation_mode(not self._presentation_mode)
                return True
            tkey = self._two_hands_toggle_key
            if key_masked in (ord(tkey), ord(tkey.upper())):
                self._set_two_hands_mode(not self._two_hands_mode)
                return True
            hkey = self._hologram_toggle_key
            if key_masked in (ord(hkey), ord(hkey.upper())):
                if self.hologram is not None and self.hologram.available:
                    new_state = self.hologram.toggle()
                    # Reflete no painel Qt (alternado pela tecla H).
                    self._sync_settings_panel()
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
                return True
            # Demais teclas do overlay OpenCV (so agem com o painel
            # legado visivel — inertes no fluxo normal com painel Qt)
            self.ui.handle_key(key_masked)
        return False

    def _sync_frame_aspect(self, frame: np.ndarray) -> None:
        """A webcam pode entregar resolucao diferente da pedida: o
        holograma precisa do aspecto REAL pra mao nao sair achatada."""
        shape = frame.shape[:2]
        if shape == self._frame_shape:
            return
        self._frame_shape = shape
        if self.hologram is not None and shape[0] > 0:
            try:
                self.hologram.set_frame_aspect(shape[1] / shape[0])
            except Exception as e:
                logger.debug("set_frame_aspect falhou: %s", e)

    def _idle_tick(self) -> None:
        """Tick sem frame novo: mantem painel, holograma e teclado vivos.

        Se a webcam sumiu (desconectada / tomada por outro app), solta
        qualquer botao pressionado, zera o estado dos gestos e mostra o
        aviso no preview enquanto o thread da camera tenta reconectar.
        """
        silent_for = time.perf_counter() - self._last_frame_t
        if not self._camera_lost and (
            self.camera.is_stalled or silent_for > self._CAMERA_LOST_AFTER_S
        ):
            self._on_camera_lost()

        if self._camera_lost:
            self._update_hologram(None)
        elif self.hologram is not None and self.hologram.available:
            self.hologram.pump()
        if self.settings_panel is not None:
            self.settings_panel.pump()

        if self.enable_preview:
            if self._camera_lost:
                cv2.imshow(self.window_name, self._camera_lost_frame())
            self._handle_key(self._read_key(), None)

    def _on_camera_lost(self) -> None:
        self._camera_lost = True
        msg = "Webcam sem imagem — tentando reconectar..."
        print(f"[AVM] {msg}", flush=True)
        logger.warning(msg)
        # Nunca deixar o botao do mouse preso num drag sem camera.
        with self._vision_lock:
            self.cursor.force_release()
            self.gesture_detector.reset()
            self.smoother.reset()
            self._presentation.reset()
            self._reset_hand_tracking_state()
            if self._two_hands is not None:
                self._two_hands.reset()

    def _camera_lost_frame(self) -> np.ndarray:
        if self._lost_frame is None:
            h = int(getattr(config, "CAMERA_HEIGHT", 540))
            w = int(getattr(config, "CAMERA_WIDTH", 960))
            img = np.zeros((h, w, 3), dtype=np.uint8)
            font = cv2.FONT_HERSHEY_SIMPLEX
            for i, line in enumerate((
                "Webcam sem imagem",
                "Reconectando automaticamente...",
                "Verifique o cabo ou feche apps que usam a camera.",
            )):
                scale = 1.0 if i == 0 else 0.6
                (tw, _), _ = cv2.getTextSize(line, font, scale, 2)
                cv2.putText(
                    img, line, ((w - tw) // 2, h // 2 - 30 + i * 36),
                    font, scale, (200, 200, 200), 2 if i == 0 else 1,
                    cv2.LINE_AA,
                )
            self._lost_frame = img
        return self._lost_frame

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
            two = self._active_two_hands()
            cursor = two.owner.cursor if two is not None else self.cursor
            sx, sy = cursor.last_position
            if sx is None or sy is None:
                try:
                    import pyautogui
                    sx, sy = pyautogui.position()
                except Exception as e:
                    logger.debug("pyautogui.position() falhou: %s", e)
                    sx = self.cursor.screen_width // 2
                    sy = self.cursor.screen_height // 2
            h.update_cursor(sx, sy)
            if two is not None:
                h.update_poses(two.poses(), two.owner_index)
            else:
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
        """Aplica setting individual no detector/smoother/cursor sem
        restart.
        """
        if key == "one_euro_min_cutoff":
            self._apply_smoothing_change("min_cutoff", value)
        elif key == "one_euro_beta":
            self._apply_smoothing_change("beta", value)

        for d, _, cursor in self._pipelines():
            if key == "dpi_fixed_multiplier":
                d.dpi_fixed = value
            elif key == "aim_assist_slowdown_factor":
                d.aim_assist_slowdown_factor = value
            elif key == "pinch_distance_threshold":
                d.pinch_threshold = value
            elif key == "sticky_friction_factor":
                d.sticky_friction_factor = value
            elif key == "anchor_freeze_duration_ms":
                cursor.click_freeze_seconds = max(0.0, value / 1000.0)

    def _apply_smoothing_change(self, attr: str, value: float) -> None:
        """Atualiza OneEuroSmoother2D ao vivo.

        OneEuroSmoother2D usa atributos internos _fx e _fy (nao _x/_y).
        Cada um e um _OneEuroAxis com .min_cutoff e .beta como campos.
        """
        for _, sm, _ in self._pipelines():
            if hasattr(sm, "_fx") and hasattr(sm._fx, attr):
                setattr(sm._fx, attr, value)
            if hasattr(sm, "_fy") and hasattr(sm._fy, attr):
                setattr(sm._fy, attr, value)
        # EMA: nao tem esses atributos — ignora

    def _pipelines(self):
        """(detector, suavizador, cursor) de cada mao ja criada."""
        out = [(self.gesture_detector, self.smoother, self.cursor)]
        if self._two_hands is not None:
            left = self._two_hands.left
            out.append((left.detector, left.smoother, left.cursor))
        return out

    def _on_profile_change(self, profile: str) -> None:
        logger.info("PROFILE -> %s", profile)
        self.runtime_settings.apply_profile(profile)
        self._sync_detector_from_settings()
        self._sync_ui_from_settings()

    def _on_toggle(self, key: str, value: bool) -> None:
        if key == "aim":
            self.runtime_settings.set_aim_enabled(value)
            for d, _, _ in self._pipelines():
                d.aim_assist_enabled = value
        elif key == "sticky":
            self.runtime_settings.set_sticky_enabled(value)
            for d, _, _ in self._pipelines():
                d.sticky_targeting_enabled = value
        elif key == "presentation":
            self._set_presentation_mode(value)
        elif key == "two_hands":
            self._set_two_hands_mode(value)
        elif key == "hologram":
            self._set_hologram_enabled(value)
        logger.info("TOGGLE %s = %s", key, value)

    def _set_hologram_enabled(self, active: bool) -> None:
        """Liga/desliga o holograma da mao e sincroniza o painel.

        No-op seguro se o holograma nao estiver disponivel (plataforma
        sem GPU/driver) — o resto do programa nao depende dele.
        """
        active = bool(active)
        if self.hologram is not None and self.hologram.available:
            self.hologram.set_enabled(active)
            logger.info("HOLOGRAM = %s (painel)", active)
        self._sync_settings_panel()

    def _set_presentation_mode(self, active: bool) -> None:
        """Liga/desliga modo apresentacao e sincroniza UI + estado interno."""
        with self._vision_lock:
            if active and self._two_hands_mode:
                self._set_two_hands_mode(False)
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
        # Reflete no painel Qt o estado atual (util quando alternado pela
        # tecla Z, nao pelo proprio painel).
        self._sync_settings_panel()

    def _set_two_hands_mode(self, active: bool) -> None:
        """Liga/desliga o modo duas maos. Recria o rastreador (o `num_hands`
        e' fixo na criacao); se falhar ao ligar, segue com uma mao."""
        active = bool(active)
        if active == self._two_hands_mode:
            self._sync_settings_panel()
            return
        if active and self._presentation_mode:
            self._set_presentation_mode(False)
        with self._vision_lock:
            try:
                self.tracker.set_max_hands(2 if active else config.MAX_NUM_HANDS)
            except Exception as e:
                msg = f"Modo duas maos: falha ao recriar o rastreador ({e})"
                print(f"[AVM] {msg}", flush=True)
                logger.warning(msg)
                if active:
                    self._sync_settings_panel()
                    return
            if active and self._two_hands is None:
                self._two_hands = self._build_two_hands()
            if self._two_hands is not None:
                self._two_hands.reset()
            self._reset_hand_tracking_state()
            self._two_hands_mode = active
        msg = f"TWO_HANDS_MODE = {active}"
        print(f"[AVM] {msg}", flush=True)
        logger.info(msg)
        self._sync_settings_panel()

    def _build_two_hands(self) -> TwoHandsController:
        """A direita reaproveita os objetos atuais; a esquerda nasce com os
        ajustes vigentes (o runtime ja reflete o painel)."""
        runtime = self.runtime_settings
        return TwoHandsController(
            right=HandChannel(
                RIGHT, self.gesture_detector, self.smoother, self.cursor,
            ),
            left=HandChannel(
                LEFT,
                self._build_detector(runtime),
                self._build_smoother(runtime),
                self._build_cursor(runtime),
            ),
        )

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

        for d, _, cursor in self._pipelines():
            d.dpi_fixed = s.get("dpi_fixed_multiplier")
            d.aim_assist_slowdown_factor = s.get("aim_assist_slowdown_factor")
            d.pinch_threshold = s.get("pinch_distance_threshold")
            d.sticky_friction_factor = s.get("sticky_friction_factor")
            d.aim_assist_enabled = s.aim_enabled
            d.sticky_targeting_enabled = s.sticky_enabled
            cursor.click_freeze_seconds = max(
                0.0, s.get("anchor_freeze_duration_ms") / 1000.0,
            )

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
        hologram_on = (
            self.hologram is not None
            and self.hologram.available
            and self.hologram.enabled
        )
        panel.sync(sliders, displays, s.aim_enabled, s.sticky_enabled,
                   s.current_profile, self._presentation_mode, hologram_on,
                   self._two_hands_mode)

    # -----------------------------------------------------------------
    # Overlay rendering
    # -----------------------------------------------------------------

    def _draw_overlays(self, frame: np.ndarray, hand, raw_results) -> None:
        # Landmarks (HandTracker.draw aceita raw_results)
        if self.draw_landmarks and raw_results is not None:
            try:
                self.tracker.draw(frame, raw_results)
            except Exception as e:
                logger.debug("tracker.draw falhou: %s", e)

        two = self._active_two_hands()
        d = two.owner.detector if two is not None else self.gesture_detector
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

        if two is not None:
            try:
                self._draw_two_hands_overlay(frame, two)
            except Exception as e:
                logger.debug("Falha ao renderizar overlay duas maos: %s", e)

    def _draw_two_hands_overlay(
        self, frame: np.ndarray, two: TwoHandsController,
    ) -> None:
        """Marca cada mao (E/D) e qual e' a dona do cursor."""
        h, w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        for channel, letter in ((two.right, "D"), (two.left, "E")):
            if channel.hand is None:
                continue
            x, y = channel.hand.landmarks[0][:2]
            color = (0, 255, 0) if channel is two.owner else (200, 200, 200)
            cv2.putText(
                frame, letter, (int(x * w), int(y * h)),
                font, 0.9, color, 2, cv2.LINE_AA,
            )
        cv2.putText(
            frame, "DUAS MAOS", (8, h - 12),
            font, 0.6, (0, 255, 0), 2, cv2.LINE_AA,
        )

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

    def _cleanup(self, vision_stopped: bool = True) -> None:
        """`vision_stopped` False: a thread de visao ainda pode estar usando
        rastreador e camera, entao eles ficam pro fim do processo."""
        if self._two_hands is not None:
            self._two_hands.left.cursor.force_release()
        steps = [("cursor.force_release", self.cursor.force_release)]
        if vision_stopped:
            steps += [
                ("tracker.close", self.tracker.close),
                ("camera.close", self.camera.close),
            ]
        for label, fn in steps:
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
