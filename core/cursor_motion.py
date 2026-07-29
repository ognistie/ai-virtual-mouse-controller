"""
core.cursor_motion
==================

Pipeline de MOVIMENTO do cursor: ganho adaptativo por distancia, precisao
continua e assistencia da borda inferior.

Por que este modulo existe
--------------------------
Antes, tres problemas moravam dentro do ``GestureDetector``:

1. ``compute_dpi_multiplier()`` era MONOTONICO NA DIRECAO ERRADA — mao
   grande (perto da webcam) ganhava multiplicador ALTO e mao pequena
   (longe) ganhava BAIXO. O usuario longe, com landmarks menores e mais
   ruidosos, era justamente quem tinha menos alcance.
2. ``apply_dpi_to_position()`` escalava uma POSICAO ABSOLUTA em torno de
   (0.5, 0.5). Como a escala estimada da mao oscila alguns por cento a
   cada frame, o cursor se deslocava com a mao parada — proporcionalmente
   a distancia ate o centro da tela.
3. O delta do pipeline de precisao era ``target - saida_anterior``, ou
   seja um ERRO entre espaco de entrada e espaco de saida. Multiplicar
   isso pelo fator de aim assist nao reduz sensibilidade: vira um filtro
   de lag de 1a ordem, e o cursor continua escorregando na direcao da
   ancora mesmo com a mao imovel.

Aqui o movimento passa a ser RELATIVO e as assistencias, CONTINUAS:

    saida += (ancora_atual - ancora_anterior) x ganho_total
    ganho_total = sensibilidade_base x ganho_distancia x ganho_precisao

Invariantes garantidos por construcao (e cobertos em tests/):

- ancora parada => deslocamento ZERO, independente de distancia, perfil,
  aim assist ou assistencia de borda. Mudanca de ganho so afeta
  movimentos FUTUROS — nunca reposiciona o cursor.
- toda transicao (distancia, precisao, entrada da curva inferior) e' pelo
  menos C1: nenhuma muda a velocidade aparente em degrau.
- comportamento identico por TEMPO FISICO em 30 ou 60 FPS: tudo depende
  de ``dt``, nada de constante por frame.

Design
------
- Modulo PURO: sem I/O, sem cv2/Qt/pyautogui, sem relogio proprio. O
  chamador injeta ``dt`` — o que torna os testes deterministas sem sleep.
- Espaco de trabalho: coordenadas normalizadas do MediaPipe ([0,1] no
  frame), o mesmo espaco da ancora. Quem mapeia pra pixel continua sendo
  o ``CursorController``.
- Estado encapsulado em ``CursorMotion``; a matematica fica em funcoes
  puras testaveis isoladamente.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Sequence, Tuple


Point = Tuple[float, float]
Landmarks = Sequence[Tuple[float, float, float]]


# ---------------------------------------------------------------------
# Helpers matematicos puros
# ---------------------------------------------------------------------

def clamp(value: float, low: float, high: float) -> float:
    """Limita ``value`` ao intervalo [low, high]."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def lerp(a: float, b: float, t: float) -> float:
    """Interpolacao linear entre ``a`` e ``b`` com ``t`` em [0, 1]."""
    return a + (b - a) * t


