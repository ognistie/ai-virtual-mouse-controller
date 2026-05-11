# AI Virtual Mouse Controller

Controle do cursor do sistema operacional usando **gestos da mão** capturados pela webcam, com qualidade de input comparável a periféricos profissionais.

> Versão atual: **v6.8 (Modern UI)** — painel de configurações em runtime, recommended baseline embutido, ajuste ao vivo sem reiniciar.

---

## Sumário

- [O que é](#o-que-é)
- [Demo](#demo)
- [Como funciona — visão geral](#como-funciona--visão-geral)
- [Arquitetura](#arquitetura)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Decisões técnicas](#decisões-técnicas)
- [Stack](#stack)
- [Instalação](#instalação)
- [Uso](#uso)
- [Configuração](#configuração)
- [Histórico de evolução](#histórico-de-evolução)
- [Limitações conhecidas](#limitações-conhecidas)
- [Roadmap](#roadmap)

---

## O que é

Este projeto transforma uma webcam comum em uma **interface gestual de cursor**. O usuário move a mão diante da câmera e o cursor do sistema responde em tempo real — abrir e fechar a pinça vira clique, manter pinça por 2 segundos vira arrasto, fazer "✌️" vira duplo-clique, e assim por diante.

A engenharia foi pensada para resolver os três problemas que matam projetos similares:

1. **Tremor de cursor** quando a mão fica parada (ruído natural da detecção)
2. **Imprecisão ao mirar em alvos pequenos** (botões "X", links)
3. **Cliques perdidos** quando o gesto é rápido demais para a histerese

A solução combina filtragem adaptativa (OneEuroFilter), curvas balísticas estilo cursor de jogo (aim assist + sticky targeting), e um pipeline de detecção com tolerância configurável.

---

## Demo

A interface v6.8 traz um **painel lateral ativável com tecla `S`** para ajustar todos os parâmetros sem reiniciar o programa:

```
┌──────────────────────────────────────────────────────────────────┐
│ FPS 58   SHAPE PINCH   DPI 1.05x          PRECISION   FROZEN    │
├──────────────────────────────────┬───────────────────────────────┤
│                                  │ SETTINGS              S close │
│                                  │ Sensitivity              0.92 │
│                                  │ ━━━━━━━━●━━━━━━━━━━━           │
│                                  │ Aim assist               0.55 │
│                                  │ ━━━━━━━━━━━━●━━━━━━━           │
│      [video da webcam]           │ Smoothness               0.80 │
│                                  │ ━━━━━━━━━━━━━━━━●━━━           │
│                                  │ Pinch sensitivity       0.075 │
│                                  │ ━━━━━━●━━━━━━━━━━━━━           │
│                                  │ Sticky strength          0.75 │
│                                  │ ━━━━━━●━━━━━━━━━━━━━           │
│                                  │ Anchor freeze            60ms │
│                                  │ ━━━━●━━━━━━━━━━━━━━━           │
│                                  │ PROFILE                       │
│                                  │ [smooth]  precise             │
│                                  │  snappy   stable              │
│                                  │ Aim assist: ON                │
│                                  │ Sticky: ON                    │
│                                  │ Reset profile                 │
│                                  │ Apply recommended             │
├──────────────────────────────────┴───────────────────────────────┤
│ ESC quit · arrows nav · +/- adjust · 1-4 profile · D doc · S close│
└──────────────────────────────────────────────────────────────────┘
```

---

## Como funciona — visão geral

### Pipeline de execução (60 FPS alvo)

```
   webcam              MediaPipe          GestureDetector       OneEuro          OS cursor
  (thread)              Hands              (state machine)      filter           (PyAutoGUI)
     │                    │                       │                │                 │
     │                    │                       │                │                 │
  frame BGR ──→ frame RGB ──→ 21 landmarks ──→ MOVE/CLICK/etc ──→ smooth ──→ pyautogui.moveTo
     │           (mirror)         3D                 events         (x, y)              │
     │                                                                                  │
     └─────────────────────────  desenhado de volta no preview da janela ←─────────────┘
                                              + UI overlay (sliders, badges)
```

Cada frame da webcam roda esse pipeline inteiro. A meta de 60 FPS exige que cada estágio seja não-bloqueante — daí a thread separada na captura e a estrutura compacta dos componentes.

### Os 5 gestos canônicos

| Gesto | O que faz |
|---|---|
| 🖐️ Mão aberta | Move o cursor |
| 🤏 Pinça (polegar + indicador juntos) | Clique simples |
| 🤏 Pinça mantida ≥ 2 segundos | Inicia arrasto (drag) |
| ✌️ Dois dedos (peace) | Duplo-clique (cursor congela durante o gesto) |
| ✊ Punho fechado | Pausa cursor (anti-acidente) |
| Mão fora do frame | Pausa total |

---

## Arquitetura

O projeto segue uma separação **core / services** clássica:

- **`core/`** — componentes puros, sem orquestração. Cada arquivo tem uma única responsabilidade e pode ser usado isoladamente. Não conhecem `config.py`.
- **`services/`** — camada de orquestração que junta os componentes do core, lê configuração e roda o loop principal.
- **`config.py`** — constantes de configuração inicial (camera, MediaPipe, gestos). Lido apenas pelo service, não pelo core.
- **`main.py`** — entry point que apenas configura logging e chama `VirtualMouseService.from_config()`.

### Fluxo de dados

```
        config.py
            │
            ▼
   VirtualMouseService.from_config()        ← cria todas as peças
            │
            ├──→ ThreadedCamera        (lê webcam em thread daemon)
            ├──→ HandTracker           (wrapper sobre MediaPipe Hands)
            ├──→ GestureDetector       (state machine de gestos)
            ├──→ CursorController      (wrapper sobre PyAutoGUI)
            ├──→ OneEuroSmoother2D     (filtragem temporal)
            ├──→ RuntimeSettings       (settings ao vivo, profiles, recommended)
            └──→ UIOverlay             (painel desenhado sobre o frame)
            │
            ▼
       loop principal
            │
            ▼
       service.run()
```

### Princípios de design

1. **Componentes puros no `core/`** — testáveis isoladamente, sem dependência de configuração global.
2. **Service como bridge** — única classe que conhece todos os componentes. Centraliza orquestração.
3. **Settings em duas camadas** — `config.py` traz constantes que **não mudam em runtime** (resolução de câmera, modelo do MediaPipe). `RuntimeSettings` traz os parâmetros **ajustáveis ao vivo** pela UI (DPI, smoothness, aim assist).
4. **Defesa contra evolução** — o service usa `getattr()` defensivo para features opcionais (ex: `anchor_frozen`), permitindo trocar o `GestureDetector` por uma versão mais antiga sem quebrar.

---

## Estrutura do projeto

```
ai-virtual-mouse-controller/
├── main.py                          # entry point: logging + service.run()
├── config.py                        # constantes (camera, mediapipe, gestos)
├── core/
│   ├── camera.py                    # ThreadedCamera: captura assíncrona de webcam
│   ├── hand_tracker.py              # HandTracker: wrapper sobre MediaPipe Hands
│   ├── gesture_detector.py          # GestureDetector: state machine dos gestos
│   ├── cursor_controller.py         # CursorController: wrapper sobre PyAutoGUI
│   ├── smoothing.py                 # EMASmoother + OneEuroFilter (factory)
│   ├── ui_overlay.py                # UIOverlay: painel + sliders desenhados em OpenCV
│   ├── runtime_settings.py          # RuntimeSettings: profiles + sliders ↔ valores reais
│   └── utils.py                     # FPSCounter, clamp, map_range, etc.
├── services/
│   └── virtual_mouse_service.py     # VirtualMouseService: orquestração + loop
└── tests/
    ├── test_smoothing.py
    └── test_gesture_detector.py
```

### Responsabilidades — quem faz o quê

| Módulo | Faz | Não faz |
|---|---|---|
| `camera.py` | Captura BGR em thread, descarta frames atrasados | Não conhece MediaPipe nem cursor |
| `hand_tracker.py` | Recebe frame RGB, retorna landmarks tipados | Não desenha (só expõe `process_with_raw` para quem quer) |
| `gesture_detector.py` | Histerese, debounce, pipeline de precisão, decide quando emitir CLICK/MOVE | Não toca em PyAutoGUI |
| `cursor_controller.py` | Mapeia (x, y) normalizado → pixel do monitor, chama PyAutoGUI | Não filtra, não detecta |
| `smoothing.py` | Filtra ruído temporal (EMA ou OneEuro) | Não conhece gestos |
| `ui_overlay.py` | Desenha topbar + painel + sliders em cima do frame | Não sabe rodar o sistema |
| `runtime_settings.py` | Estado dos sliders, profiles, recommended baseline | Não sabe desenhar nem detectar |

---

## Decisões técnicas

### Por que threading na captura de webcam

`cv2.VideoCapture.read()` é uma chamada **bloqueante**. Se o pipeline de detecção (MediaPipe + filtros + render) leva 30ms, e fazer `read()` no mesmo thread leva mais 15ms, o FPS efetivo cai pela metade.

A solução é uma **thread daemon** que loop-eternamente lê frames e mantém apenas o **mais recente** em memória (descarta atrasados). O thread principal pega "o que tiver" sempre que pode. Resultado prático: ~58 FPS estáveis vs ~25 FPS sem thread.

### Por que MediaPipe Hands (e não OpenPose, YOLO, etc.)

- **Latência baixa** — modelo otimizado para mobile, roda em CPU sem perder FPS
- **Nativamente normalizado** — retorna coordenadas em [0, 1], independente da resolução do frame
- **21 landmarks 3D** — suficiente para detectar pinça, peace, fist com geometria simples
- **Sem GPU obrigatória** — funciona em qualquer notebook moderno

A alternativa seria treinar um modelo customizado, mas para o conjunto de 5 gestos suportados o MediaPipe é overkill já pronto.

### Por que landmark 9 é a âncora do cursor (e não a ponta do indicador)

A ponta do indicador (landmark 8) é o mais óbvio mas tem dois problemas:

1. **Move muito durante a pinça** — fechar polegar+indicador faz o landmark 8 saltar pra encontrar o polegar, jogando o cursor pra longe do alvo no momento do clique.
2. **É naturalmente trêmulo** — pontas de dedos têm mais ruído articular que a base.

O **landmark 9 (base do dedo médio)** fica no centro de gravidade da palma. Mesmo durante uma pinça vigorosa, ele se move pouco. Resultado: o cursor permanece estável durante o clique.

### Por que OneEuroFilter (e não Kalman, EMA pura, ou média móvel)

Tracking de cursor tem um trade-off cruel: filtragem alta = cursor parado fica imóvel (bom!), mas movimento rápido fica laggy (ruim). EMA com α fixo escolhe um lado.

O **OneEuroFilter (Casiez et al., 2012)** é um low-pass com **cutoff frequency adaptativa**: quanto maior a velocidade, mais o filtro deixa passar. Em repouso filtra agressivamente; em movimento rápido praticamente não interfere. É o padrão da indústria para tracking gestual.

Implementação em `core/smoothing.py` com dois `_OneEuroAxis` independentes (X e Y), parâmetros expostos para tuning ao vivo via UI.

### Por que aim assist e sticky targeting

Inspirado em controles de console (FPS games):

- **Aim assist** — quando o usuário faz pinça ou peace, o cursor desacelera. Isso compensa o tremor natural ao mirar em alvos pequenos.
- **Sticky targeting** — quando a velocidade do cursor cai bruscamente (sinal de que o usuário está "se aproximando" de um alvo), aplica fricção extra. O cursor parece "agarrar" o alvo.

A composição dos dois usa `min()` em vez de produto (multiplicação) para evitar que o cursor fique travado a 16% da velocidade real perto de botões — um problema descoberto durante testes reais.

### Por que histerese de 2 frames + position hold de 3 frames

Sem histerese, qualquer ruído na detecção do MediaPipe vira oscilação rápida entre estados (pinça/aberto/pinça/aberto), causando cliques fantasma. **Histerese (debounce) de 2 frames** exige confirmação antes de mudar de estado. **Position hold de 3 frames** mantém o cursor na última posição quando a mão sai do frame brevemente — evita o cursor "saltar" para um canto quando o tracking pisca.

### Por que duas camadas de configuração (`config.py` + `RuntimeSettings`)

- **`config.py`** — coisas que dependem do hardware/ambiente e não fazem sentido mudar em runtime (resolução da câmera, modelo do MediaPipe, qual landmark é a âncora).
- **`RuntimeSettings`** — coisas que o usuário ajusta para preferência pessoal (DPI, suavidade, força do aim assist). Estas mudam ao vivo via UI sem reiniciar.

Essa separação permite que o painel de settings seja **realmente útil** (mexe e vê o efeito imediato) sem inflar o `config.py` com lógica de UI.

---

## Stack

| Camada | Tecnologia | Versão alvo |
|---|---|---|
| Linguagem | Python | 3.11+ |
| Visão computacional | OpenCV (`opencv-python`) | 4.x |
| Detecção de mão | MediaPipe (`mediapipe`) | `>=0.10.9, <0.11` |
| Controle de cursor | PyAutoGUI | latest |
| Computação numérica | NumPy | latest |
| OS alvo | Windows 10/11 | (DSHOW backend) |

> **Por que pinar mediapipe?** Versões recentes (0.11+) removeram `mp.solutions.hands` em favor de uma nova API `tasks`. Pinar para a faixa 0.10.x mantém o código atual funcional.

---

## Instalação

### Pré-requisitos

- **Python 3.11 ou 3.12** (não use 3.14 — MediaPipe não tem wheels)
- **Windows 10/11** (testado; outros sistemas devem funcionar mas a câmera usa DSHOW)
- **Webcam** funcional (qualquer USB ou integrada)

### Setup

```powershell
# Clone
git clone https://github.com/ognistie/ai-virtual-mouse-controller.git)
cd ai-virtual-mouse-controller

# Virtual env (use Python 3.11 explicitamente)
python -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# Dependências
pip install -r requirements.txt
```

### `requirements.txt` recomendado

```txt
opencv-python>=4.8
mediapipe>=0.10.9,<0.11
pyautogui
numpy
```

### Verificação rápida

```powershell
python main.py
```

Uma janela "AI Virtual Mouse" deve abrir mostrando sua webcam. Se aparecer "Hand not detected", mostre a mão. Pressione **`ESC`** para sair.

### Troubleshooting

| Sintoma | Causa provável | Solução |
|---|---|---|
| `metadata-generation-failed` no pip install | Python 3.14 (sem wheels) | Use Python 3.11 |
| `AttributeError: module mediapipe has no attribute 'solutions'` | mediapipe 0.11+ instalado | `pip install mediapipe==0.10.21` |
| `VideoCapture::open VIDEOIO(DSHOW): backend ... can't be used` | Câmera ocupada | Feche Teams/Zoom/OBS, verifique permissões de privacidade do Windows |
| `.venv` quebrado após copiar de outra máquina | venvs têm path absoluto | Apague `.venv` e recrie |

---

## Uso

### Gestos básicos

Posicione a mão a ~50cm da câmera, com palma virada para a câmera.

| Quero | Faço |
|---|---|
| Mover o cursor | Mão aberta, palma para câmera |
| Clicar | Toque polegar + indicador (pinça) |
| Duplo-clique | Mostre dois dedos (✌️) por meio segundo |
| Arrastar algo | Pinça e segure por 2 segundos (cursor "pega"); mova a mão; abra a mão para soltar |
| Pausar cursor | Feche o punho |
| Pausar tudo | Tire a mão do frame |

### Atalhos do painel (v6.8)

| Tecla | Ação |
|---|---|
| `S` | Abre/fecha painel de configurações |
| `↑` `↓` | Navega entre sliders |
| `←` `→` ou `+` `-` | Ajusta o slider focado (passos de 5%) |
| `1` `2` `3` `4` | Troca para profile smooth/precise/snappy/stable |
| `R` | Reset para defaults do profile atual |
| `A` | Liga/desliga aim assist |
| `K` | Liga/desliga sticky targeting |
| `D` | Mostra/esconde tabela "Recommended vs Current" |
| `ESC` | Sai do programa |

### Mouse físico (no painel)

Você pode também **clicar e arrastar nos sliders** com o mouse físico, e clicar nos botões de profile/toggle/reset. Isso é útil enquanto a mão está sendo detectada (a câmera não trava o mouse físico).

---

## Configuração

### Profiles (4 presets prontos)

Cada profile ajusta múltiplos parâmetros simultaneamente:

| Profile | Ideal para | Características |
|---|---|---|
| **smooth** | Uso geral | Equilíbrio entre suavidade e responsividade |
| **precise** | Alvos pequenos (botão "X", links) | DPI menor, aim assist mais forte, mais filtragem |
| **snappy** | Movimento rápido / arrastos longos | DPI maior, aim assist sutil, menos filtragem |
| **stable** | Mãos trêmulas | Filtragem máxima (com pequeno lag) |

### Sliders (6 ajustes ao vivo)

| Slider | Controla | Valor real |
|---|---|---|
| Sensitivity | Multiplicador de DPI | 0.50× a 1.50× |
| Aim assist | Quanto desacelera ao mirar | 0.00 (off) a 0.80 (forte) |
| Smoothness | Filtragem do OneEuro | raw (1ms lag) ↔ smooth (50ms lag) |
| Pinch sensitivity | Distância máxima polegar-indicador para pinça | 0.050 (estrito) a 0.110 (permissivo) |
| Sticky strength | Fricção quando cursor desacelera | 0.00 (off) a 0.50 (forte) |
| Anchor freeze | Tempo que cursor congela ao iniciar pinça | 0ms (off) a 200ms |

### Recommended baseline

A v6.8 mantém em memória os valores que **comprovadamente funcionam bem** (extraídos da v6.5 estabilizada):

```
Sensitivity      0.85
Aim assist       0.40
Smoothness       1.20 (cutoff)
Pinch            0.075
Sticky           0.75
Freeze           50ms
```

Se você se perder ajustando, o botão **"Apply recommended"** restaura tudo de volta. A tecla **`D`** mostra a tabela comparativa em tempo real (valores diferentes do recommended ficam destacados em amarelo).

### Editando o `config.py`

Para mudanças permanentes (não-runtime), edite as constantes no topo do arquivo:

```python
CAMERA_INDEX = 0           # 0 = webcam padrão; 1+ se tiver várias
CAMERA_WIDTH = 960         # resolução de captura
CAMERA_HEIGHT = 540
CAMERA_FPS_TARGET = 60     # FPS alvo (hardware decide o real)

MAX_NUM_HANDS = 1          # mantém em 1; multi-mão não é suportado
MODEL_COMPLEXITY = 1       # 0 = rápido/menos preciso; 1 = preciso/mais lento

CURSOR_ANCHOR_LANDMARK = 9 # landmark usado como ponto do cursor
SCREEN_MARGIN_PERCENTAGE = 0.20  # zona "morta" nas bordas da câmera
```

---

## Histórico de evolução

O projeto passou por 8 versões principais, cada uma resolvendo um problema concreto observado no uso real:

| Versão | Foco | O que ficou |
|---|---|---|
| v1–v3 | MVP funcional | Pipeline base camera→tracker→detector→cursor |
| v4 | Suavização | OneEuroFilter substituiu EMA pura |
| v5 | Profissionalização | DPI adaptativo, histerese, position hold |
| **v6.0** | Reescrita do detector | State machine com debounce, gestos canônicos definidos |
| **v6.1** | Fix de duplo-clique | `double_click()` com interval explícito (Chrome/Explorer não reconheciam) |
| **v6.2** | Smooth & responsive | Curva balística, `min_tracking_confidence` ajustada |
| **v6.3** | Precision & aim | Aim assist auto-ativado em pinça/peace |
| **v6.4** | Smooth & sticky | Sticky targeting + smoothstep entre zonas de velocidade |
| **v6.5** | Easier pinch | Threshold +36%, dual detection desabilitado, cliques fluidos |
| **v6.6** | Polished | Anchor freeze (Apple Vision Pro style), median filter, composição limitada |
| **v6.7** | Reliable click | Pinch debounce separado, tolerance frames, aim assist mais sutil |
| **v6.8** | Modern UI | **Painel runtime, profiles, recommended baseline, ajuste ao vivo** |

A v6.5 é a baseline conhecida-boa que serve como **"recommended"** dentro do painel da v6.8.

---

## Limitações conhecidas

- **Windows-only na prática** — usa `cv2.CAP_DSHOW` como primeira tentativa de backend. Em macOS/Linux funciona via fallback, mas pode ter latência maior.
- **Uma mão apenas** — `MAX_NUM_HANDS = 1` por design. Multi-mão exigiria desambiguação de qual mão controla o cursor.
- **Sem suporte a multi-monitor inteligente** — o cursor mapeia para o monitor primário do PyAutoGUI. Em setups multi-monitor, considere alterar `screen_margin_percentage` para conseguir alcançar as bordas.
- **Iluminação importa** — em ambientes muito escuros, o MediaPipe perde tracking. O confidence gate ajuda mas não substitui boa iluminação.
- **Anchor freeze só no v6.6+** — se você usar o `gesture_detector.py` da v6.5, o slider "Anchor freeze" do painel funciona visualmente mas não tem efeito real (a UI não quebra graças ao `getattr()` defensivo no service).
- **Sem persistência de configuração** — ajustes feitos na UI não são salvos entre sessões. Para mudanças permanentes, edite `config.py` ou `runtime_settings.py`.

---

## Roadmap

Possíveis próximos passos, em ordem de impacto estimado:

### Curto prazo
- **Persistência de settings** — salvar últimas configurações em `~/.avmc/settings.json`
- **Calibração automática** — assistente inicial que pede pra mão fazer movimentos típicos e ajusta DPI/threshold automaticamente
- **Indicador visual de gesto** — overlay maior mostrando qual gesto está sendo detectado (útil para debug e onboarding)

### Médio prazo
- **Suporte a clique direito** — gesto de três dedos ou pinça com dedo médio
- **Scroll** — gesto de "rolar" com indicador em movimento circular ou vertical
- **Multi-monitor inteligente** — detecção automática de qual monitor está olhando

### Longo prazo
- **Modo apresentação** — clicker virtual para slides, sem precisar mover o cursor
- **Atalhos customizáveis** — usuário define gestos próprios mapeados para combinações de teclas
- **Modelo treinado custom** — substituir MediaPipe por modelo próprio menor/mais rápido para o conjunto restrito de gestos suportados



## Créditos

- **MediaPipe Hands** (Google) — detecção de mão e landmarks
- **OneEuroFilter** — Casiez, Roussel & Vogel, 2012 — filtragem de coordenadas
- **OpenCV** — captura e renderização
- **PyAutoGUI** — interface com o cursor do sistema operacional

Inspiração de design para aim assist e sticky targeting: controles de mira em FPS de console e o sistema de "anchor freeze" do Apple Vision Pro.
