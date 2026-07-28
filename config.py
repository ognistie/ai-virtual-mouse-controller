"""
config.py
=========

Configuracao do AI Virtual Mouse Controller.

Gestos:
- Mao aberta 🖐️           → move cursor
- Pinca 🤏                 → clique simples
- Pinca mantida 1.5s       → iniciar arrasto
- Polegar+medio 🤞         → clique direito
- Dois dedos ✌️            → duplo clique
- Punho ✊                 → cursor congelado
- Mao fora do frame        → pausa
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
"""0=lite, 1=full.

Lite reduz inference em ~40-50% vs full sem perda perceptivel no pinch.
Volta pra 1 se notar drop na deteccao em iluminacao ruim ou mao longe.
"""

INFERENCE_PRE_RESIZE_ENABLED: bool = False
"""Redimensiona o frame BGR antes de enviar pro MediaPipe.

MediaPipe rescale internamente — fazer antes corta memcpy + acelera.
Default False (compatibilidade). Liga pra testar ganho.
"""

INFERENCE_PRE_RESIZE_WIDTH: int = 320
"""Largura alvo do resize. Altura calculada mantendo aspect ratio."""

CV2_USE_POLLKEY: bool = True
"""Usa cv2.pollKey() (non-blocking) em vez de cv2.waitKeyEx(1).

waitKeyEx(1) no Windows respeita o timer default de 15.6 ms → cap em
~64 FPS na pratica. pollKey() (opencv >= 4.7) nao tem esse cap.
Fallback automatico se pollKey nao existir. Coloca False se notar
problema de window refresh.
"""

# ---------------------------------------------------------------------
# CURSOR — PONTO DE ANCORA
# ---------------------------------------------------------------------
#
# Opcoes:
#   -2 = ANCORA ROBUSTA DA MAO TODA (default)
#        Combinacao ponderada de TODOS os 21 landmarks, com pesos por
#        anatomia, proximidade da borda do frame e estabilidade local.
#        Histerese suaviza queda de confianca. Resolve casos onde a
#        webcam nao enxerga o centro da palma — o cursor migra
#        organicamente pros landmarks ainda visiveis sem saltos.
#        Ver core/hand_anchor.py.
#   -1 = MIDPOINT DO PINCH (polegar 4 + indicador 8)
#        Alinha 1-pra-1 com a ancora do holograma; pinch fecha sobre
#        o cursor. Vulneravel se polegar OU indicador for ocluido.
#    0 = pulso
#    8 = ponta do indicador
#    9 = palma (middle MCP)
#   12 = ponta do medio
#
CURSOR_ANCHOR_LANDMARK: int = -2

# ---------------------------------------------------------------------
# POSITION HOLD
# ---------------------------------------------------------------------

POSITION_HOLD_FRAMES: int = 2
"""Frames de hold antes de aceitar mudanca de posicao. Sobe pra 3 se
notar instabilidade."""

CURSOR_FOLLOWTHROUGH_FRAMES: int = 6
"""Janela de "cinematic prediction" quando a mao sai do FOV.

A 30fps ≈ 200ms. Cursor continua na direcao da ultima velocidade
conhecida com decay exponencial, em vez de travar abrupto. Permite
acertar canto/borda mesmo quando a mao ja saiu do quadro util.
"""

CURSOR_FOLLOWTHROUGH_DAMPING: float = 0.82
"""Decay exponencial da velocidade no follow-through.

0.82 a cada frame → cursor cobre ~80% da distancia maxima em 4-5 frames
e desacelera suave. < 0.7 = cursor "freia" rapido (pouco impulso).
> 0.9 = cursor "desliza" muito (impreciso).
"""

# ---------------------------------------------------------------------
# AIM ASSIST
# ---------------------------------------------------------------------

AIM_ASSIST_ENABLED: bool = True
AIM_ASSIST_SLOWDOWN_FACTOR: float = 0.40
AIM_ASSIST_TRIGGER_SHAPES: Tuple[str, ...] = ("peace", "pinch")
AIM_ASSIST_PRE_ACTIVATION: bool = True
AIM_ASSIST_PRE_PINCH_THRESHOLD: float = 0.12
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
DPI_FIXED_MULTIPLIER: float = 1.00

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
# PINCA
# ---------------------------------------------------------------------

PINCH_DISTANCE_THRESHOLD: float = 0.075
"""Distancia maxima polegar→indicador pra classificar como pinca.