def smoothstep(t: float) -> float:
    """Smoothstep classico (3t^2 - 2t^3), com clamp em [0, 1].

    Escolhido em vez de interpolacao linear porque a DERIVADA e' zero nas
    duas pontas: emendar dois trechos em t=0 ou t=1 nao produz degrau de
    velocidade aparente (continuidade C1). E' o que mata o "joelho" que a
    curva ease-out anterior tinha no boost da borda inferior, onde a
    derivada saltava de 1.0 pra ``power`` (1.5) instantaneamente.
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def rate_limit(current: float, target: float, max_step: float) -> float:
    """Aproxima ``current`` de ``target`` em no maximo ``max_step``."""
    if max_step <= 0.0:
        return current
    delta = target - current
    if delta > max_step:
        return current + max_step
    if delta < -max_step:
        return current - max_step
    return target


def _median(values: List[float]) -> float:
    """Mediana de uma lista NAO vazia (ordena in-place)."""
    values.sort()
    n = len(values)
    mid = n // 2
    if n % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) * 0.5


# ---------------------------------------------------------------------
# Estimativa da escala aparente da palma
# ---------------------------------------------------------------------
# Deliberadamente NAO usamos o ``z`` do MediaPipe como distancia da
# camera: ele e' relativo ao pulso, sub-estimado em relacao a x,y e
# ruidoso o suficiente pra invalidar qualquer ganho derivado dele.
#
# Usamos a escala APARENTE da palma — quanto maior a palma no frame,
# mais perto a mao esta. Segmentos escolhidos por serem os mais estaveis
# da mao (nao mudam com o gesto, so com a pose/distancia):
#
#   (0, 9)   pulso -> MCP do medio  = "comprimento da palma"
#   (5, 9)   MCP indicador -> medio
#   (9, 13)  MCP medio -> anelar
#   (13, 17) MCP anelar -> mindinho
#   (5, 17)  largura da fileira de MCPs
#
# Cada segmento e' dividido pela sua PROPORCAO ANATOMICA aproximada em
# relacao ao comprimento da palma, de forma que todos fiquem na mesma
# unidade ("palmas") e a mediana faca sentido. As proporcoes sao
# aproximadas de proposito: o estimador so precisa ser MONOTONICO na
# distancia, e a mediana absorve o erro de um ou dois segmentos.
_SCALE_SEGMENTS: Tuple[Tuple[int, int, float], ...] = (
    (0, 9, 1.00),
    (5, 9, 0.24),
    (9, 13, 0.22),
    (13, 17, 0.26),
    (5, 17, 0.70),
)

# Um segmento so entra na mediana se estiver dentro desta faixa em
# relacao a mediana bruta dos candidatos — rejeicao de outlier grosseiro
# (landmark predito/ocluido produz distancias absurdas).
_SCALE_OUTLIER_LOW: float = 0.55
_SCALE_OUTLIER_HIGH: float = 1.80


def estimate_palm_scale(landmarks: Optional[Landmarks]) -> Optional[float]:
    """Escala aparente da palma, em unidades de "comprimento de palma".

    Retorna ``None`` quando nao ha landmarks suficientes ou quando todos
    os segmentos sao degenerados (mao de perfil extremo, deteccao ruim).
    Nesse caso o chamador deve MANTER a ultima escala conhecida em vez de
    inventar um valor — mudar o ganho com base em lixo e' pior do que
    ficar com o ganho anterior.
    """
    if landmarks is None or len(landmarks) < 21:
        return None

    candidates: List[float] = []
    for i, j, ratio in _SCALE_SEGMENTS:
        p = landmarks[i]
        q = landmarks[j]
        dx = float(p[0]) - float(q[0])
        dy = float(p[1]) - float(q[1])
        dist = math.sqrt(dx * dx + dy * dy)
        if not math.isfinite(dist) or dist <= 1e-5:
            continue
        candidates.append(dist / ratio)

    if not candidates:
        return None
    if len(candidates) <= 2:
        return _median(list(candidates))

    rough = _median(list(candidates))
    if rough <= 1e-6:
        return None
    kept = [
        c for c in candidates
        if _SCALE_OUTLIER_LOW * rough <= c <= _SCALE_OUTLIER_HIGH * rough
    ]
    if not kept:
        return rough
    return _median(kept)


# ---------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class MotionConfig:
    """Parametros do pipeline de movimento. Imutavel e serializavel."""

    # --- Distancia -> ganho -------------------------------------------
    scale_reference: float = 0.22
    """Escala aparente da palma considerada "distancia neutra" (ganho 1.0).
    Mesma unidade de ``HAND_SIZE_REFERENCE``."""

    scale_far_ratio: float = 0.60
    """escala/referencia a partir da qual a mao conta como LONGE."""

    scale_near_ratio: float = 1.55
    """escala/referencia a partir da qual a mao conta como PERTO."""

    gain_far: float = 1.40
    gain_neutral: float = 1.00
    gain_near: float = 0.75

    distance_gain_enabled: bool = True

    scale_filter_hz: float = 1.2
    """Cutoff do passa-baixa da escala. Baixo de proposito: distancia da
    mao muda em escala de ~1s, tremor muda em ~10ms."""

    scale_deadband_ratio: float = 0.05
    """Zona morta relativa: variacoes menores que 5% da escala filtrada
    nao movem o filtro. Impede que ruido de landmark vire mudanca de
    ganho."""

    gain_rate_per_second: float = 1.5
    """Teto de variacao do ganho de distancia por segundo. Mesmo que a
    escala pule, o ganho leva tempo pra acompanhar."""

    scale_lost_reset_seconds: float = 0.30
    """Tempo sem mao antes de descartar a escala filtrada."""

    # --- Precisao (aim assist + sticky) -------------------------------
    aim_slowdown_factor: float = 0.40
    sticky_friction_factor: float = 0.75
    sticky_enabled: bool = True
    sticky_deceleration_threshold: float = 0.7
    sticky_min_speed: float = 0.30
    """Velocidade minima (unidades normalizadas por SEGUNDO) pra sticky
    atuar. 0.30/s == os antigos 0.005/frame a 60 FPS."""

    precision_attack_seconds: float = 0.10
    """Constante de tempo de ENTRADA da precisao (~63%). Entrada completa
    em ~2.2x esse valor."""

    precision_release_seconds: float = 0.22
    """Constante de tempo de SAIDA. Substitui o antigo holdover por
    timer: a liberacao e' progressiva, nunca um degrau."""

    # --- Curva balistica (anti-tremor) --------------------------------
    velocity_curve_enabled: bool = True
    velocity_reference_fps: float = 60.0
    """Os thresholds abaixo sao herdados em unidades POR FRAME. Multiplicar
    por esta referencia converte pra unidades por segundo mantendo o feel
    identico a 60 FPS — e passando a valer o mesmo a 30 FPS."""
    velocity_tremor_threshold: float = 0.003
    velocity_precision_zone: float = 0.025
    velocity_fast_threshold: float = 0.12
    velocity_slow_factor: float = 0.55
    velocity_fast_factor: float = 1.15

    speed_filter_hz: float = 8.0
    """Passa-baixa da velocidade que alimenta a curva balistica.

    Sem ele, um pico ISOLADO de ruido de landmark abre o portao
    anti-tremor por um frame e injeta um passo inteiro na saida. Como o
    pipeline integra deltas, esses passos viram deriva lenta com a mao
    parada. Movimento real sustenta velocidade por varios frames e passa
    normalmente — o custo e' ~1 frame de resposta na largada.
    """

    # --- Borda inferior ------------------------------------------------
    bottom_assist_enabled: bool = True
    bottom_edge: float = 0.86
    """Coordenada Y (espaco da ancora) que o ``CursorController`` mapeia
    pro ultimo pixel da tela — normalmente ``1 - SCREEN_MARGIN_BOTTOM``."""

    bottom_band: float = 0.22
    """Largura da faixa acima da borda onde a assistencia atua."""

    bottom_max_gain: float = 3.2
    """Ganho MAXIMO do movimento descendente, atingido na borda. Com mao
    perto da webcam (ganho de distancia 0.75) o produto fica ~1.65 no
    ultimo trecho: e' o que devolve o alcance da taskbar sem tocar na
    precisao do miolo da tela."""

    bottom_max_extra_rate: float = 0.60
    """Teto da velocidade ADICIONADA pela assistencia (unidades/s), em
    cima da velocidade que o usuario ja imprime."""

    bottom_intent_speed: float = 0.12
    """Velocidade descendente (unidades/s) que conta como intencao plena
    de descer. Abaixo disso a assistencia entra proporcionalmente."""

    bottom_up_epsilon: float = 0.0015
    """Zona morta do sinal de subida (unidades/s). Unica histerese da
    assistencia — existe pra impedir flicker com a mao praticamente
    parada, nunca pra prender o cursor."""

    # --- Limites globais ----------------------------------------------
    total_gain_min: float = 0.05
    total_gain_max: float = 3.0
    max_dt: float = 0.10
    """Teto de ``dt`` aceito. Protege contra stall do loop (troca de
    janela, GC longo) virar um salto de cursor."""


