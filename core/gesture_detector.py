"""
core.gesture_detector
=====================

State machine de gestos da mao. Recebe HandLandmarks por frame, retorna
GestureEvents (MOVE, CLICK, DOUBLE_CLICK, RIGHT_CLICK, DRAG_START,
DRAG_END, PAUSE).

Modelo de clique: PRESS-TO-CLICK. CLICK e RIGHT_CLICK disparam no
momento em que os dedos se ENCOSTAM (edge detection no raw_shape), nao
quando soltam. Sensacao de botao fisico — feedback imediato.

Gestos:
- 🖐️  OPEN_HAND (4 dedos up)        -> MOVE
- 🤏  PINCH     (polegar+indicador)  -> CLICK no encostar
- 🤏  PINCH 1.5s+                     -> DRAG
- 🤞  PINCH_MIDDLE (polegar+medio)    -> RIGHT_CLICK no encostar
- ✌️  PEACE                            -> DOUBLE_CLICK ao soltar
- ✊  FIST                             -> congelado

Recursos: histerese (debounce/exit frames), threshold adaptativo de
pinch com piso minimo, cooldown anti-bounce, follow-through quando a
mao sai do FOV.

A MATEMATICA DE MOVIMENTO nao mora aqui. Ganho adaptativo por
distancia, precisao continua (aim assist + sticky), curva balistica e
assistencia da borda inferior vivem em `core/cursor_motion.py` — modulo
puro, com `dt` injetado. Este arquivo classifica gestos, calcula a
ancora UMA vez por frame e alimenta aquele pipeline.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, List, Optional, Tuple

from .cursor_motion import CursorMotion, MotionConfig, clamp as _clamp
from .finger_posture import (
    FINGER_CHAIN_INDEX,
    FINGER_CHAIN_MIDDLE,
    finger_extension,
)
from .hand_anchor import RobustHandAnchor
from .hand_tracker import HandLandmarks


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Landmark indices
# ---------------------------------------------------------------------
LM_WRIST = 0
LM_THUMB_TIP = 4
LM_INDEX_TIP = 8
LM_MIDDLE_TIP = 12
LM_MIDDLE_MCP = 9

# Sentinel: ancora o cursor no MIDPOINT do pinch (polegar + indicador).
# Quando usado como CURSOR_ANCHOR_LANDMARK, get_anchor_position retorna
# (lm[4] + lm[8]) / 2 — mesmo ponto onde o holograma desenha a junção
# do pinch. Resultado: ao fechar o pinch os dedos se encontram exatamente
# sobre o cursor (imersão visual no overlay holográfico).
LM_PINCH_MIDPOINT = -1

# Sentinel: ancora ROBUSTA — combinacao ponderada de TODOS os landmarks
# visiveis e estaveis (ver core/hand_anchor.py). Resistente a oclusao:
# quando a webcam corta a palma, a mao esta de perfil, ou um landmark
# critico falha, o peso migra organicamente pros pontos ainda confiaveis,
# sem saltos no cursor. RECOMENDADO como default — preserva a UX
# (cursor segue a mao) sem o ponto unico de falha.
LM_ROBUST_HAND = -2


# ---------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------

class Gesture(Enum):
    NONE = "none"
    MOVE = "move"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    DRAG_START = "drag_start"
    DRAG_END = "drag_end"
    PAUSE = "pause"
    NEXT_SLIDE = "next_slide"
    PREV_SLIDE = "prev_slide"


class HandShape(Enum):
    UNKNOWN = "unknown"
    OPEN_HAND = "open_hand"
    PINCH = "pinch"
    PINCH_MIDDLE = "pinch_middle"
    PEACE = "peace"
    FIST = "fist"
    OTHER = "other"


@dataclass
class GestureEvent:
    gesture: Gesture
    position: Optional[Tuple[float, float]] = None
    timestamp: float = field(default_factory=time.perf_counter)


# ---------------------------------------------------------------------
# Helpers geometricos
# ---------------------------------------------------------------------

def _distance_2d(p1, p2) -> float:
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]
    return math.sqrt(dx * dx + dy * dy)


# Peso do eixo Z na distancia de pinch. MediaPipe sub-estima z em
# relacao a x,y (~50%), entao z=1.0 ja e' conservador. Use < 1 se
# o tracker do seu setup vier muito noisy em z.
_PINCH_Z_WEIGHT: float = 1.0


def _pinch_distance(p1, p2, z_weight: float = _PINCH_Z_WEIGHT) -> float:
    """Distancia 3D entre landmarks (orientation-invariant).

    A distancia 2D no plano da imagem subestima a separacao quando a mao
    esta de perfil — polegar e indicador podem aparecer sobrepostos no
    eixo de profundidade, gerando falso PINCH. Adicionando z na metrica,
    o sinal volta a refletir a separacao real dos dedos independente
    da orientacao da palma.
    """
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]
    dz = (p1[2] - p2[2]) * z_weight
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def compute_hand_size(hand: HandLandmarks) -> float:
    # 2D de proposito: wrist→middle_MCP no plano da imagem e' robusto
    # a rotacao da palma (sempre vertical no frame), serve como escala
    # estavel pra adaptive thresholds. Trocar pra 3D introduziria
    # ruido do z do MediaPipe nessa escala.
    lm = hand.landmarks
    return _distance_2d(lm[LM_WRIST], lm[LM_MIDDLE_MCP])


def get_anchor_position(
    hand: HandLandmarks,
    anchor_landmark: int = LM_MIDDLE_MCP,
    *,
    robust_anchor: "Optional[RobustHandAnchor]" = None,
):
    """
    Calcula a coordenada normalizada que o cursor deve seguir.

    Modos (selecionados via `anchor_landmark`):
      LM_ROBUST_HAND (-2)   ancora ponderada usando todos os 21 landmarks
                            (resistente a oclusao). Exige `robust_anchor`
                            injetado — sem ele, faz fallback p/ pinch midpoint.
      LM_PINCH_MIDPOINT (-1) midpoint(polegar 4, indicador 8). Alinha com
                            o ponto onde o holograma fecha o pinch.
      0..20                  landmark unico do MediaPipe Hands.

    A funcao continua PURA: estado fica encapsulado no `robust_anchor`
    injetado, nao em variaveis globais.
    """
    # Modo robusto: delega pra instancia stateful injetada.
    if anchor_landmark == LM_ROBUST_HAND:
        if robust_anchor is not None:
            res = robust_anchor.compute(hand.landmarks)
            return (res.x, res.y)
        # Sem instancia disponivel: fallback consistente p/ pinch midpoint
        # (segundo melhor anchor — alinha com o holograma).
        anchor_landmark = LM_PINCH_MIDPOINT

    # Modo especial: ancora no midpoint do pinch (polegar 4 + indicador 8).
    # Alinha 1-pra-1 com a ancora do mesh holografico (_PINCH_ANCHOR em
    # hand_mesh.py), garantindo que o ponto onde os dedos se tocam no
    # overlay coincida exatamente com o cursor na tela.
    if anchor_landmark == LM_PINCH_MIDPOINT:
        lm_t = hand.landmarks[LM_THUMB_TIP]
        lm_i = hand.landmarks[LM_INDEX_TIP]
        return ((lm_t[0] + lm_i[0]) * 0.5, (lm_t[1] + lm_i[1]) * 0.5)
    lm = hand.landmarks[anchor_landmark]
    return (lm[0], lm[1])


def compute_dpi_multiplier(hand_size, reference_size, min_mult, max_mult, enabled=True):
    """DEPRECATED — nao e' mais usada pelo pipeline de movimento.

    Mantida apenas por compatibilidade de API. A relacao aqui e' o
    INVERSO da UX desejada (mao perto = multiplicador alto), o que era a
    causa raiz do cursor "morrer" com a mao longe da webcam. O ganho
    adaptativo correto vive em ``core.cursor_motion.distance_gain``, que
    e' monotonico e inverso na escala aparente da palma.
    """
    if not enabled or reference_size <= 0:
        return 1.0
    ratio = hand_size / reference_size
    if ratio >= 1.0:
        t = min(1.0, (ratio - 1.0) / 0.5)
        return 1.0 + (max_mult - 1.0) * t
    else:
        t = max(0.0, (ratio - 0.5) / 0.5)
        return min_mult + (1.0 - min_mult) * t


def apply_dpi_to_position(pos, dpi_mult, center=(0.5, 0.5)):
    """DEPRECATED — nao e' mais usada pelo pipeline de movimento.

    Escalar uma POSICAO ABSOLUTA em torno do centro faz o cursor se
    deslocar sempre que o ganho oscila, mesmo com a mao parada. O
    pipeline atual integra DELTAS (ver ``core.cursor_motion``), onde
    mudanca de ganho so afeta movimentos futuros.
    """
    if dpi_mult == 1.0:
        return pos
    dx = (pos[0] - center[0]) * dpi_mult
    dy = (pos[1] - center[1]) * dpi_mult
    return (
        max(0.0, min(1.0, center[0] + dx)),
        max(0.0, min(1.0, center[1] + dy)),
    )


def classify_shape(
    hand: HandLandmarks,
    pinch_threshold: float,
    pinch_middle_threshold: float = 0.0,
    pinch_middle_index_guard: float = 0.0,
    *,
    pinch_middle_index_extension_min: float = 0.88,
    pinch_middle_middle_extension_max: float = 0.92,
) -> HandShape:
    """
    Classifica a forma da mao em uma das HandShape.

    PRIORIDADE de classificacao:
    1. PINCH_MIDDLE (clique direito) — polegar+medio juntos E indicador
       claramente ESTENDIDO E medio claramente CURVADO (postura intencional)
    2. PINCH (clique normal) — polegar+indicador juntos
    3. FIST / OPEN_HAND / PEACE / OTHER

    Por que postura anatomica?
    O classificador anterior decidia PINCH_MIDDLE so com distancias
    par-a-par. Por acoplamento de tendoes flexores (FDP), o dedo medio
    cai naturalmente 30-50% quando o usuario faz PINCH (polegar+indicador).
    Isso fazia o medio chegar perto do polegar inadvertidamente -> falso
    positivo de RIGHT_CLICK durante CLICK.

    Fix: validamos a POSTURA dos dedos via `finger_extension` (score
    rotation-invariant). PINCH_MIDDLE so dispara quando o usuario fez a
    postura intencional:
      - Indicador EXTENDIDO (>= 0.88 ratio): claramente apontando
      - Medio CURVADO (<= 0.92 ratio): foi deliberadamente ate o polegar
    Durante um PINCH natural, o indicador estaria curvado (score ~0.65-0.80)
    e a porta fica fechada — sem alucinacao.

    Args:
        pinch_middle_threshold: 0 desabilita deteccao de PINCH_MIDDLE,
                                preservando o comportamento legado.
        pinch_middle_index_guard: distancia minima polegar→indicador para
                                  reconhecer PINCH_MIDDLE (evita falsos
                                  positivos com a pinca normal).
        pinch_middle_index_extension_min: score minimo de extensao do
                                          indicador para validar postura
                                          (default 0.88 = quase reto).
        pinch_middle_middle_extension_max: score maximo de extensao do
                                           medio (default 0.92 = nao pode
                                           estar reto pra ser "tocando" o
                                           polegar de forma intencional).
    """
    lm = hand.landmarks
    # 3D pinch distances — orientation-invariant. Distancia 2D no plano
    # da imagem subestima a separacao quando a mao esta de lado.
    dist_thumb_index = _pinch_distance(lm[LM_THUMB_TIP], lm[LM_INDEX_TIP])

    # PINCH_MIDDLE: ordem de portas (cheap → caro):
    # 1) distancia polegar↔medio abaixo do threshold (raw signal)
    # 2) postura anatomica do indicador (estendido)
    # 3) postura anatomica do medio (curvado, foi ate o polegar)
    # 4) guard de distancia (indicador afastado OU disambiguacao apertada)
    if pinch_middle_threshold > 0:
        dist_thumb_middle = _pinch_distance(lm[LM_THUMB_TIP], lm[LM_MIDDLE_TIP])
        if dist_thumb_middle < pinch_middle_threshold:
            # Postura: indicador claramente estendido E medio curvado.
            # Esses dois gates juntos eliminam o falso positivo do
            # acoplamento de tendoes durante PINCH normal.
            index_ext = finger_extension(lm, FINGER_CHAIN_INDEX)
            middle_ext = finger_extension(lm, FINGER_CHAIN_MIDDLE)
            posture_intentional = (
                index_ext >= pinch_middle_index_extension_min
                and middle_ext <= pinch_middle_middle_extension_max
            )

            if posture_intentional:
                # v6.9.1.1 — disambiguacao por proximidade relativa:
                # quando o usuario transita PINCH -> PINCH_MIDDLE, o
                # indicador ainda fica naturalmente perto do polegar.
                # Apertamos 0.70 -> 0.55 (margem maior pra exigir
                # intencao real) — agora protegido pela postura, podemos
                # ser mais permissivos sem regredir false positives.
                middle_clearly_closer = (
                    dist_thumb_middle < dist_thumb_index * 0.55
                )
                if (
                    dist_thumb_index > pinch_middle_index_guard
                    or middle_clearly_closer
                ):
                    return HandShape.PINCH_MIDDLE

    if dist_thumb_index < pinch_threshold:
        return HandShape.PINCH

    fingers = hand.fingers_up()
    _thumb, index, middle, ring, pinky = fingers

    if not index and not middle and not ring and not pinky:
        return HandShape.FIST

    if index and middle and ring and pinky:
        return HandShape.OPEN_HAND

    if index and middle and not ring and not pinky:
        return HandShape.PEACE

    return HandShape.OTHER


# ---------------------------------------------------------------------
# CURVA BALLISTICA SUAVIZADA
# ---------------------------------------------------------------------

def apply_velocity_curve_smooth(
    delta: Tuple[float, float],
    velocity: float,
    tremor_threshold: float,
    precision_zone: float,
    fast_threshold: float,
    slow_factor: float,
    fast_factor: float,
) -> Tuple[float, float]:
    """
    Curva ballistica suavizada usando smoothstep — sem saltos.

    velocity < tremor → (0, 0)  [anti-tremor]
    velocity entre tremor e precision → smoothstep(slow_factor → 1.0)
    velocity entre precision e fast → 1.0 (zona neutra)
    velocity entre fast e 2*fast → smoothstep(1.0 → fast_factor)
    velocity >= 2*fast → fast_factor
    """
    if velocity < tremor_threshold:
        return (0.0, 0.0)

    factor: float

    if velocity < precision_zone:
        t = (velocity - tremor_threshold) / max(1e-9, precision_zone - tremor_threshold)
        s = 3 * t * t - 2 * t * t * t  # smoothstep
        factor = slow_factor + (1.0 - slow_factor) * s

    elif velocity < fast_threshold:
        factor = 1.0

    else:
        upper = fast_threshold * 2.0
        t = min(1.0, (velocity - fast_threshold) / max(1e-9, upper - fast_threshold))
        s = 3 * t * t - 2 * t * t * t
        factor = 1.0 + (fast_factor - 1.0) * s

    return (delta[0] * factor, delta[1] * factor)


# ---------------------------------------------------------------------
# ---------------------------------------------------------------------
# Detector principal
# ---------------------------------------------------------------------

class GestureDetector:
    """
    State machine de gestos.

    Combina:
    - Press-to-click para CLICK e RIGHT_CLICK
    - Sticky targeting + aim assist com pre-ativacao
    - Curva ballistica suavizada
    - Threshold de pinca adaptativo com piso minimo
    - Histerese de gestos com debounce/exit frames separados
    """

    def __init__(
        self,
        anchor_landmark: int = LM_MIDDLE_MCP,
        pinch_threshold: float = 0.075,
        pinch_threshold_floor: float = 0.045,
        pinch_middle_threshold: float = 0.075,
        pinch_middle_index_guard: float = 0.110,
        pinch_min_hold_seconds: float = 0.015,
        pinch_adaptive: bool = True,
        dpi_adaptive_enabled: bool = True,
        hand_size_reference: float = 0.22,
        dpi_min: float = 0.7,
        dpi_max: float = 1.4,
        dpi_fixed: float = 0.85,
        drag_hold_seconds: float = 3.0,
        click_cooldown: float = 0.25,
        double_click_cooldown: float = 2.5,
        double_click_window: float = 0.35,
        debounce_frames: int = 2,
        exit_frames: int = 3,
        position_hold_frames: int = 3,
        # Aim assist
        aim_assist_enabled: bool = True,
        aim_assist_slowdown_factor: float = 0.40,
        aim_assist_trigger_shapes: Tuple[str, ...] = ("peace", "pinch"),
        aim_assist_pre_activation: bool = True,
        aim_assist_pre_pinch_threshold: float = 0.12,
        aim_assist_holdover_seconds: float = 0.30,
        # Sticky targeting
        sticky_targeting_enabled: bool = True,
        sticky_deceleration_threshold: float = 0.7,
        sticky_friction_factor: float = 0.75,
        sticky_min_velocity: float = 0.005,
        # Curva ballistica
        velocity_curve_enabled: bool = True,
        velocity_tremor_threshold: float = 0.003,
        velocity_precision_zone: float = 0.025,
        velocity_fast_threshold: float = 0.12,
        velocity_slow_factor: float = 0.55,
        velocity_fast_factor: float = 1.15,
        # Pinca dual (default desabilitada)
        pinch_dual_detection: bool = False,
        pinch_velocity_threshold: float = 0.008,
        # Postura anatomica pra validar PINCH_MIDDLE — anti acoplamento
        # de tendoes (medio caindo junto durante PINCH normal).
        pinch_middle_index_extension_min: float = 0.88,
        pinch_middle_middle_extension_max: float = 0.92,
        # Cinematic follow-through quando a mao sai do FOV
        followthrough_frames: int = 6,
        followthrough_damping: float = 0.82,
        # Movimento adaptativo (core/cursor_motion.py). Todos opcionais:
        # os defaults reproduzem os do MotionConfig.
        distance_gain_enabled: bool = True,
        distance_gain_far: float = 1.40,
        distance_gain_near: float = 0.75,
        distance_scale_far_ratio: float = 0.60,
        distance_scale_near_ratio: float = 1.55,
        distance_scale_filter_hz: float = 1.2,
        distance_gain_rate_per_second: float = 1.5,
        precision_attack_seconds: float = 0.10,
        precision_release_seconds: float = 0.22,
        bottom_assist_enabled: bool = True,
        bottom_assist_edge: float = 0.86,
        bottom_assist_band: float = 0.22,
        bottom_assist_max_gain: float = 3.2,
        bottom_assist_max_extra_rate: float = 0.60,
        bottom_assist_intent_speed: float = 0.12,
    ) -> None:
        self.anchor_landmark = anchor_landmark
        self.pinch_threshold = pinch_threshold
        self.pinch_threshold_floor = pinch_threshold_floor
        self.pinch_middle_threshold = pinch_middle_threshold
        self.pinch_middle_index_guard = pinch_middle_index_guard
        self.pinch_min_hold_seconds = pinch_min_hold_seconds
        self.pinch_adaptive = pinch_adaptive
        self.dpi_adaptive_enabled = dpi_adaptive_enabled
        self.hand_size_reference = hand_size_reference
        self.dpi_min = dpi_min
        self.dpi_max = dpi_max
        self.dpi_fixed = dpi_fixed
        self.drag_hold_seconds = drag_hold_seconds
        self.click_cooldown = click_cooldown
        self.double_click_cooldown = double_click_cooldown
        self.debounce_frames = max(1, debounce_frames)
        self.exit_frames = max(self.debounce_frames, exit_frames)
        self.position_hold_frames = max(0, position_hold_frames)

        self.aim_assist_enabled = aim_assist_enabled
        self._aim_assist_slowdown_factor = aim_assist_slowdown_factor
        self.aim_assist_trigger_shapes = tuple(aim_assist_trigger_shapes)
        self.aim_assist_pre_activation = aim_assist_pre_activation
        self.aim_assist_pre_pinch_threshold = aim_assist_pre_pinch_threshold
        # Mantido por compatibilidade de API/config. O holdover por timer
        # foi substituido pela liberacao progressiva do envelope de
        # precisao (release), que nao troca o fator em degrau.
        self.aim_assist_holdover_seconds = aim_assist_holdover_seconds

        self._sticky_targeting_enabled = sticky_targeting_enabled
        self.sticky_deceleration_threshold = sticky_deceleration_threshold
        self._sticky_friction_factor = sticky_friction_factor
        self.sticky_min_velocity = sticky_min_velocity

        # Parametros do pipeline de movimento adaptativo.
        self._distance_gain_enabled = distance_gain_enabled
        self._distance_gain_far = distance_gain_far
        self._distance_gain_near = distance_gain_near
        self._distance_scale_far_ratio = distance_scale_far_ratio
        self._distance_scale_near_ratio = distance_scale_near_ratio
        self._distance_scale_filter_hz = distance_scale_filter_hz
        self._distance_gain_rate_per_second = distance_gain_rate_per_second
        self._precision_attack_seconds = precision_attack_seconds
        self._precision_release_seconds = precision_release_seconds
        self._bottom_assist_enabled = bottom_assist_enabled
        self._bottom_assist_edge = bottom_assist_edge
        self._bottom_assist_band = bottom_assist_band
        self._bottom_assist_max_gain = bottom_assist_max_gain
        self._bottom_assist_max_extra_rate = bottom_assist_max_extra_rate
        self._bottom_assist_intent_speed = bottom_assist_intent_speed

        self.velocity_curve_enabled = velocity_curve_enabled
        self.velocity_tremor_threshold = velocity_tremor_threshold
        self.velocity_precision_zone = velocity_precision_zone
        self.velocity_fast_threshold = velocity_fast_threshold
        self.velocity_slow_factor = velocity_slow_factor
        self.velocity_fast_factor = velocity_fast_factor

        self.pinch_dual_detection = pinch_dual_detection
        self.pinch_velocity_threshold = pinch_velocity_threshold

        self.pinch_middle_index_extension_min = float(
            pinch_middle_index_extension_min
        )
        self.pinch_middle_middle_extension_max = float(
            pinch_middle_middle_extension_max
        )

        # Ancora robusta da mao — instanciada eagermente porem barata.
        # So consultada quando anchor_landmark == LM_ROBUST_HAND. Manter
        # como atributo permite reset() pelo proprio detector quando
        # convem (mao perdida, modo trocado em runtime, etc).
        self._robust_anchor: RobustHandAnchor = RobustHandAnchor()

        # Pipeline de movimento adaptativo (ganho por distancia, precisao
        # continua, assistencia de borda). Toda a matematica de movimento
        # mora la — aqui so classificamos gestos e alimentamos o dt.
        self._motion: CursorMotion = CursorMotion(self._build_motion_config())
        self._last_frame_t: Optional[float] = None

        # Estado interno
        self._candidate_shape: HandShape = HandShape.UNKNOWN
        self._candidate_count: int = 0
        self._confirmed_shape: HandShape = HandShape.UNKNOWN

        self._pinch_started_at: Optional[float] = None
        self._drag_active: bool = False
        self._last_click_time: float = 0.0
        self._last_right_click_time: float = 0.0
        self._pinch_middle_started_at: Optional[float] = None
        # Press-to-click
        self._previous_raw_shape: HandShape = HandShape.UNKNOWN
        self._click_already_fired: bool = False
        self._right_click_already_fired: bool = False
        self._last_double_click_time: float = 0.0

        self._peace_started_at: Optional[float] = None
        self.PEACE_MIN_HOLD_SECONDS: float = 0.04

        self._hand_lost_frames: int = 0

        # Tracking de movimento
        self._last_smoothed_pos: Optional[Tuple[float, float]] = None
        self._last_velocity: float = 0.0
        self._last_velocity_vec: Tuple[float, float] = (0.0, 0.0)
        self._velocity_history: Deque[float] = deque(maxlen=5)

        # Follow-through: cinematic prediction quando a mao sai do FOV.
        # Cursor "termina" o gesto que o usuario comecou em vez de travar
        # abrupto. Janela em frames + decay exponencial da velocidade.
        self.followthrough_frames: int = max(0, int(followthrough_frames))
        self.followthrough_damping: float = max(
            0.0, min(0.99, float(followthrough_damping))
        )
        self._followthrough_consumed: int = 0
        self._followthrough_velocity: Tuple[float, float] = (0.0, 0.0)

        # Pinca dual
        self._pinch_dist_history: Deque[Tuple[float, float]] = deque(maxlen=3)

        # Overlay
        self._last_hand_size: float = 0.0
        self._last_dpi: float = 1.0
        self._last_pinch_dist: float = 0.0
        self._last_event_gesture: Gesture = Gesture.NONE
        self._aim_assist_active: bool = False
        self._sticky_active: bool = False

    # -----------------------------------------------------------------
    # Configuracao do pipeline de movimento
    # -----------------------------------------------------------------

    def _build_motion_config(self) -> MotionConfig:
        """Monta o MotionConfig a partir do estado atual do detector.

        Chamado no __init__ e sempre que um slider/perfil altera um
        parametro que vive dentro do MotionConfig (que e' frozen).
        """
        return MotionConfig(
            scale_reference=self.hand_size_reference,
            scale_far_ratio=self._distance_scale_far_ratio,
            scale_near_ratio=self._distance_scale_near_ratio,
            gain_far=self._distance_gain_far,
            gain_near=self._distance_gain_near,
            distance_gain_enabled=(
                self._distance_gain_enabled and self.dpi_adaptive_enabled
            ),
            scale_filter_hz=self._distance_scale_filter_hz,
            gain_rate_per_second=self._distance_gain_rate_per_second,
            aim_slowdown_factor=self._aim_assist_slowdown_factor,
            sticky_friction_factor=self._sticky_friction_factor,
            sticky_enabled=self._sticky_targeting_enabled,
            sticky_deceleration_threshold=self.sticky_deceleration_threshold,
            precision_attack_seconds=self._precision_attack_seconds,
            precision_release_seconds=self._precision_release_seconds,
            velocity_curve_enabled=self.velocity_curve_enabled,
            velocity_tremor_threshold=self.velocity_tremor_threshold,
            velocity_precision_zone=self.velocity_precision_zone,
            velocity_fast_threshold=self.velocity_fast_threshold,
            velocity_slow_factor=self.velocity_slow_factor,
            velocity_fast_factor=self.velocity_fast_factor,
            bottom_assist_enabled=self._bottom_assist_enabled,
            bottom_edge=self._bottom_assist_edge,
            bottom_band=self._bottom_assist_band,
            bottom_max_gain=self._bottom_assist_max_gain,
            bottom_max_extra_rate=self._bottom_assist_max_extra_rate,
            bottom_intent_speed=self._bottom_assist_intent_speed,
        )

    def _sync_motion_config(self) -> None:
        """Propaga mudancas de runtime sem mover o cursor.

        ``CursorMotion.set_config`` preserva a posicao de saida — trocar
        de perfil no meio do uso ajusta o proximo movimento, nunca
        reposiciona o cursor.
        """
        motion = getattr(self, "_motion", None)
        if motion is not None:
            motion.set_config(self._build_motion_config())

    # Sliders/perfis mudam estes valores em runtime (ver
    # VirtualMouseService._apply_setting_live). Como MotionConfig e'
    # frozen, os setters reconstroem a config em vez de deixar o valor
    # divergir silenciosamente do que o pipeline usa.

    @property
    def aim_assist_slowdown_factor(self) -> float:
        return self._aim_assist_slowdown_factor

    @aim_assist_slowdown_factor.setter
    def aim_assist_slowdown_factor(self, value: float) -> None:
        self._aim_assist_slowdown_factor = float(value)
        self._sync_motion_config()

    @property
    def sticky_friction_factor(self) -> float:
        return self._sticky_friction_factor

    @sticky_friction_factor.setter
    def sticky_friction_factor(self, value: float) -> None:
        self._sticky_friction_factor = float(value)
        self._sync_motion_config()

    @property
    def sticky_targeting_enabled(self) -> bool:
        return self._sticky_targeting_enabled

    @sticky_targeting_enabled.setter
    def sticky_targeting_enabled(self, value: bool) -> None:
        self._sticky_targeting_enabled = bool(value)
        self._sync_motion_config()

    @property
    def motion(self) -> CursorMotion:
        """Pipeline de movimento — exposto pra HUD/debug e testes."""
        return self._motion

    def reset(self) -> None:
        self._candidate_shape = HandShape.UNKNOWN
        self._candidate_count = 0
        self._confirmed_shape = HandShape.UNKNOWN
        self._pinch_started_at = None
        self._pinch_middle_started_at = None
        self._peace_started_at = None
        self._velocity_history.clear()
        self._pinch_dist_history.clear()
        self._followthrough_consumed = 0
        self._followthrough_velocity = (0.0, 0.0)
        self._last_velocity_vec = (0.0, 0.0)
        # Press-to-click state
        self._previous_raw_shape = HandShape.UNKNOWN
        self._click_already_fired = False
        self._right_click_already_fired = False
        # Ancora robusta: limpa historico de landmarks (mao saiu por muito
        # tempo, comecaremos do zero ao re-detectar).
        self._robust_anchor.reset()
        # Movimento: descarta entrada/escala/assistencias mas PRESERVA a
        # posicao de saida. Quando a mao volta, a entrada re-ancora e o
        # cursor continua de onde parou — sem salto e sem voltar ao centro.
        self._motion.soft_reset()
        self._last_frame_t = None

    # -----------------------------------------------------------------
    # AIM ASSIST com pre-ativacao + holdover
    # -----------------------------------------------------------------

    def _aim_target(self, shape: HandShape, pinch_dist: float) -> float:
        """Alvo de precisao em [0, 1] — GRADUAL, nunca liga/desliga.

        Antes isto era booleano com holdover por timer: a troca entre
        1.0 e AIM_ASSIST_SLOWDOWN_FACTOR acontecia num unico frame e a
        mudanca de velocidade era perceptivel. Agora:

        - shape confirmado (PINCH/PEACE) => alvo 1.0
        - pre-ativacao => rampa linear na PROXIMIDADE do pinch, entre o
          threshold de pre-ativacao (0) e o de pinch (1)
        - solto => alvo 0, e o envelope de release faz a liberacao
          progressiva que o holdover fazia em degrau
        """
        if not self.aim_assist_enabled:
            return 0.0

        if shape.value in self.aim_assist_trigger_shapes:
            return 1.0

        if self.aim_assist_pre_activation:
            hi = self.aim_assist_pre_pinch_threshold
            lo = self.pinch_threshold
            if hi > lo and pinch_dist < hi:
                return _clamp((hi - pinch_dist) / (hi - lo), 0.0, 1.0)

        return 0.0

    # -----------------------------------------------------------------
    # PIPELINE DE PRECISAO
    # -----------------------------------------------------------------
    # A matematica (ganho por distancia, curva balistica, sticky gradual,
    # aim assist continuo, borda inferior) vive em core/cursor_motion.py.
    # Aqui so alimentamos o pipeline e espelhamos o estado pro HUD.

    def _frame_dt(self, now: float) -> float:
        """Tempo desde o frame anterior. Nada aqui depende de FPS nominal."""
        if self._last_frame_t is None:
            dt = 1.0 / 60.0
        else:
            dt = now - self._last_frame_t
        self._last_frame_t = now
        if dt <= 0.0:
            dt = 1.0 / 60.0
        return dt

    def _run_motion(
        self,
        anchor: Tuple[float, float],
        hand: HandLandmarks,
        dt: float,
        aim_target: float,
        precision_hold: bool,
    ) -> Tuple[float, float]:
        pos = self._motion.update(
            anchor,
            dt,
            landmarks=hand.landmarks,
            base_sensitivity=self.dpi_fixed,
            aim_target=aim_target,
            precision_hold=precision_hold,
        )
        # Espelhos pro HUD e pro follow-through (mantidos na mesma
        # unidade de antes: deslocamento POR FRAME).
        self._last_smoothed_pos = pos
        self._last_velocity = self._motion.speed * dt
        self._last_velocity_vec = self._motion.last_delta
        self._velocity_history.append(self._last_velocity)
        self._last_dpi = self._motion.total_gain
        self._aim_assist_active = self._motion.precision_weight > 0.05
        self._sticky_active = self._motion.sticky_weight > 0.05
        return pos

    # -----------------------------------------------------------------
    # PINCA DUAL
    # -----------------------------------------------------------------

    def _classify_shape_dual(
        self,
        hand: HandLandmarks,
        effective_pinch_threshold: float,
        now: float,
        *,
        effective_pinch_middle_threshold: Optional[float] = None,
        effective_pinch_middle_index_guard: Optional[float] = None,
    ) -> HandShape:
        pinch_dist = _pinch_distance(
            hand.landmarks[LM_THUMB_TIP],
            hand.landmarks[LM_INDEX_TIP],
        )
        self._pinch_dist_history.append((now, pinch_dist))

        # Usa effective scaled se passados, fallback pra self.*
        # (compat com callers antigos / testes).
        mid_th = (
            effective_pinch_middle_threshold
            if effective_pinch_middle_threshold is not None
            else self.pinch_middle_threshold
        )
        mid_guard = (
            effective_pinch_middle_index_guard
            if effective_pinch_middle_index_guard is not None
            else self.pinch_middle_index_guard
        )

        raw_shape = classify_shape(
            hand,
            effective_pinch_threshold,
            pinch_middle_threshold=mid_th,
            pinch_middle_index_guard=mid_guard,
            pinch_middle_index_extension_min=(
                self.pinch_middle_index_extension_min
            ),
            pinch_middle_middle_extension_max=(
                self.pinch_middle_middle_extension_max
            ),
        )

        if not self.pinch_dual_detection:
            return raw_shape

        if raw_shape != HandShape.PINCH:
            return raw_shape

        # Validar pinca: dedos devem estar aproximando ou mantendo
        if len(self._pinch_dist_history) >= 2:
            (t_prev, d_prev) = self._pinch_dist_history[-2]
            dt = max(1e-6, now - t_prev)
            d_velocity = (pinch_dist - d_prev) / dt

            # Se afastando rapido E nao estava ja confirmado como PINCH, rejeita
            if d_velocity > self.pinch_velocity_threshold:
                if self._confirmed_shape == HandShape.PINCH:
                    return HandShape.PINCH
                return HandShape.OTHER

        return HandShape.PINCH

    # -----------------------------------------------------------------
    # Update principal
    # -----------------------------------------------------------------

    def update(self, hand: Optional[HandLandmarks]) -> List[GestureEvent]:
        events: List[GestureEvent] = []
        now = time.perf_counter()
        dt = self._frame_dt(now)

        # Position hold + follow-through:
        # - 0..position_hold_frames frames: hold (cursor parado, pode ser
        #   blink momentaneo da deteccao).
        # - position_hold + followthrough_frames: extrapola pela velocidade
        #   recente com decay. Cursor "termina" o gesto que o usuario
        #   comecou em vez de travar abrupto quando a mao sai do FOV.
        # - alem disso: reset + PAUSE (mao realmente perdida).
        # Drag mantem durante TODA a janela (hold + followthrough) — corte
        # so quando a mao realmente nao volta.
        if hand is None:
            self._hand_lost_frames += 1
            hold_limit = self.position_hold_frames
            ft_limit = hold_limit + self.followthrough_frames
            # Blink curto: o hold nao mexe em nada. Perda prolongada faz
            # o motion descartar a escala e pedir re-ancoragem — o
            # retorno da mao alinha a entrada sem mover a saida.
            self._motion.notify_hand_lost(dt)

            if self._hand_lost_frames <= hold_limit:
                return events  # hold puro

            if self._hand_lost_frames <= ft_limit:
                # Inicia a janela: copia velocidade vetorial do ultimo
                # movimento real como ponto de partida (so na 1a iteracao).
                if self._followthrough_consumed == 0:
                    self._followthrough_velocity = self._last_velocity_vec
                # Aplica velocidade com damping exponencial — cursor
                # desacelera suavemente em vez de teletransportar.
                if self._last_smoothed_pos is not None:
                    fx, fy = self._followthrough_velocity
                    # advance_output NAO passa por ganho nem pela
                    # assistencia de borda: follow-through e' um
                    # mecanismo separado, aplicado so com a mao AUSENTE.
                    nx, ny = self._motion.advance_output(fx, fy)
                    self._last_smoothed_pos = (nx, ny)
                    self._followthrough_velocity = (
                        fx * self.followthrough_damping,
                        fy * self.followthrough_damping,
                    )
                    self._followthrough_consumed += 1
                    events.append(GestureEvent(Gesture.MOVE, (nx, ny), now))
                return events

            # Alem da janela: mao realmente sumiu — reset + PAUSE
            if self._drag_active:
                events.append(GestureEvent(Gesture.DRAG_END, None, now))
                self._drag_active = False
            self.reset()
            self._last_event_gesture = Gesture.PAUSE
            self._aim_assist_active = False
            self._sticky_active = False
            return events

        # Mao voltou — limpa state de followthrough
        self._hand_lost_frames = 0
        self._followthrough_consumed = 0
        self._followthrough_velocity = (0.0, 0.0)
        self._last_hand_size = compute_hand_size(hand)

        # Pinch threshold adaptativo (com FLOOR minimo)
        effective_pinch_threshold = self.pinch_threshold
        # pinch_middle tambem precisa escalar com hand_size. Sem isso,
        # mao GRANDE no frame (perto da camera ou angulo lateral/perfil
        # tipo OK-sign) tem distancias normalizadas maiores que o
        # threshold fixo 0.075 → PINCH_MIDDLE rejeitado mesmo com
        # polegar+medio em contato real.
        effective_pinch_middle_threshold = self.pinch_middle_threshold
        effective_pinch_middle_index_guard = self.pinch_middle_index_guard
        if self.pinch_adaptive and self.hand_size_reference > 0:
            scale = self._last_hand_size / self.hand_size_reference
            scale_clamped = max(0.5, min(2.0, scale))
            effective_pinch_threshold = max(
                self.pinch_threshold_floor,
                self.pinch_threshold * scale_clamped,
            )
            # Mesma logica adaptive aplicada ao middle threshold + guard.
            # Floor compartilhado (pinch_threshold_floor) pro middle.
            effective_pinch_middle_threshold = max(
                self.pinch_threshold_floor,
                self.pinch_middle_threshold * scale_clamped,
            )
            effective_pinch_middle_index_guard = (
                self.pinch_middle_index_guard * scale_clamped
            )

        self._last_pinch_dist = _pinch_distance(
            hand.landmarks[LM_THUMB_TIP],
            hand.landmarks[LM_INDEX_TIP],
        )

        # Classificacao DUAL — passa effective middle params escalados
        raw_shape = self._classify_shape_dual(
            hand, effective_pinch_threshold, now,
            effective_pinch_middle_threshold=effective_pinch_middle_threshold,
            effective_pinch_middle_index_guard=effective_pinch_middle_index_guard,
        )

        # PRESS-TO-CLICK
        # Detecta BORDA SUBINDO (dedos encostando) e dispara CLICK/
        # RIGHT_CLICK imediatamente — em vez de esperar o usuario soltar.
        # Release-to-click tinha feedback atrasado e perdia cliques curtos
        # que soltavam antes da histerese confirmar PINCH.
        anchor_now = get_anchor_position(hand, self.anchor_landmark, robust_anchor=self._robust_anchor)

        edge_pinch = (
            self._previous_raw_shape != HandShape.PINCH
            and raw_shape == HandShape.PINCH
        )
        edge_pinch_middle = (
            self._previous_raw_shape != HandShape.PINCH_MIDDLE
            and raw_shape == HandShape.PINCH_MIDDLE
        )

        # Atualiza estado para o proximo frame (depois das edges calculadas)
        self._previous_raw_shape = raw_shape

        # CLICK no PRESS (encostou)
        if edge_pinch and not self._drag_active:
            if (now - self._last_click_time) >= self.click_cooldown:
                self._last_click_time = now
                self._click_already_fired = True
                self._pinch_started_at = now  # ainda rastreia para drag
                logger.info("CLICK 🤏 (press-to-click)")
                events.append(GestureEvent(Gesture.CLICK, anchor_now, now))

        # RIGHT_CLICK no PRESS (encostou medio + indicador afastado)
        if edge_pinch_middle:
            if (now - self._last_right_click_time) >= self.click_cooldown:
                self._last_right_click_time = now
                self._right_click_already_fired = True
                self._pinch_middle_started_at = now
                logger.info("RIGHT_CLICK 👉 (press-to-click)")
                events.append(GestureEvent(Gesture.RIGHT_CLICK, anchor_now, now))
        # ===========================================================

        # Histerese
        if raw_shape == self._confirmed_shape:
            self._candidate_shape = raw_shape
            self._candidate_count = 0
            confirmed_now = self._confirmed_shape
        else:
            if raw_shape == self._candidate_shape:
                self._candidate_count += 1
            else:
                self._candidate_shape = raw_shape
                self._candidate_count = 1

            if self._confirmed_shape in (HandShape.UNKNOWN, HandShape.OTHER):
                threshold = self.debounce_frames
            else:
                threshold = self.exit_frames

            if self._candidate_count >= threshold:
                confirmed_now = self._candidate_shape
            else:
                confirmed_now = self._confirmed_shape

        # Precisao continua: alvo gradual + envelope attack/release.
        aim_target = self._aim_target(confirmed_now, self._last_pinch_dist)

        # CURSOR MOVE
        cursor_moves_in_open_hand = (confirmed_now == HandShape.OPEN_HAND)
        cursor_moves_in_pinch_drag = (
            confirmed_now == HandShape.PINCH and self._drag_active
        )

        if cursor_moves_in_open_hand or cursor_moves_in_pinch_drag:
            # anchor_now ja foi calculado UMA vez neste frame — a ancora
            # robusta e' stateful e recomputar corromperia velocidade e
            # historico de estabilidade.
            final_pos = self._run_motion(
                anchor_now, hand, dt, aim_target,
                precision_hold=cursor_moves_in_pinch_drag,
            )
            events.append(GestureEvent(Gesture.MOVE, final_pos, now))
        else:
            # Qualquer shape que NAO move o cursor (PEACE, FIST, PINCH
            # antes do drag, OTHER/UNKNOWN em transicao) passa por hold():
            # re-alinha a ENTRADA sem mover a SAIDA. Sem isso a ancora
            # continuaria correndo enquanto o cursor esta congelado e o
            # delta acumulado viraria um salto ao voltar pra OPEN_HAND.
            self._last_smoothed_pos = self._motion.hold(
                anchor_now, dt, aim_target=aim_target,
            )
            self._last_velocity = 0.0
            self._sticky_active = False
            self._aim_assist_active = aim_target > 0.05

        # ACOES
        previous_shape = self._confirmed_shape
        action_event = self._process_action(
            shape=confirmed_now,
            previous_shape=previous_shape,
            anchor=anchor_now,
            now=now,
        )
        if action_event is not None:
            events.append(action_event)
            self._last_event_gesture = action_event.gesture
        elif confirmed_now == HandShape.OPEN_HAND:
            self._last_event_gesture = Gesture.MOVE
        elif confirmed_now == HandShape.FIST:
            self._last_event_gesture = Gesture.PAUSE
        elif confirmed_now == HandShape.PEACE:
            self._last_event_gesture = Gesture.PAUSE

        self._confirmed_shape = confirmed_now
        return events

    # -----------------------------------------------------------------
    # Logica de acoes
    # -----------------------------------------------------------------

    def _process_action(self, shape, previous_shape, anchor, now):
        # `anchor` chega pronto do update(): a ancora robusta e' STATEFUL
        # (historico por landmark + velocidade) e precisa ser computada
        # exatamente UMA vez por mao/frame.
        event = None

        # 1. SAIU DE PEACE -> DOUBLE_CLICK
        if previous_shape == HandShape.PEACE and shape != HandShape.PEACE:
            peace_duration = 0.0
            if self._peace_started_at is not None:
                peace_duration = now - self._peace_started_at
            self._peace_started_at = None

            if peace_duration >= self.PEACE_MIN_HOLD_SECONDS:
                if (now - self._last_double_click_time) >= self.double_click_cooldown:
                    self._last_double_click_time = now
                    logger.info("DOUBLE_CLICK ✌️ disparado (duration: %.2fs)", peace_duration)
                    event = GestureEvent(Gesture.DOUBLE_CLICK, anchor, now)

            if event is not None:
                if shape == HandShape.PINCH:
                    self._pinch_started_at = now
                return event

        # 2. SAIU DE PINCH
        if previous_shape == HandShape.PINCH and shape != HandShape.PINCH:
            if self._drag_active:
                self._drag_active = False
                self._pinch_started_at = None
                self._click_already_fired = False
                return GestureEvent(Gesture.DRAG_END, anchor, now)

            self._pinch_started_at = None

            # Se ja disparou no PRESS (encostou), so reseta a flag — sem
            # isso disparariamos 2 cliques (encostar + soltar).
            if self._click_already_fired:
                self._click_already_fired = False
                return None

            # FALLBACK release-to-click: roda apenas se a edge detection do press
            # falhou (caso raro de MediaPipe perder 1-2 frames consecutivos).
            held_for = 0.0
            if self._pinch_started_at is not None:
                held_for = now - self._pinch_started_at
            if held_for < self.drag_hold_seconds and held_for > self.pinch_min_hold_seconds:
                if (now - self._last_click_time) >= self.click_cooldown:
                    self._last_click_time = now
                    return GestureEvent(Gesture.CLICK, anchor, now)

        # 2b. SAIU DE PINCH_MIDDLE
        if previous_shape == HandShape.PINCH_MIDDLE and shape != HandShape.PINCH_MIDDLE:
            self._pinch_middle_started_at = None

            # Idem ao bloco PINCH — se ja disparou no press, ignora.
            if self._right_click_already_fired:
                self._right_click_already_fired = False
                return None

            # FALLBACK release-to-click (caso edge detection tenha falhado)
            held_for = 0.0
            if self._pinch_middle_started_at is not None:
                held_for = now - self._pinch_middle_started_at
            if held_for > self.pinch_min_hold_seconds:
                if (now - self._last_right_click_time) >= self.click_cooldown:
                    self._last_right_click_time = now
                    logger.info("RIGHT_CLICK 👉 (fallback release)")
                    return GestureEvent(Gesture.RIGHT_CLICK, anchor, now)

        # 3. ENTRA em PEACE
        if shape == HandShape.PEACE:
            if self._peace_started_at is None:
                self._peace_started_at = now
            return None

        # 4. ENTRA em PINCH
        if shape == HandShape.PINCH:
            if self._pinch_started_at is None:
                self._pinch_started_at = now

            held_for = now - self._pinch_started_at

            if held_for >= self.drag_hold_seconds and not self._drag_active:
                self._drag_active = True
                return GestureEvent(Gesture.DRAG_START, anchor, now)

            return None

        # 4b. ENTRA em PINCH_MIDDLE
        # RIGHT_CLICK ja foi disparado no PRESS (edge detection no update).
        # Aqui apenas marcamos o tempo pra fallback caso a flag esteja
        # False ao soltar (caso raro de edge perdida).
        if shape == HandShape.PINCH_MIDDLE:
            if self._pinch_middle_started_at is None:
                self._pinch_middle_started_at = now
            return None

        return None

    # -----------------------------------------------------------------
    # Introspeccao
    # -----------------------------------------------------------------

    @property
    def current_shape(self) -> HandShape:
        return self._confirmed_shape

    @property
    def current_gesture(self) -> Gesture:
        return self._last_event_gesture

    @property
    def is_dragging(self) -> bool:
        return self._drag_active

    @property
    def hand_size(self) -> float:
        return self._last_hand_size

    @property
    def dpi_multiplier(self) -> float:
        return self._last_dpi

    @property
    def pinch_distance(self) -> float:
        return self._last_pinch_dist

    @property
    def drag_progress(self) -> float:
        if self._drag_active:
            return 1.0
        if self._pinch_started_at is None:
            return 0.0
        held = time.perf_counter() - self._pinch_started_at
        return min(1.0, held / max(0.01, self.drag_hold_seconds))

    @property
    def double_click_cooldown_remaining(self) -> float:
        elapsed = time.perf_counter() - self._last_double_click_time
        remaining = self.double_click_cooldown - elapsed
        return max(0.0, remaining)

    @property
    def is_frozen(self) -> bool:
        return self._confirmed_shape in (
            HandShape.PEACE, HandShape.FIST, HandShape.PINCH_MIDDLE,
            HandShape.UNKNOWN, HandShape.OTHER,
        )

    @property
    def aim_assist_active(self) -> bool:
        return self._aim_assist_active

    @property
    def sticky_active(self) -> bool:
        """Sticky targeting ativo neste momento?"""
        return self._sticky_active

    @property
    def velocity(self) -> float:
        return self._last_velocity

    # Compat
    @property
    def freeze_remaining(self) -> float:
        return 0.0

    @property
    def index_angle_from_up(self) -> float:
        return 0.0