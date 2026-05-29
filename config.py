"""
config.py  (v6.5 - Easier Pinch)
================================

GESTOS:
- Mao aberta 🖐️           → move cursor
- Pinca 🤏                 → clique simples (MAIS FACIL na v6.5)
- Pinca mantida 2s         → iniciar arrasto
- Dois dedos ✌️            → DUPLO CLIQUE (cursor congela)
- Punho ✊                 → cursor congelado
- Mao fora do frame        → pausa

MUDANCAS v6.5 (pinch mais facil de disparar):
1. PINCH_DISTANCE_THRESHOLD: 0.055 → 0.075 (pinca mais "solta" funciona)
2. PINCH_THRESHOLD_FLOOR: novo (piso minimo do threshold adaptativo)
3. PINCH_DUAL_DETECTION: True → False (dual estava rejeitando cliques validos)
4. PINCH_MIN_HOLD_SECONDS: 0.03 → 0.015 (cliques mais rapidos passam)
5. CLICK_COOLDOWN_SECONDS: 0.35 → 0.25 (cliques consecutivos mais rapidos)

Mantem todas melhorias da v6.4:
- Sticky targeting (cursor "agarra" ao mirar)
- Aim assist com pre-ativacao + holdover
- Curva ballistica suavizada
"""

from typing import Literal, Tuple

# ---------------------------------------------------------------------
# CAMERA
# ---------------------------------------------------------------------

CAMERA_INDEX: int = 0
CAMERA_WIDTH: int = 960
CAMERA_HEIGHT: int = 540
CAMERA_FPS_TARGET: int = 60

# ---------------------------------------------------------------------
# MEDIAPIPE HANDS
# ---------------------------------------------------------------------

MAX_NUM_HANDS: int = 1
MIN_DETECTION_CONFIDENCE: float = 0.6
MIN_TRACKING_CONFIDENCE: float = 0.4
MODEL_COMPLEXITY: int = 0
"""
0=lite, 1=full.

PERF: lite reduz inference em ~40-50% vs full. Gesture pinch e' robusto
o suficiente pra rodar com lite sem perda perceptivel. Volta pra 1 se
notar drop na deteccao em iluminacao ruim ou mao longe da camera.
"""

INFERENCE_PRE_RESIZE_ENABLED: bool = False
"""
PERF: redimensiona o frame BGR antes de enviar pro MediaPipe.
MediaPipe rescale internamente — fazer antes corta memcpy + acelera.

Default False (compatibilidade). Liga pra testar ganho.
"""

INFERENCE_PRE_RESIZE_WIDTH: int = 320
"""Largura alvo do resize. Altura calculada mantendo aspect ratio."""

CV2_USE_POLLKEY: bool = True
"""
PERF: usa cv2.pollKey() (non-blocking) em vez de cv2.waitKeyEx(1).

waitKeyEx(1) no Windows respeita o timer default de 15.6 ms → cap em
~64 FPS na pratica. pollKey() (disponivel em opencv >= 4.7) nao tem
esse cap.

Fallback automatico pra waitKeyEx se pollKey nao existir na versao
de cv2 instalada. Coloca False se notar problema de window refresh.
"""

# ---------------------------------------------------------------------
# CURSOR — PONTO DE ANCORA
# ---------------------------------------------------------------------

CURSOR_ANCHOR_LANDMARK: int = 9

# ---------------------------------------------------------------------
# POSITION HOLD
# ---------------------------------------------------------------------

POSITION_HOLD_FRAMES: int = 2
"""
PERF/UX: 3 → 2 frames. Cursor reage 1 frame mais rapido a mudanca de
posicao. Pequeno mas perceptivel. Volta pra 3 se notar instabilidade.
"""

# ---------------------------------------------------------------------
# AIM ASSIST
# ---------------------------------------------------------------------

AIM_ASSIST_ENABLED: bool = True
AIM_ASSIST_SLOWDOWN_FACTOR: float = 0.40
AIM_ASSIST_TRIGGER_SHAPES: Tuple[str, ...] = ("peace", "pinch")
AIM_ASSIST_PRE_ACTIVATION: bool = True
AIM_ASSIST_PRE_PINCH_THRESHOLD: float = 0.12
"""v6.5: 0.10 → 0.12 (combina com novo threshold de pinca)"""