Pinca natural (dedos proximos mas nao encostando) funciona — nao
exige toque pra disparar.
"""

PINCH_THRESHOLD_FLOOR: float = 0.045
"""Piso minimo do threshold apos escala adaptativa.

Quando mao esta longe (hand_size < reference) o threshold cai
proporcionalmente. Sem o piso, mao a 0.10 fazia threshold virar 0.034
(impossivel de fechar).
"""

PINCH_ADAPTIVE_TO_HAND_SIZE: bool = True

PINCH_DUAL_DETECTION: bool = False
"""Deteccao dual (distancia + velocidade de aproximacao).

Desabilitada porque estava rejeitando pincas legitimas quando o
usuario relaxava os dedos pra soltar. Protecao contra falsos positivos
vem do debounce de 2 frames + threshold.
"""

PINCH_VELOCITY_THRESHOLD: float = 0.008

PINCH_MIN_HOLD_SECONDS: float = 0.015
"""Tempo minimo da pinca pra contar como click. 15ms permite cliques
rapidos e fluidos sem disparar em encontros acidentais."""

# ---------------------------------------------------------------------
# CLIQUE DIREITO — pinca polegar+medio
# ---------------------------------------------------------------------

PINCH_MIDDLE_THRESHOLD: float = 0.075
"""Distancia maxima polegar→medio pra classificar como pinca do clique
direito. Mesmo valor do pinch normal pra manter sensacao consistente."""

PINCH_MIDDLE_INDEX_GUARD: float = 0.090
"""Distancia MINIMA polegar→indicador pra permitir o clique direito.

Ao fechar polegar+medio o indicador tende a se aproximar junto
(mecanica natural da mao). Sem essa guarda, dispararia pinca normal
(CLICK) tambem. Forca o usuario a manter o indicador afastado do
polegar — pinca normal e clique direito ficam mutuamente exclusivos.
"""

# ---------------------------------------------------------------------
# POSTURA ANATOMICA — anti acoplamento de tendoes
# ---------------------------------------------------------------------
# Durante PINCH (polegar+indicador), o dedo medio cai naturalmente 30-50%
# do quanto o indicador caiu — efeito do flexor digitorum profundus (FDP)
# que controla os 4 dedos juntos. Isso fazia o classificador antigo
# disparar PINCH_MIDDLE (RIGHT_CLICK) inadvertidamente durante CLICK.
#
# Fix: alem das distancias par-a-par, validamos a POSTURA dos dedos via
# score de extensao (razao distancia direta MCP→TIP / soma de segmentos).
# Score ∈ [0,1]: ~1.0 = dedo reto, ~0.65 = curvado, ~0.40 = fechado.

PINCH_MIDDLE_INDEX_EXTENSION_MIN: float = 0.88
"""Score MINIMO de extensao do indicador pra validar PINCH_MIDDLE.

0.88 = indicador claramente apontando (apenas leve flexao natural).
Durante PINCH normal o indicador esta CURVADO (score 0.65-0.80) — nessa
faixa a porta de PINCH_MIDDLE fica fechada. So abre quando o usuario
estende deliberadamente o indicador.

Ajuste UP (0.92+) se ainda houver falsos positivos.
Ajuste DOWN (0.80-) se o gesto intencional for rejeitado.
"""

PINCH_MIDDLE_MIDDLE_EXTENSION_MAX: float = 0.92
"""Score MAXIMO de extensao do dedo medio pra validar PINCH_MIDDLE.

0.92 = medio nao pode estar totalmente reto. Pra PINCH_MIDDLE real o
medio precisa ter dobrado pra encontrar o polegar.

Ajuste UP (0.96) pra aceitar gestos mais sutis.
Ajuste DOWN (0.85) se houver falsos positivos com medio quase reto.
"""

# ---------------------------------------------------------------------
# DRAG
# ---------------------------------------------------------------------

DRAG_HOLD_SECONDS: float = 1.5
"""Tempo de pinch sustentado pra iniciar drag.

1.5s = aggressive. Press-to-click ja disparou em ~0ms; quem so quer
clicar solta antes de 1.5s. Risco maior se voce hesitar durante um
clique.
"""

# ---------------------------------------------------------------------
# CLIQUE E COOLDOWNS
# ---------------------------------------------------------------------

CLICK_COOLDOWN_SECONDS: float = 0.15
"""Tempo minimo entre dois cliques consecutivos.