# ---------------------------------------------------------------------
# Ganho por distancia
# ---------------------------------------------------------------------

def distance_gain(scale: float, cfg: MotionConfig) -> float:
    """Ganho em funcao da escala aparente da palma.

    Relacao MONOTONICA e INVERSA (o oposto do que o codigo antigo fazia):

        escala pequena (mao LONGE)      -> ganho alto   (cfg.gain_far)
        escala == referencia            -> ganho 1.0    (cfg.gain_neutral)
        escala grande  (mao PERTO)      -> ganho baixo  (cfg.gain_near)

    Os dois trechos usam ``smoothstep``, cuja derivada e' zero em t=0.
    Como ambos partem da referencia com t=0, a curva e' C1 no ponto de
    emenda: atravessar a distancia neutra nao muda a velocidade aparente
    em degrau.
    """
    if not cfg.distance_gain_enabled or cfg.scale_reference <= 0.0:
        return cfg.gain_neutral

    ref = cfg.scale_reference
    if scale <= ref:
        far = ref * cfg.scale_far_ratio
        span = ref - far
        if span <= 1e-9:
            return cfg.gain_neutral
        t = clamp((ref - scale) / span, 0.0, 1.0)
        return lerp(cfg.gain_neutral, cfg.gain_far, smoothstep(t))

    near = ref * cfg.scale_near_ratio
    span = near - ref
    if span <= 1e-9:
        return cfg.gain_neutral
    t = clamp((scale - ref) / span, 0.0, 1.0)
    return lerp(cfg.gain_neutral, cfg.gain_near, smoothstep(t))


