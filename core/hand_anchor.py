"""
core.hand_anchor
================

Ancora ROBUSTA da mao pra controle de cursor.

Problema:
- Ancorar o cursor em UM landmark (palma=9, midpoint pinch=(4+8)/2, etc) faz
  o controle desabar quando esse ponto fica ocluido:
    * webcam cortando o centro da mao em cantos do frame
    * mao de perfil esconde fingertips ou MCPs
    * iluminacao ruim derruba a confianca de landmarks especificos
  Resultado: cursor saltita, pula entre estimativas ruins, ou para de seguir.

Solucao (este modulo):
- Calcula UMA ancora ponderada usando TODOS os 21 landmarks com pesos
  baseados em (a) confiabilidade anatomica, (b) proximidade da borda do
  frame e (c) estabilidade temporal local.
- Mantem historico curto por landmark pra detectar instabilidade (sintoma
  classico de oclusao).
- Histerese: blende com a ultima ancora boa quando a confianca cai bruscamente
  -> sem saltos visiveis quando um landmark crítico falha.

Design:
- Modulo puro: sem I/O, sem dependencia de Qt/cv2. So math.
- Stateful (historico + ultima ancora) atras de uma classe — facil de testar
  ou substituir.
- Saida normalizada em [0, 1] x [0, 1] — mesmo espaco dos landmarks
  MediaPipe. Quem mapeia pra tela e' o CursorController (sem mudancas).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------
# Pesos anatomicos por landmark MediaPipe Hands
# ---------------------------------------------------------------------
# Logica:
# - Landmarks 5, 9, 13, 17 (MCPs) sao a "espinha" da palma; movem so quando
#   a mao move. Pegam peso maximo.
# - Wrist (0) e' tambem estavel mas pode ficar fora do frame se a webcam
#   estiver alta. Peso intermediario.
# - Fingertips (4, 8, 12, 16, 20) sao discriminativos pra orientacao mas
#   se mexem com o gesto -> peso medio.
# - PIPs/DIPs (6, 7, 10, 11, 14, 15, 18, 19) seguem fingertips, peso baixo.
# - Thumb base/IP (1, 2, 3) tem anatomia fora do plano da palma; peso baixo.
#
# Soma maxima ≈ 12.8 (usado pra normalizar confianca).
_ANATOMY_WEIGHTS: Tuple[float, ...] = (
    0.55,                         # 0  wrist
    0.25, 0.30, 0.35, 0.55,       # 1-4  thumb CMC, MCP, IP, TIP
    1.00, 0.55, 0.45, 0.70,       # 5-8  index MCP, PIP, DIP, TIP
    1.00, 0.60, 0.50, 0.70,       # 9-12 middle MCP, PIP, DIP, TIP
    0.95, 0.55, 0.45, 0.65,       # 13-16 ring
    0.85, 0.45, 0.40, 0.55,       # 17-20 pinky
)
_ANATOMY_SUM_MAX: float = sum(_ANATOMY_WEIGHTS)


# ---------------------------------------------------------------------
# Estado por landmark — historico curto pra scorar estabilidade
# ---------------------------------------------------------------------

@dataclass
class _LandmarkHistory:
    """Janela rolante de posicoes (x, y) por landmark."""
    xs: Deque[float] = field(default_factory=lambda: deque(maxlen=8))
    ys: Deque[float] = field(default_factory=lambda: deque(maxlen=8))


# ---------------------------------------------------------------------
# Saida tipada
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class AnchorResult:
    """Resultado de uma chamada compute(). Imutavel."""
    x: float            # [0, 1] — coord normalizada (MediaPipe space)
    y: float            # [0, 1]
    confidence: float   # [0, 1] — quao "completa" e' a leitura
    used_landmarks: int # quantos landmarks efetivamente pesaram


# ---------------------------------------------------------------------
# Ancora robusta
# ---------------------------------------------------------------------

class RobustHandAnchor:
    """
    Calcula uma ancora unica e estavel a partir de TODOS os landmarks.

    Uso:
        anchor = RobustHandAnchor()
        ...
        result = anchor.compute(hand.landmarks)
        cursor_pos = (result.x, result.y)

    Thread-safety: nao seguro. Uma instancia por thread/loop.
    """

    # Constantes ajustaveis — defaults bem testados.
    _EDGE_MARGIN: float = 0.06           # 6% das bordas do frame
    # Taper ASSIMETRICO: margem inferior bem menor que as demais.
    # A borda de baixo e' o caminho da taskbar — com taper simetrico os
    # landmarks inferiores (wrist/MCPs) perdiam peso cedo e a media
    # ponderada puxava a ancora pra CIMA enquanto o usuario descia.
    _EDGE_MARGIN_BOTTOM: float = 0.02
    _STAB_WINDOW: int = 8                # frames p/ medir variancia
    _STAB_SCALE: float = 220.0           # quao agressivamente penaliza variancia
    _HYSTERESIS_BLEND: float = 0.35      # alpha do blend em low-confidence
    _CONFIDENCE_DROP_RATIO: float = 0.5  # < 0.5 * last_conf = "queda brusca"
    _CONFIDENCE_FLOOR: float = 0.40      # abaixo disso ja considera frame ruim
    _RESET_AFTER_LOST_FRAMES: int = 8    # apos N frames sem mao -> reset

    # Extrapolacao por velocidade nas BORDAS do frame: quando a ancora
    # esta proxima da borda do quadro da camera (landmarks ja saindo, ou
    # prestes a sair), continuamos o cursor usando a velocidade recente.
    # Sem isso, o cursor "trava" 5-15% antes da borda real da tela, que
    # e' exatamente onde estao os controles importantes (taskbar, fechar,
    # abas, etc.). Duas zonas de atuacao:
    #
    #   1. Proximidade NORMAL (< _EDGE_PROXIMITY): ativa quando a confianca
    #      cai (mao parcialmente fora). Gain proporcional a (1 - confidence).
    #   2. Proximidade CRITICA (< _EDGE_CRITICAL_PROXIMITY): ativa SEMPRE,
    #      mesmo com confidence alta — porque na pratica MediaPipe ainda
    #      detecta a mao bem mas o anchor "trava" porque os landmarks
    #      acessiveis sao todos da palma central, sem alavanca pra borda.
    _EDGE_EXTRAPOLATION_ENABLED: bool = True
    _EDGE_VELOCITY_GAIN: float = 2.2      # quanto da velocidade aplicar
    _EDGE_PROXIMITY: float = 0.30         # ancora a < 30% da borda = "perto"
    _EDGE_CRITICAL_PROXIMITY: float = 0.08  # < 8% = critico, sempre ativo
    _EDGE_CRITICAL_GAIN: float = 1.0      # gain quando em zona critica
    # Gain de descida na zona critica.
    #
    # 1.0 (era 1.4, antes 2.6): simetrico com as outras bordas. A
    # assistencia ESPECIFICA da borda inferior passou a viver em um
    # unico lugar — core/cursor_motion.py (BottomAssist), com teto de
    # velocidade e aceleracao e dependencia de intencao.
    #
    # Somar um reforco aqui TAMBEM significava empilhar predicao (esta
    # extrapolacao) com aceleracao (boost/creep do CursorController) em
    # pontos diferentes do pipeline, sem teto conjunto: era impossivel
    # raciocinar sobre a velocidade final do cursor perto do fundo.
    # A extrapolacao generica de borda (todas as quatro) continua.
    _EDGE_CRITICAL_GAIN_DOWN: float = 1.0

    def __init__(
        self,
        *,
        edge_margin: Optional[float] = None,
        stability_window: Optional[int] = None,
        hysteresis_blend: Optional[float] = None,
    ) -> None:
        self._edge_margin = (
            float(edge_margin) if edge_margin is not None else self._EDGE_MARGIN
        )
        self._stab_window = (
            int(stability_window) if stability_window is not None
            else self._STAB_WINDOW
        )
        self._hyst_alpha = (
            float(hysteresis_blend) if hysteresis_blend is not None
            else self._HYSTERESIS_BLEND
        )

        self._histories: Optional[List[_LandmarkHistory]] = None
        self._last_anchor: Optional[Tuple[float, float]] = None
        self._last_confidence: float = 0.0
        self._lost_frames: int = 0
        # Velocidade rolante (delta entre frames consecutivos) — usada
        # para extrapolar a ancora quando a confianca cai perto das bordas.
        self._last_velocity: Tuple[float, float] = (0.0, 0.0)

    # ------------------------------------------------------------- API

    def compute(
        self,
        landmarks: Optional[Sequence[Tuple[float, float, float]]],
    ) -> AnchorResult:
        """
        Calcula a ancora robusta da mao no frame atual.

        Args:
            landmarks: 21 tuplas (x, y, z) normalizadas (MediaPipe).
                None ou len < 21 = mao nao detectada nesse frame.

        Returns:
            AnchorResult com (x, y, confidence, used_landmarks).
            Se mao nao detectada: retorna ultima ancora (confidence=0)
            ate completar _RESET_AFTER_LOST_FRAMES, depois (0.5, 0.5).
        """
        if landmarks is None or len(landmarks) < 21:
            self._lost_frames += 1
            if self._lost_frames >= self._RESET_AFTER_LOST_FRAMES:
                self.reset()
                return AnchorResult(0.5, 0.5, 0.0, 0)
            if self._last_anchor is not None:
                lx, ly = self._last_anchor
                return AnchorResult(lx, ly, 0.0, 0)
            return AnchorResult(0.5, 0.5, 0.0, 0)

        self._lost_frames = 0

        # Lazy-init / reset do historico se o numero de landmarks mudou
        n = len(landmarks)
        if self._histories is None or len(self._histories) != n:
            self._histories = [_LandmarkHistory() for _ in range(n)]

        margin = self._edge_margin
        sum_wx = 0.0
        sum_wy = 0.0
        sum_w = 0.0
        used = 0

        for i in range(n):
            lm = landmarks[i]
            x = float(lm[0])
            y = float(lm[1])

            hist = self._histories[i]
            hist.xs.append(x)
            hist.ys.append(y)

            # (1) Anatomia: peso fixo por landmark
            w_anat = _ANATOMY_WEIGHTS[i] if i < len(_ANATOMY_WEIGHTS) else 0.4

            # (2) Frame edge taper: cai pra 0 nas bordas linearmente
            # (margem inferior menor — ver _EDGE_MARGIN_BOTTOM)
            edge_factor = _edge_factor(
                x, y, margin, bottom_margin=self._EDGE_MARGIN_BOTTOM,
            )
            if edge_factor <= 0.0:
                continue  # landmark fora do quadro util — nao contribui

            # (3) Estabilidade: penaliza alta variancia no historico curto
            stab_factor = _stability_factor(
                hist.xs, hist.ys, scale=self._STAB_SCALE,
            )

            w = w_anat * edge_factor * stab_factor
            if w <= 1e-6:
                continue

            sum_wx += w * x
            sum_wy += w * y
            sum_w += w
            used += 1

        if sum_w <= 1e-6:
            # Caso degenerado: nenhum landmark passou os filtros.
            # Mantem ultima ancora pra nao quebrar o cursor.
            if self._last_anchor is not None:
                lx, ly = self._last_anchor
                return AnchorResult(lx, ly, 0.0, 0)
            return AnchorResult(0.5, 0.5, 0.0, 0)

        ax = sum_wx / sum_w
        ay = sum_wy / sum_w

        # Confianca normalizada: peso total / (40% do teto teorico de pesos).
        # 40% representa "mao razoavelmente visivel" — calibrado empiricamente.
        confidence = min(1.0, sum_w / max(1e-6, _ANATOMY_SUM_MAX * 0.4))

        # Histerese: queda brusca + valor absoluto baixo => blend com ultima
        # ancora. Sem isso, perder o thumb de repente joga a ancora pra
        # palma (que e' onde os pesos remanescentes ficam) e o cursor pula.
        if (
            self._last_anchor is not None
            and confidence < self._last_confidence * self._CONFIDENCE_DROP_RATIO
            and confidence < self._CONFIDENCE_FLOOR
        ):
            lx, ly = self._last_anchor
            a = self._hyst_alpha
            ax = a * ax + (1.0 - a) * lx
            ay = a * ay + (1.0 - a) * ly

        # Edge velocity extrapolation em duas zonas:
        # - Critica (< 8% da borda): ativa SEMPRE, gain fixo. Mao ja chegou
        #   no limite do FOV; aceleramos pro canto da tela mesmo com tracker
        #   confiante porque o anchor "trava" sem alavanca de landmarks.
        # - Normal (< 30% da borda): ativa quando confianca cai. Gain
        #   proporcional a (1 - confidence) — quanto pior a deteccao,
        #   mais peso na velocidade recente.
        # Soma direcional preservada — so projeta se a velocidade estava
        # indo PRA aquela borda (evita pulos espurios).
        if self._EDGE_EXTRAPOLATION_ENABLED and self._last_anchor is not None:
            edge_dx = min(ax, 1.0 - ax)
            edge_dy = min(ay, 1.0 - ay)
            in_critical = (
                edge_dx < self._EDGE_CRITICAL_PROXIMITY
                or edge_dy < self._EDGE_CRITICAL_PROXIMITY
            )
            in_normal = (
                edge_dx < self._EDGE_PROXIMITY
                or edge_dy < self._EDGE_PROXIMITY
            )
            should_extrapolate = in_critical or (in_normal and confidence < 0.7)
            if should_extrapolate:
                vx, vy = self._last_velocity
                going_to_edge_x = (vx < 0 and ax < 0.5) or (vx > 0 and ax > 0.5)
                going_to_edge_y = (vy < 0 and ay < 0.5) or (vy > 0 and ay > 0.5)
                if in_critical:
                    gain_x = self._EDGE_CRITICAL_GAIN
                    # Descendo (vy > 0) o gain e' reforcado — unica
                    # borda onde a geometria da camera trava o alcance.
                    gain_y = (
                        self._EDGE_CRITICAL_GAIN_DOWN if vy > 0
                        else self._EDGE_CRITICAL_GAIN
                    )
                else:
                    gain_x = gain_y = (
                        self._EDGE_VELOCITY_GAIN * (1.0 - confidence)
                    )
                if going_to_edge_x:
                    ax += vx * gain_x
                if going_to_edge_y:
                    ay += vy * gain_y
                ax = 0.0 if ax < 0.0 else (1.0 if ax > 1.0 else ax)
                ay = 0.0 if ay < 0.0 else (1.0 if ay > 1.0 else ay)

        # Atualiza velocidade rolante (smoothing leve pra nao espelhar
        # tremor frame-a-frame).
        if self._last_anchor is not None:
            lx, ly = self._last_anchor
            new_vx = ax - lx
            new_vy = ay - ly
            self._last_velocity = (
                0.6 * self._last_velocity[0] + 0.4 * new_vx,
                0.6 * self._last_velocity[1] + 0.4 * new_vy,
            )

        self._last_anchor = (ax, ay)
        self._last_confidence = confidence
        return AnchorResult(ax, ay, confidence, used)

    def reset(self) -> None:
        """Limpa todo o estado. Use quando a mao some por muitos frames."""
        self._histories = None
        self._last_anchor = None
        self._last_confidence = 0.0
        self._lost_frames = 0
        self._last_velocity = (0.0, 0.0)

    # --------------------------------------------------------- introspeccao

    @property
    def last_confidence(self) -> float:
        """Confianca do ultimo compute(). Util pra HUD/debug."""
        return self._last_confidence


# ---------------------------------------------------------------------
# Helpers puros (modulo level — testaveis sozinhos)
# ---------------------------------------------------------------------

def _edge_factor(
    x: float,
    y: float,
    margin: float,
    bottom_margin: Optional[float] = None,
) -> float:
    """
    Taper linear: 1.0 dentro do frame com folga; 0.0 nas bordas exatas.

    Geometric AND: precisa estar OK em x E y.

    `bottom_margin` habilita taper ASSIMETRICO no eixo y: a borda
    inferior e' o caminho natural da mao ao mirar a taskbar. Com taper
    simetrico, os landmarks de baixo (wrist, MCPs) perdiam peso cedo
    demais e a media ponderada puxava a ancora pra CIMA — na direcao
    oposta ao movimento do usuario. Margem inferior menor mantem esses
    landmarks contribuindo ate quase sairem do quadro.
    """
    if margin <= 0.0:
        return 1.0
    bm = bottom_margin if bottom_margin is not None else margin
    fx = min(x, 1.0 - x) / margin
    fy = min(y / margin, (1.0 - y) / max(bm, 1e-6))
    fx = 0.0 if fx < 0.0 else (1.0 if fx > 1.0 else fx)
    fy = 0.0 if fy < 0.0 else (1.0 if fy > 1.0 else fy)
    return fx * fy


def _stability_factor(
    xs: Deque[float],
    ys: Deque[float],
    *,
    scale: float,
) -> float:
    """
    Score de estabilidade ∈ (0, 1] a partir da variancia local do landmark.

    var = 0   -> 1.0  (perfeitamente estavel)
    var alto  -> ~0   (chacoalhando = provavelmente predicted/instavel)

    Hiperbole 1 / (1 + scale * var) — suave e sem zeros bruscos.
    """
    n = len(xs)
    if n < 2:
        return 1.0
    # Mean
    mx = sum(xs) / n
    my = sum(ys) / n
    # Sum of squared deviations
    ssd = 0.0
    for hx, hy in zip(xs, ys):
        dx = hx - mx
        dy = hy - my
        ssd += dx * dx + dy * dy
    var = ssd / n
    return 1.0 / (1.0 + scale * var)