0.15s permite sequencias rapidas (abrir menu, clicar opcao, fechar) sem
descartar cliques legitimos. Cobre ainda assim a janela de bounce do
press-to-click. Aumente pra 0.20s+ se notar duplo-clique acidental ao
abrir/fechar pinch rapido.
"""

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

SCREEN_MARGIN_PERCENTAGE: float = 0.10
"""Margem isotropica (fallback). Usado se as margens assimetricas
abaixo nao forem definidas.
"""

# Margens assimetricas — top/bottom < X. Anatomia + camera 16:9: alcance
# horizontal e' confortavel (braco oscila amplo); vertical cansa rapido
# (ombro contra gravidade). Margens menores no Y permitem atingir abas
# do navegador / taskbar com a mao em altura comfortavel, sem precisar
# levar ate o limite do FOV (onde MediaPipe ja erra). X mantem folga
# pra evitar saltos quando a mao sai pelo lado natural do gesto.

SCREEN_MARGIN_X: float = 0.08
"""Margem horizontal (esquerda/direita)."""

SCREEN_MARGIN_TOP: float = 0.04
"""Margem superior — pequena pra alcancar abas/menu/fechar com
inclinacao leve do braco. Menor que SCREEN_MARGIN_X de proposito.
"""

SCREEN_MARGIN_BOTTOM: float = 0.14
"""Margem inferior — MAIOR que a superior de proposito.

A camera fica no topo do monitor apontando pra baixo: a borda inferior
do frame corresponde a mao quase na mesa, regiao onde o MediaPipe
degrada (wrist sai do FOV primeiro). Com 0.08, o fundo da tela e'
atingido em y=0.86 do frame — antes da zona de degradacao — em vez
de exigir a mao quase na mesa.
"""

# ---------------------------------------------------------------------
# ALCANCE DA BORDA INFERIOR (taskbar)
# ---------------------------------------------------------------------
# Tres mecanismos complementares pra fechar a "zona morta" acima da
# taskbar. Causa raiz: (a) edge-taper da ancora enviesa o cursor pra
# cima quando landmarks inferiores saem do frame; (b) extrapolacao por
# velocidade zera na fase de homing (aproximacao lenta do alvo, Fitts).
# Cada mecanismo cobre um elo: mapeamento -> gap terminal -> homing.

CURSOR_Y_BOTTOM_BOOST_ENABLED: bool = True
"""Remapeamento nao-linear do eixo Y: abaixo do joelho (knee), o
restante do frame cobre o restante da tela com aceleracao — pequenos
movimentos de mao perto do fundo viram avancos maiores do cursor."""

CURSOR_Y_BOTTOM_BOOST_KNEE: float = 0.82
"""Fracao da TELA (0-1, apos margem) onde a curva de boost comeca.
Acima do joelho o mapeamento e' linear (precisao intacta no miolo).

0.82: boost restrito aos ~18% finais da tela. Com 0.68 o terco
inferior inteiro (onde ficam icones da area de trabalho) era
acelerado, deixando o cursor nervoso ao passar por cima dos apps."""

CURSOR_Y_BOTTOM_BOOST_POWER: float = 1.5
"""Expoente da curva ease-out no trecho boosted. 1.0 = linear (sem
efeito). 1.5 = curva mais suave que 1.9 — glide sobre os apps sem
saltos. Aumente pra chegar mais rapido ao fundo; reduza se perder
precisao / se o cursor 'escorregar' na regiao inferior."""

CURSOR_EDGE_SNAP_PX: int = 48
"""Magnetismo da borda inferior: movendo pra BAIXO a menos de N pixels
do fundo, o cursor snap pra borda. Alvos da taskbar ficam colados na
borda (Fitts: borda = alvo de largura infinita); o snap fecha o gap
terminal que o vies da ancora deixa. 0 desabilita."""

CURSOR_EDGE_CREEP_ENABLED: bool = True
"""Border creep: mao parada na banda inferior por mais que o delay =
cursor desliza suavemente ate a borda. Cobre a fase de homing (usuario
desacelera ao mirar -> velocidade ~0 -> extrapolacao nao ajuda)."""

CURSOR_EDGE_CREEP_BAND_PX: int = 110
"""Altura da banda inferior (em px de tela) onde o creep atua."""

CURSOR_EDGE_CREEP_DELAY_S: float = 0.15
"""Tempo parado dentro da banda antes do creep comecar. Evita disparo
em travessias rapidas pela regiao."""

CURSOR_EDGE_CREEP_RATE_PX_S: float = 380.0
"""Velocidade do deslize (px/s). ~350-400 le como 'assistencia suave',
nao como cursor fugindo do controle."""

# ---------------------------------------------------------------------
# FEEL DE MOUSE FISICO
# ---------------------------------------------------------------------
# Replicam dois fundamentos que fazem mouse fisico ser preciso:
#  1. Apertar o botao NUNCA desloca o cursor.
#  2. DPI baixo durante movimentos finos (texto, drag).

CLICK_FREEZE_SECONDS: float = 0.12
"""Quanto tempo o cursor fica TRAVADO no pixel atual logo apos um click.