def velocity_curve_factor(speed: float, cfg: MotionConfig) -> float:
    """Fator da curva balistica dado ``speed`` em unidades por SEGUNDO.

    Mesma forma da curva anterior (``apply_velocity_curve_smooth``), com
    os thresholds convertidos de por-frame pra por-segundo. Trechos:

        speed < tremor                  -> 0.0            (anti-tremor)
        tremor..precisao                -> smoothstep(slow -> 1.0)
        precisao..rapido                -> 1.0            (zona neutra)
        rapido..2x rapido               -> smoothstep(1.0 -> fast)
        >= 2x rapido                    -> fast
    """
    if not cfg.velocity_curve_enabled:
        return 1.0

    fps = cfg.velocity_reference_fps
    tremor = cfg.velocity_tremor_threshold * fps
    precision = cfg.velocity_precision_zone * fps
    fast = cfg.velocity_fast_threshold * fps

    if speed < tremor:
        return 0.0
    if speed < precision:
        t = (speed - tremor) / max(1e-9, precision - tremor)
        return lerp(cfg.velocity_slow_factor, 1.0, smoothstep(t))
    if speed < fast:
        return 1.0
    t = clamp((speed - fast) / max(1e-9, fast), 0.0, 1.0)
    return lerp(1.0, cfg.velocity_fast_factor, smoothstep(t))


# ---------------------------------------------------------------------
# Envelope attack/release
# ---------------------------------------------------------------------

class Envelope:
    """Segue um alvo em [0,1] com constantes de tempo separadas.

    Substitui os liga/desliga booleanos: qualquer assistencia que use
    este envelope entra e sai de forma gradual e dependente de ``dt``.
    """

    def __init__(self, attack_seconds: float, release_seconds: float) -> None:
        self._attack = max(1e-4, float(attack_seconds))
        self._release = max(1e-4, float(release_seconds))
        self._value: float = 0.0

    def update(self, target: float, dt: float) -> float:
        target = clamp(target, 0.0, 1.0)
        tau = self._attack if target > self._value else self._release
        alpha = 1.0 - math.exp(-dt / tau)
        self._value += (target - self._value) * alpha
        return self._value

    def force(self, value: float) -> None:
        """Salto imediato — usado apenas por cancelamentos explicitos
        (ex: mao subindo cancela a assistencia inferior na hora)."""
        self._value = clamp(value, 0.0, 1.0)

    def reset(self) -> None:
        self._value = 0.0

    @property
    def value(self) -> float:
        return self._value


# ---------------------------------------------------------------------
# Assistencia da borda inferior
# ---------------------------------------------------------------------