AIM_ASSIST_HOLDOVER_SECONDS: float = 0.30
PRECISION_MODE_DEAD_ZONE: int = 4

# ---------------------------------------------------------------------
# STICKY TARGETING
# ---------------------------------------------------------------------

STICKY_TARGETING_ENABLED: bool = True
STICKY_DECELERATION_THRESHOLD: float = 0.7
STICKY_FRICTION_FACTOR: float = 0.75
STICKY_MIN_VELOCITY: float = 0.005

# ---------------------------------------------------------------------
# CURVA BALLISTICA
# ---------------------------------------------------------------------

VELOCITY_CURVE_ENABLED: bool = True
VELOCITY_TREMOR_THRESHOLD: float = 0.003
VELOCITY_PRECISION_ZONE: float = 0.025
VELOCITY_SLOW_FACTOR: float = 0.55
VELOCITY_FAST_THRESHOLD: float = 0.12
VELOCITY_FAST_FACTOR: float = 1.15

# ---------------------------------------------------------------------
# DPI ADAPTATIVO
# ---------------------------------------------------------------------

DPI_ADAPTIVE_ENABLED: bool = True
HAND_SIZE_REFERENCE: float = 0.22
DPI_MULTIPLIER_MIN: float = 0.7
DPI_MULTIPLIER_MAX: float = 1.4
DPI_FIXED_MULTIPLIER: float = 0.85

# ---------------------------------------------------------------------
# SUAVIZACAO
# ---------------------------------------------------------------------

SmoothingStrategy = Literal["ema", "one_euro"]
SMOOTHING_STRATEGY: SmoothingStrategy = "one_euro"

SMOOTHING_FACTOR: float = 0.6

ONE_EURO_FREQ: float = 60.0
ONE_EURO_MIN_CUTOFF: float = 1.2
ONE_EURO_BETA: float = 0.020
ONE_EURO_D_CUTOFF: float = 1.0
DEAD_ZONE_PIXELS: int = 1

# ---------------------------------------------------------------------
# PINCA (v6.5: MAIS FACIL DE DISPARAR)
# ---------------------------------------------------------------------

PINCH_DISTANCE_THRESHOLD: float = 0.075
"""
v6.5: 0.055 → 0.075 (+36% mais permissivo).
Pinca natural (dedos proximos mas nao encostando) agora funciona.
Antes: precisava encostar polegar no indicador para disparar.
"""

PINCH_THRESHOLD_FLOOR: float = 0.045
"""
NOVO v6.5: piso MINIMO do threshold apos escala adaptativa.
Quando mao esta longe (hand_size < reference), o threshold cai proporcionalmente.
Sem o piso: mao a 0.10 fazia threshold virar 0.034 (impossivel de fechar).
Com piso 0.045: garantia de que nunca fica absurdamente apertado.
"""

PINCH_ADAPTIVE_TO_HAND_SIZE: bool = True

PINCH_DUAL_DETECTION: bool = False
"""
v6.5: True → False (DESABILITADO).
A deteccao dual (distancia + velocidade de aproximacao) estava
REJEITANDO pincas legitimas quando o usuario relaxava os dedos
para soltar. Sem ela, deteccao volta a ser confiavel.
A protecao contra falsos positivos vem do debounce de 2 frames + threshold
mais cuidadoso.
"""

PINCH_VELOCITY_THRESHOLD: float = 0.008  # ainda existe se voce quiser reabilitar

PINCH_MIN_HOLD_SECONDS: float = 0.015
"""
NOVO v6.5: tempo minimo da pinca para contar como click.
Antes era hardcoded em 0.03s (30ms). Agora 0.015s (15ms).
Permite cliques mais rapidos e fluidos.
"""

# ---------------------------------------------------------------------
# CLIQUE DIREITO — pinca polegar+medio (NOVO v6.9)
# ---------------------------------------------------------------------

PINCH_MIDDLE_THRESHOLD: float = 0.075
"""
v6.9: distancia maxima polegar (lm 4) → ponta do dedo medio (lm 12)
para classificar como pinca do clique direito.

Mesmo valor da pinca normal (PINCH_DISTANCE_THRESHOLD) para manter
sensacao consistente entre os dois gestos.
"""