Ao fechar a pinca, o midpoint 4+8 (ancora do cursor) inerentemente se
desloca alguns pixels — o polegar avanca em direcao ao indicador. Sem
freeze, o cursor "viaja" 2-10px durante o click, fazendo cliques em
alvos pequenos errarem.

0.12s cobre o intervalo entre a deteccao do raw shape PINCH e a
estabilizacao da pose. Aumente pra 0.15-0.20s se ainda notar deriva.
Diminua pra 0.08s se sentir cursor "preso" apos clicks consecutivos.
"""

DRAG_PRECISION_FACTOR: float = 0.55
"""Multiplicador do delta do cursor enquanto DRAG esta ativo.

0.55 = mao percorre ~1.8x a distancia pra cobrir o mesmo pixel range.
Replica o "ajustar DPI pra baixo" que profissionais fazem fisicamente
ao selecionar texto / arrastar com precisao. Sem isso, a tremedeira
natural da mao no ar (3-5 pixels) atrapalha selecao fina.

Ajuste UP (0.7-0.8) se o drag estiver lento demais.
Ajuste DOWN (0.4) pra precisao maxima em telas 4K+.
"""

# ---------------------------------------------------------------------
# PAINEL DE AJUSTES (Qt)
# ---------------------------------------------------------------------
# Painel nativo PySide6 acessivel por um handle discreto na borda da
# tela — visual clean, independente da janela de preview. Requer PySide6
# (ja usado pelo holograma). Sem ele, o painel OpenCV da tecla S segue
# como fallback automatico.

SETTINGS_PANEL_QT_ENABLED: bool = True
"""Liga o painel Qt + handle lateral. False mantem apenas o painel
OpenCV da tecla S."""

SETTINGS_PANEL_WIDTH: int = 320
"""Largura do painel deslizante em pixels."""

SETTINGS_PANEL_SIDE: str = "right"
"""Borda onde o handle aparece: 'right' ou 'left'."""

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
WINDOW_NAME: str = "AI Virtual Mouse — ESC to quit"

# ---------------------------------------------------------------------
# JANELA DE EXIBICAO
# ---------------------------------------------------------------------

DISPLAY_COMPACT_WIDTH: int = 480
DISPLAY_COMPACT_HEIGHT: int = 270
"""Tamanho da janela em modo compacto (sem painel de settings).

A camera continua capturando em CAMERA_WIDTH x CAMERA_HEIGHT (resolucao
cheia pra a IA); o frame e redimensionado antes de ser exibido.
Deteccao da mao nao perde precisao.
"""

DISPLAY_EXPANDED_WIDTH: int = 760
DISPLAY_EXPANDED_HEIGHT: int = 540
"""Tamanho quando o painel de settings esta aberto (tecla S)."""

DISPLAY_POSITION_BOTTOM_RIGHT: bool = True
"""Posiciona a janela no canto inferior direito ao iniciar (estilo
webcam de streamer). False deixa o SO decidir."""

DISPLAY_POSITION_MARGIN: int = 20
"""Distancia em pixels da borda da tela ao posicionar automaticamente."""

DISPLAY_ALWAYS_ON_TOP: bool = True
"""Mantem a janela da webcam SEMPRE visivel acima de outras janelas.

Sem isso, ao clicar em outra aplicacao a janela vai pra tras e voce
nao consegue ver sua mao pra controlar o cursor. Comportamento similar
a webcams de streamers no OBS Studio. Tecla T alterna em runtime.
"""

DISPLAY_TOPMOST_TOGGLE_KEY: str = "t"
"""Tecla pra alternar always-on-top em runtime."""

# ---------------------------------------------------------------------
# HOLOGRAMA (mao virtual desenhada na tela)
# ---------------------------------------------------------------------

HOLOGRAM_ENABLED: bool = False
"""Abre uma janela fullscreen transparente sobre o desktop e desenha
uma mao "holografica" seguindo o cursor. Tecla H liga em runtime.

Default False porque adiciona overhead visual. Em demo/onboarding
parece magica; em uso prolongado pode distrair.
"""

HOLOGRAM_BACKEND: str = "auto"
"""Backend de renderizacao do holograma.