class BottomAssist:
    """Assistencia UNICA e continua pra alcancar a barra de tarefas.

    Substitui a pilha anterior (Y bottom boost + edge snap + border
    creep + gain reforcado de descida na ancora), que somava aceleracao e
    predicao de tres lugares diferentes e ainda teleportava o cursor pro
    ultimo pixel.

    Por que a assistencia e' um GANHO e nao uma velocidade injetada
    -------------------------------------------------------------
    Injetar velocidade (como o border creep fazia) move o cursor por
    conta propria: e' o unico jeito de violar "mao parada => cursor
    parado". Aqui a assistencia MULTIPLICA o movimento descendente que o
    usuario ja esta fazendo:

        prox  = clamp((y - (borda - faixa)) / faixa, 0, 1)
        drive = smoothstep(velocidade descendente / velocidade de intencao)
        ganho = 1 + (ganho_max - 1) * smoothstep(prox) * drive
        dy    = dy * ganho                     (apenas dy > 0)

    Consequencias diretas:

    - ancora parada => dy = 0 => assistencia = 0. Impossivel o cursor
      fugir sozinho, em qualquer configuracao.
    - subir (dy < 0) => ganho 1.0 no MESMO frame, sem rampa de saida.
    - ``smoothstep(prox)`` tem derivada zero na entrada da faixa: o
      ganho comeca em exatamente 1.0 e cresce suave (C1). Nao existe
      "joelho" onde a velocidade aparente pule.
    - a velocidade adicionada e' proporcional a do usuario e tem teto
      explicito (``bottom_max_extra_rate``); nao ha dinamica propria,
      logo nao ha overshoot nem oscilacao.
    """

    def __init__(self, cfg: MotionConfig) -> None:
        self._cfg = cfg
        self._gain: float = 1.0

    def set_config(self, cfg: MotionConfig) -> None:
        self._cfg = cfg

    def gain(self, y: float, vy: float) -> float:
        """Multiplicador do movimento DESCENDENTE em (y, vy).

        Args:
            y: posicao vertical de saida ANTES da assistencia.
            vy: velocidade vertical de saida (unidades/s). Negativa =
                subindo.
        """
        cfg = self._cfg
        if (
            not cfg.bottom_assist_enabled
            or cfg.bottom_band <= 0.0
            or cfg.bottom_max_gain <= 1.0
            or vy <= cfg.bottom_up_epsilon
        ):
            self._gain = 1.0
            return 1.0

        prox = clamp(
            (y - (cfg.bottom_edge - cfg.bottom_band)) / cfg.bottom_band,
            0.0, 1.0,
        )
        if prox <= 0.0:
            self._gain = 1.0
            return 1.0

        drive = smoothstep(
            clamp(vy / max(1e-6, cfg.bottom_intent_speed), 0.0, 1.0)
        )
        gain = 1.0 + (cfg.bottom_max_gain - 1.0) * smoothstep(prox) * drive

        # Teto absoluto da velocidade ADICIONADA, independente de quao
        # rapido o usuario esteja descendo.
        extra = (gain - 1.0) * vy
        if extra > cfg.bottom_max_extra_rate:
            gain = 1.0 + cfg.bottom_max_extra_rate / vy

        self._gain = gain
        return gain

    def reset(self) -> None:
        self._gain = 1.0

    @property
    def strength(self) -> float:
        """Intensidade atual normalizada — util pro HUD de debug."""
        span = self._cfg.bottom_max_gain - 1.0
        if span <= 0.0:
            return 0.0
        return clamp((self._gain - 1.0) / span, 0.0, 1.0)


# ---------------------------------------------------------------------
# Escala filtrada da mao
# ---------------------------------------------------------------------

class HandScaleTracker:
    """Escala aparente da palma, filtrada no tempo e com zona morta."""

    def __init__(self, cfg: MotionConfig) -> None:
        self._cfg = cfg
        self._raw: Optional[float] = None
        self._filtered: Optional[float] = None
        self._lost_seconds: float = 0.0

    def set_config(self, cfg: MotionConfig) -> None:
        self._cfg = cfg

    def update(self, landmarks: Optional[Landmarks], dt: float) -> Optional[float]:
        raw = estimate_palm_scale(landmarks)
        if raw is None:
            # Sem leitura confiavel: mantem a escala anterior. Trocar de
            # ganho com base em landmark ruim e' pior que nao trocar.
            self.notify_lost(dt)
            return self._filtered

        self._lost_seconds = 0.0
        self._raw = raw
        if self._filtered is None:
            self._filtered = raw
            return self._filtered

        deadband = self._filtered * self._cfg.scale_deadband_ratio
        if abs(raw - self._filtered) <= deadband:
            return self._filtered

        alpha = 1.0 - math.exp(
            -dt * 2.0 * math.pi * max(1e-6, self._cfg.scale_filter_hz)
        )
        self._filtered += (raw - self._filtered) * alpha
        return self._filtered

    def notify_lost(self, dt: float) -> None:
        """Contabiliza tempo sem leitura valida; reseta apos o limite."""
        self._lost_seconds += dt
        if self._lost_seconds >= self._cfg.scale_lost_reset_seconds:
            self._raw = None
            self._filtered = None

    def reset(self) -> None:
        self._raw = None
        self._filtered = None
        self._lost_seconds = 0.0

    @property
    def raw(self) -> Optional[float]:
        return self._raw

    @property
    def filtered(self) -> Optional[float]:
        return self._filtered


# ---------------------------------------------------------------------
# Snapshot de debug
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class MotionDebug:
    """Estado interno do frame — so pra HUD/log. Nao sai da maquina."""
    scale_raw: float
    scale_filtered: float
    distance_gain: float
    total_gain: float
    precision_weight: float
    sticky_weight: float
    bottom_strength: float
    speed: float
    reanchored: bool


# ---------------------------------------------------------------------
# Pipeline de movimento
# ---------------------------------------------------------------------