PINCH_MIDDLE_INDEX_GUARD: float = 0.090
"""
v6.9: distancia MINIMA polegar → indicador (lm 4 → lm 8) para
permitir o clique direito.

Por que: ao fechar polegar+medio, o indicador tende a se aproximar
junto (mecanica natural da mao). Sem essa guarda, dispararia pinca
normal (CLICK) tambem. Esse threshold forca o usuario a manter o
indicador afastado do polegar para o clique direito ser reconhecido.

Resultado: pinca normal e clique direito ficam mutuamente exclusivos.
"""

# ---------------------------------------------------------------------
# DRAG
# ---------------------------------------------------------------------

DRAG_HOLD_SECONDS: float = 1.5
"""
1.5s = aggressive: drag dispara rapido.

Compat: press-to-click ja disparou em ~0ms. Quem so quer clicar
solta antes de 1.5s → sem drag acidental. Mas pinch sustentado >1.5s
sem mover muito ja entra em drag — risco maior se voce hesitar
durante um clique.
"""

# ---------------------------------------------------------------------
# CLIQUE E COOLDOWNS (v6.5: cliques consecutivos mais rapidos)
# ---------------------------------------------------------------------

CLICK_COOLDOWN_SECONDS: float = 0.25
"""v6.5: 0.35 → 0.25 (cliques consecutivos mais rapidos)"""

DOUBLE_CLICK_COOLDOWN_SECONDS: float = 2.5
DOUBLE_CLICK_WINDOW_SECONDS: float = 0.35

# ---------------------------------------------------------------------
# HISTERESE DE GESTOS
# ---------------------------------------------------------------------

GESTURE_DEBOUNCE_FRAMES: int = 2
GESTURE_EXIT_FRAMES: int = 3

# ---------------------------------------------------------------------
# TELA E MAPEAMENTO
# ---------------------------------------------------------------------

SCREEN_MARGIN_PERCENTAGE: float = 0.18
"""
UX: 0.20 → 0.18. Zona morta menor nas bordas → mesma area de mao cobre
mais tela. Cursor sente "mais grudado" no movimento. Sutil.
"""

# ---------------------------------------------------------------------
# DEBUG E PREVIEW
# ---------------------------------------------------------------------

ENABLE_PREVIEW: bool = True
SHOW_FPS: bool = True
SHOW_GESTURE_NAME: bool = True
SHOW_DPI: bool = True
SHOW_DRAG_PROGRESS: bool = True
SHOW_AIM_ASSIST: bool = True
DRAW_LANDMARKS: bool = True
WINDOW_NAME: str = "AI Virtual Mouse v6.5 — ESC to quit"

# ---------------------------------------------------------------------
# JANELA DE EXIBICAO (NOVO v6.9 — UX webcam de streamer)
# ---------------------------------------------------------------------

DISPLAY_COMPACT_WIDTH: int = 480
DISPLAY_COMPACT_HEIGHT: int = 270
"""
Tamanho da janela em modo COMPACTO (sem painel de settings).
A camera continua capturando em CAMERA_WIDTH x CAMERA_HEIGHT (resolucao
cheia para a IA), mas o frame e redimensionado antes de ser exibido.
Detec
cao da mao nao perde precisao."""

DISPLAY_EXPANDED_WIDTH: int = 760
DISPLAY_EXPANDED_HEIGHT: int = 540
"""
Tamanho quando o painel de settings esta aberto (tecla S).
Mais alto e mais largo para acomodar o painel lateral sem cortar
nem distorcer os controles.
"""

DISPLAY_POSITION_BOTTOM_RIGHT: bool = True
"""
Se True, posiciona a janela automaticamente no canto inferior direito
da tela ao iniciar (estilo webcam de streamer/OBS). Se False, deixa o
sistema operacional decidir a posicao inicial.
"""

DISPLAY_POSITION_MARGIN: int = 20
"""Distancia em pixels da borda da tela ao posicionar automaticamente."""

DISPLAY_ALWAYS_ON_TOP: bool = True
"""
NOVO v6.9: mantem a janela da webcam SEMPRE visivel acima de outras janelas.

Por que: sem isso, ao clicar em qualquer outra aplicacao (browser, codigo,
documentos) a janela da webcam vai pra tras e voce nao consegue ver sua
mao para controlar o cursor.

Mesmo comportamento que webcams de streamers no OBS Studio: a previa fica
sempre vivivel acima de tudo, mas voce continua podendo clicar e trabalhar
nas outras janelas normalmente.

Se quiser desabilitar (ex: durante apresentacao em tela cheia), use a tecla
T para alternar em runtime, ou mude esta constante para False.
"""