- "auto"     : tenta GL primeiro; cai pra QPainter se driver falhar
- "gl"       : forca ModernGL (mesh 3D + GLSL shader)
- "qpainter" : forca pipeline 2D vetorial (compatibilidade/debug)

ModernGL renderiza a mao como mesh 3D real (palm ellipsoide + finger
capsules) com shader holografico (fresnel rim, depth fade, cyan
volumetric). Requer driver OpenGL 3.3+. Em ambientes sem GPU/driver
(RDP, VM sem aceleracao), usar "qpainter".
"""

HOLOGRAM_TOGGLE_KEY: str = "h"
"""Tecla pra alternar o holograma em runtime."""

HOLOGRAM_OPACITY: float = 0.70
"""Opacidade base. Camadas (palm/finger outline, particles, tips) usam
fracoes desse valor."""

HOLOGRAM_HAND_SIZE_PX: int = 180
"""Tamanho do bounding box da mao em pixels."""

HOLOGRAM_FPS: int = 30
"""Taxa de redesenho. 30 Hz e' suave + economico em CPU."""

HOLOGRAM_COLOR_BONE: str = "#00d4ff"
"""Cor primaria do holograma — azul neon ciano (linhas/contornos)."""

HOLOGRAM_COLOR_POINT: str = "#e0f7ff"
"""Cor dos pontos brilhantes (tips, particulas)."""

HOLOGRAM_PARTICLES_ENABLED: bool = False
"""Nuvem densa de particles dentro da silhueta. False = visual clean."""

HOLOGRAM_PARTICLE_COUNT: int = 180
"""Numero de particles na nuvem. >300 pode impactar FPS."""

HOLOGRAM_VIEW_DORSAL: bool = False
"""Vista anatomica do holograma.

True  = dorsal (costas da mao na tela) — espelha X dos landmarks
False = palm view — segue webcam mirroreada
"""

HOLOGRAM_TRANSPARENT_COLOR: str = "#010203"
"""Cor "magica" pintada como fundo do canvas que vira transparente no
compositor do Windows. Tem que ser cor que nao apareca em mais nada."""

# ---------------------------------------------------------------------
# PERFORMANCE TELEMETRY (instrumentacao per estagio do tick loop)
# ---------------------------------------------------------------------

PERF_TELEMETRY_ENABLED: bool = True
"""Liga timing por estagio (camera/inference/gesture/events/hologram/
preview/waitkey). Reporta p50/p99 a cada N ticks via logger.info.

Custo: ~100ns por entry/exit (desprezivel).
"""

PERF_TELEMETRY_WINDOW: int = 120
"""Tamanho do rolling window de samples por estagio (= 2s a 60 FPS)."""

PERF_TELEMETRY_REPORT_EVERY: int = 120
"""Logar report a cada N ticks (= 2s a 60 FPS)."""

# ---------------------------------------------------------------------
# MODO APRESENTACAO
# ---------------------------------------------------------------------
# Mao aberta cruzando do meio do frame pra um lado dispara
# next/prev slide. Toggle via tecla Z ou painel S — quando ativo, o
# detector de mouse fica suspenso.

PRESENTATION_TOGGLE_KEY: str = "z"

PRESENTATION_NEXT_KEY: str = "right"
"""Tecla disparada pra AVANCAR slide. Default 'right' (compativel com
PowerPoint, Google Slides, Keynote, leitores PDF)."""

PRESENTATION_PREV_KEY: str = "left"
"""Tecla disparada pra VOLTAR slide."""

PRESENTATION_DEAD_ZONE: float = 0.75
"""Largura nominal da zona neutra. A largura EFETIVA depende da formula
no PresentationController.__init__ — com a atual (divisao por 3), 0.75
resulta em ~50% central neutro (x de 0.25 a 0.75). Max 0.80."""

PRESENTATION_EXTENSION_THRESHOLD: float = 0.80
"""Score minimo de extensao dos 4 dedos longos pra reconhecer MAO ABERTA.
Reduza (0.70) se a mao nao for detectada como aberta com facilidade."""

PRESENTATION_COOLDOWN_S: float = 0.5
"""Tempo minimo entre disparos consecutivos."""

# ---------------------------------------------------------------------
# SEGURANCA
# ---------------------------------------------------------------------

PYAUTOGUI_FAILSAFE: bool = False
PYAUTOGUI_PAUSE: float = 0.0

# ---------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------

LOG_LEVEL: str = "INFO"