class CursorMotion:
    """Integrador de deltas com ganho adaptativo e precisao continua.

    Uso::

        motion = CursorMotion(MotionConfig())
        out = motion.update(anchor, dt, landmarks=lm, base_sensitivity=1.0)

    Thread-safety: nao seguro. Uma instancia por loop.
    """

    def __init__(self, cfg: Optional[MotionConfig] = None) -> None:
        self._cfg = cfg if cfg is not None else MotionConfig()
        self._scale = HandScaleTracker(self._cfg)
        self._bottom = BottomAssist(self._cfg)
        self._aim_env = Envelope(
            self._cfg.precision_attack_seconds,
            self._cfg.precision_release_seconds,
        )
        self._sticky_env = Envelope(
            self._cfg.precision_attack_seconds,
            self._cfg.precision_release_seconds,
        )

        self._output: Optional[Point] = None
        self._prev_anchor: Optional[Point] = None
        self._distance_gain: float = self._cfg.gain_neutral
        self._gain_primed: bool = False
        self._total_gain: float = 1.0
        self._speed: float = 0.0
        self._speed_filtered: float = 0.0
        self._speed_primed: bool = False
        self._last_delta: Point = (0.0, 0.0)
        self._speed_history: Deque[float] = deque(maxlen=5)
        self._reanchored: bool = False

    # ------------------------------------------------------------- API

    @property
    def config(self) -> MotionConfig:
        return self._cfg

    def set_config(self, cfg: MotionConfig) -> None:
        """Troca a configuracao preservando a POSICAO de saida.

        Usado quando um slider/perfil muda em runtime: o novo ganho vale
        do proximo movimento em diante, o cursor nao se mexe por causa da
        troca.
        """
        self._cfg = cfg
        self._scale.set_config(cfg)
        self._bottom.set_config(cfg)
        self._distance_gain = clamp(
            self._distance_gain,
            min(cfg.gain_near, cfg.gain_far, cfg.gain_neutral),
            max(cfg.gain_near, cfg.gain_far, cfg.gain_neutral),
        )

    def update(
        self,
        anchor: Point,
        dt: float,
        *,
        landmarks: Optional[Landmarks] = None,
        base_sensitivity: float = 1.0,
        aim_target: float = 0.0,
        precision_hold: bool = False,
    ) -> Point:
        """Avanca o pipeline um frame e devolve a posicao de saida.

        Args:
            anchor: ancora normalizada do frame atual (ja calculada UMA
                vez por frame pelo chamador).
            dt: tempo desde o frame anterior, em segundos.
            landmarks: landmarks crus, so pra estimar a escala da palma.
            base_sensitivity: ganho base escolhido pelo usuario (slider).
            aim_target: alvo de precisao em [0,1] (proximidade do pinch,
                shape confirmado, etc). O envelope suaviza a entrada/saida.
            precision_hold: mantem a precisao no maximo (ex: drag ativo).
        """
        cfg = self._cfg
        dt = clamp(dt, 1e-4, cfg.max_dt)
        self._reanchored = False

        scale = self._scale.update(landmarks, dt)

        target_gain = (
            distance_gain(scale, cfg) if scale is not None else cfg.gain_neutral
        )
        previous_gain = self._distance_gain
        if not self._gain_primed:
            # Ancoragem: comeca JA no ganho correto. Rampar a partir do
            # neutro so faria sentido se houvesse movimento acontecendo —
            # e faria a mesma trajetoria render diferente a 30 e 60 FPS,
            # porque a rampa e' um clamp (nao-suave) integrado.
            previous_gain = target_gain
            self._distance_gain = target_gain
            self._gain_primed = True
        else:
            self._distance_gain = rate_limit(
                self._distance_gain, target_gain, cfg.gain_rate_per_second * dt,
            )
        # Integracao TRAPEZOIDAL do ganho: usamos a media entre o ganho do
        # inicio e do fim do passo. Com a soma pelo valor final, a mesma
        # rampa de ganho renderia deslocamentos diferentes a 30 e 60 FPS
        # (erro de primeira ordem em dt); com a media, o erro cai pra
        # segunda ordem e as duas taxas concordam dentro de 1 px.
        effective_gain = 0.5 * (previous_gain + self._distance_gain)

        # Primeiro frame / re-ancoragem: alinha a ENTRADA sem mover a
        # SAIDA. E' isso que impede teleporte quando a mao volta em outro
        # ponto do frame — a saida so e' inicializada quando ainda nao
        # existe (primeiro frame absoluto).
        if self._output is None or self._prev_anchor is None:
            if self._output is None:
                self._output = anchor
            self._prev_anchor = anchor
            self._speed = 0.0
            self._speed_filtered = 0.0
            self._speed_primed = False
            self._last_delta = (0.0, 0.0)
            self._speed_history.clear()
            self._reanchored = True
            self._update_precision(aim_target, precision_hold, dt)
            self._total_gain = self._compose_gain(base_sensitivity, effective_gain)
            return self._output

        raw_dx = anchor[0] - self._prev_anchor[0]
        raw_dy = anchor[1] - self._prev_anchor[1]
        self._prev_anchor = anchor

        self._speed = math.sqrt(raw_dx * raw_dx + raw_dy * raw_dy) / dt
        if not self._speed_primed:
            # Primeira amostra apos ancoragem: o filtro comeca JA no valor
            # medido. Se comecasse do zero, o warm-up duraria um numero
            # fixo de FRAMES — e a mesma trajetoria fisica renderia
            # deslocamentos diferentes a 30 e a 60 FPS.
            self._speed_filtered = self._speed
            self._speed_primed = True
        else:
            alpha = 1.0 - math.exp(
                -dt * 2.0 * math.pi * max(1e-6, cfg.speed_filter_hz)
            )
            self._speed_filtered += (self._speed - self._speed_filtered) * alpha
        curve = velocity_curve_factor(self._speed_filtered, cfg)
        self._update_precision(aim_target, precision_hold, dt)
        self._total_gain = self._compose_gain(base_sensitivity, effective_gain)

        gain = self._total_gain * curve
        dx = raw_dx * gain
        dy = raw_dy * gain

        # Assistencia da borda inferior: MULTIPLICA o movimento
        # descendente do proprio usuario (ver BottomAssist). Nunca soma
        # deslocamento autonomo — por isso nao aparece quando dy == 0.
        dy *= self._bottom.gain(self._output[1], dy / dt)

        out_x = clamp(self._output[0] + dx, 0.0, 1.0)
        out_y = clamp(self._output[1] + dy, 0.0, 1.0)

        self._last_delta = (out_x - self._output[0], out_y - self._output[1])
        self._output = (out_x, out_y)
        # Historico entra DEPOIS do uso pelo sticky deste frame.
        self._speed_history.append(self._speed)
        return self._output

    def hold(self, anchor: Point, dt: float, aim_target: float = 0.0) -> Point:
        """Congela a saida e re-alinha a entrada.

        Usado quando o gesto atual nao move o cursor (PEACE, FIST, PINCH
        antes do drag). Ao voltar a mover, o cursor continua do ponto
        exato onde parou — a continuidade entre shapes vem daqui.

        O envelope de precisao CONTINUA seguindo ``aim_target`` durante o
        congelamento: assim, quando um PINCH sustentado vira DRAG, a
        precisao ja esta engajada em vez de ter que subir do zero.
        """
        dt = clamp(dt, 1e-4, self._cfg.max_dt)
        self._scale.update(None, 0.0)
        self._prev_anchor = anchor
        self._speed = 0.0
        self._speed_filtered = 0.0
        self._speed_primed = False
        self._last_delta = (0.0, 0.0)
        self._speed_history.clear()
        self._sticky_env.update(0.0, dt)
        self._aim_env.update(aim_target, dt)
        self._bottom.reset()
        if self._output is None:
            self._output = anchor
            self._reanchored = True
        return self._output

    def notify_hand_lost(self, dt: float) -> None:
        """Mao ausente neste frame.

        Blink curto: nada muda — a saida fica onde esta e a entrada
        continua valendo. Perda longa: ``HandScaleTracker`` descarta a
        escala e ``request_reanchor`` e' chamado, de forma que o retorno
        da mao NAO produz salto.
        """
        dt = clamp(dt, 1e-4, self._cfg.max_dt)
        self._scale.notify_lost(dt)
        if self._scale.filtered is None:
            self.request_reanchor()

    def request_reanchor(self) -> None:
        """Proxima leitura re-alinha a entrada sem mover a saida."""
        self._prev_anchor = None
        self._bottom.reset()

    def advance_output(self, dx: float, dy: float) -> Point:
        """Desloca a SAIDA diretamente, sem ganho e sem assistencias.

        Existe para o follow-through do ``GestureDetector`` (cursor
        termina o gesto quando a mao sai do FOV). E' um mecanismo
        deliberadamente ISOLADO das assistencias com a mao visivel — por
        isso nao passa pelo ganho nem pela curva da borda inferior.
        """
        if self._output is None:
            return (0.5, 0.5)
        out = (
            clamp(self._output[0] + dx, 0.0, 1.0),
            clamp(self._output[1] + dy, 0.0, 1.0),
        )
        self._last_delta = (out[0] - self._output[0], out[1] - self._output[1])
        self._output = out
        return out

    def soft_reset(self) -> None:
        """Descarta entrada, escala e assistencias; PRESERVA a saida.

        Usado quando a mao some de vez: o cursor fica onde parou e o
        retorno da mao re-ancora a entrada — sem salto, sem voltar pro
        centro da tela.
        """
        self._scale.reset()
        self._bottom.reset()
        self._aim_env.reset()
        self._sticky_env.reset()
        self._prev_anchor = None
        self._distance_gain = self._cfg.gain_neutral
        self._gain_primed = False
        self._speed = 0.0
        self._speed_filtered = 0.0
        self._speed_primed = False
        self._last_delta = (0.0, 0.0)
        self._speed_history.clear()

    def reset(self) -> None:
        """Limpa todo o estado (mao perdida de vez, troca de modo)."""
        self._scale.reset()
        self._bottom.reset()
        self._aim_env.reset()
        self._sticky_env.reset()
        self._output = None
        self._prev_anchor = None
        self._distance_gain = self._cfg.gain_neutral
        self._gain_primed = False
        self._total_gain = 1.0
        self._speed = 0.0
        self._speed_filtered = 0.0
        self._speed_primed = False
        self._last_delta = (0.0, 0.0)
        self._speed_history.clear()
        self._reanchored = False

    # -------------------------------------------------------- internos

    def _update_precision(
        self, aim_target: float, precision_hold: bool, dt: float,
    ) -> None:
        target = 1.0 if precision_hold else clamp(aim_target, 0.0, 1.0)
        self._aim_env.update(target, dt)
        self._sticky_env.update(self._sticky_target(), dt)

    def _sticky_target(self) -> float:
        """Intensidade do sticky, GRADUAL em vez de liga/desliga.

        Sticky = friccao ao DESACELERAR sobre um alvo. A intensidade
        cresce conforme a velocidade atual cai em relacao a media
        recente, em vez de trocar de fator num unico frame.
        """
        cfg = self._cfg
        if not cfg.sticky_enabled:
            return 0.0
        if self._speed < cfg.sticky_min_speed:
            return 0.0
        if len(self._speed_history) < 3:
            return 0.0
        recent = list(self._speed_history)[-3:]
        avg = sum(recent) / len(recent)
        if avg < 1e-6:
            return 0.0
        ratio = self._speed / avg
        threshold = cfg.sticky_deceleration_threshold
        if ratio >= threshold or threshold <= 0.0:
            return 0.0
        return clamp((threshold - ratio) / threshold, 0.0, 1.0)

    def _compose_gain(
        self, base_sensitivity: float, distance_gain_value: float,
    ) -> float:
        """Composicao EXPLICITA e limitada do ganho final.

            base x distancia x precisao

        Nao ha nenhuma outra assistencia escondida multiplicando o mesmo
        delta: a curva balistica entra separada (e visivel) em update(),
        e a borda inferior atua como VELOCIDADE somada, nao como ganho.
        """
        cfg = self._cfg
        precision = (
            lerp(1.0, cfg.aim_slowdown_factor, self._aim_env.value)
            * lerp(1.0, cfg.sticky_friction_factor, self._sticky_env.value)
        )
        total = base_sensitivity * distance_gain_value * precision
        return clamp(total, cfg.total_gain_min, cfg.total_gain_max)

    # ---------------------------------------------------- introspeccao

    @property
    def position(self) -> Optional[Point]:
        return self._output

    @property
    def last_delta(self) -> Point:
        """Ultimo deslocamento aplicado a saida (unidades normalizadas)."""
        return self._last_delta

    @property
    def speed(self) -> float:
        """Velocidade da ANCORA no ultimo frame (unidades/segundo)."""
        return self._speed

    @property
    def distance_gain_value(self) -> float:
        return self._distance_gain

    @property
    def total_gain(self) -> float:
        return self._total_gain

    @property
    def precision_weight(self) -> float:
        return self._aim_env.value

    @property
    def sticky_weight(self) -> float:
        return self._sticky_env.value

    @property
    def bottom_strength(self) -> float:
        return self._bottom.strength

    def debug_snapshot(self) -> MotionDebug:
        """Estado do frame pra HUD/log em modo debug.

        Nao persiste nada, nao toca em disco, nao envia telemetria.
        """
        return MotionDebug(
            scale_raw=self._scale.raw if self._scale.raw is not None else 0.0,
            scale_filtered=(
                self._scale.filtered if self._scale.filtered is not None else 0.0
            ),
            distance_gain=self._distance_gain,
            total_gain=self._total_gain,
            precision_weight=self._aim_env.value,
            sticky_weight=self._sticky_env.value,
            bottom_strength=self._bottom.strength,
            speed=self._speed,
            reanchored=self._reanchored,
        )