DISPLAY_TOPMOST_TOGGLE_KEY: str = "t"
"""Tecla para alternar always-on-top em runtime. Use 't'."""

# ---------------------------------------------------------------------
# HOLOGRAMA (mao virtual desenhada na tela)
# ---------------------------------------------------------------------

HOLOGRAM_ENABLED: bool = False
"""
Se True, abre uma janela fullscreen transparente sobre o desktop e desenha
uma mao "holografica" pequena seguindo o cursor. Pode ser ligada em runtime
pela tecla H.

Default False porque adiciona overhead visual. Em modo demonstracao/onboarding
ela faz a experiencia parecer mais magica; em uso prolongado pode distrair.
"""

HOLOGRAM_TOGGLE_KEY: str = "h"
"""Tecla pra alternar o holograma em runtime."""

HOLOGRAM_OPACITY: float = 0.70
"""
Opacidade base. Camadas (palm/finger outline, particles, tips) usam
fracoes desse valor. 0.70 + paleta azul ciano da contraste suficiente
sem virar barulho.
"""

HOLOGRAM_HAND_SIZE_PX: int = 180
"""
Tamanho do bounding box da mao em pixels. 180 da presenca visual sem
estorvar — com curvas Catmull-Rom + glow PySide6 a mao ja parece anatomica.
"""

HOLOGRAM_FPS: int = 30
"""Taxa de redesenho. 30 Hz e' suave + economico em CPU."""

HOLOGRAM_COLOR_BONE: str = "#00d4ff"
"""
Cor primaria do holograma — azul neon ciano. Estilo HUD futurista.
Usado nas linhas/contornos da mao. Substitui o vermelho do MVP.
"""

HOLOGRAM_COLOR_POINT: str = "#e0f7ff"
"""
Cor dos pontos brilhantes (tips, particulas). Branco azulado para
contraste com o azul ciano do contorno.
"""

HOLOGRAM_PARTICLES_ENABLED: bool = False
"""
Liga nuvem densa de particles dentro da silhueta. Default False = visual
clean moderno (sem ruido visual). True = particle cloud densa.
"""

HOLOGRAM_PARTICLE_COUNT: int = 180
"""
Numero de particles na nuvem. 180 = densidade boa sem custo de FPS.
Aumentar pra ~300 da look mais "particle cloud". Acima disso pode
comecar a impactar perceptivelmente.
"""

HOLOGRAM_VIEW_DORSAL: bool = False
"""
Vista anatomica do holograma.

True  = dorsal (costas da mao na tela) — espelha X dos landmarks
False = palm view — segue webcam mirroreada (sua mao real "transmitida"
        na tela em 3a pessoa)

Default False = visual coerente com a imagem da webcam.
"""

HOLOGRAM_TRANSPARENT_COLOR: str = "#010203"
"""
Cor "magica" pintada como fundo do canvas que vira transparente no compositor
do Windows. Tem que ser uma cor que nao apareca em mais nada desenhado.
"""

# ---------------------------------------------------------------------
# PERFORMANCE TELEMETRY (instrumentation per estagio do tick loop)
# ---------------------------------------------------------------------

PERF_TELEMETRY_ENABLED: bool = True
"""
Liga timing por estagio (camera/inference/gesture/events/hologram/preview/
waitkey). Reporta p50/p99 a cada N ticks via logger.info.

Custo: ~100ns por entry/exit (desprezivel). Coloca False em producao se
quiser zero overhead.
"""

PERF_TELEMETRY_WINDOW: int = 120
"""Tamanho do rolling window de samples por estagio (= 2s a 60 FPS)."""

PERF_TELEMETRY_REPORT_EVERY: int = 120
"""Logar report a cada N ticks (= 2s a 60 FPS)."""

# ---------------------------------------------------------------------
# SEGURANCA
# ---------------------------------------------------------------------

PYAUTOGUI_FAILSAFE: bool = False
PYAUTOGUI_PAUSE: float = 0.0

# ---------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------

LOG_LEVEL: str = "INFO"