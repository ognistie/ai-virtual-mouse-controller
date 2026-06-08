# AI Virtual Mouse Controller

[![CI](https://github.com/ognistie/ai-virtual-mouse-controller/actions/workflows/ci.yml/badge.svg)](https://github.com/ognistie/ai-virtual-mouse-controller/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://conventionalcommits.org)

> Controle o cursor do sistema operacional com gestos da mão, usando apenas uma webcam.

Sistema gestual de controle de cursor com qualidade de input comparável a periféricos físicos. Sem hardware extra, sem GPU, sem treinar modelo.

🔗 **[Documentação completa →](https://ognistie.github.io/ai-virtual-mouse-controller/)**

---

## Features

| Recurso | Descrição |
|---|---|
| 🖐️ **6 gestos canônicos** | Move, click, right-click, double-click, drag, pause |
| ⚡ **Press-to-click** | Clique no momento exato em que os dedos se tocam |
| 🎯 **Aim assist + sticky** | Desaceleração inteligente em alvos pequenos |
| 🪟 **Janela compacta** | 480×270 always-on-top, estilo OBS streamer |
| ⚙️ **Painel runtime** | 4 profiles + 6 sliders sem reiniciar |
| 📊 **Performance** | ~58 FPS estáveis em CPU, sem GPU |
| 👁️ **Holograma opcional** | Mão holográfica azul-ciano sobre o desktop (tecla H) |
| 📈 **Perf telemetry** | Instrumentação p50/p99 por estágio do tick loop |

## Quick Start

**Requisitos:** Python 3.11 ou 3.12, webcam, Windows 10/11.

```powershell
# Clone
git clone https://github.com/ognistie/ai-virtual-mouse-controller.git
cd ai-virtual-mouse-controller

# Ambiente virtual
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# Dependências
pip install -r requirements.txt

# Execute
python main.py
```

Janela 480×270 aparece no canto inferior direito. Posicione a mão a ~50cm da câmera, palma para frente. `ESC` para sair.

## Gestos

| Gesto | Ação |
|---|---|
| 🖐️ Mão aberta | Move o cursor |
| 🤏 Pinça polegar + indicador | Clique esquerdo (no toque) |
| 🤞 Pinça polegar + médio (indicador estendido) | Clique direito (no toque) |
| ✌️ Peace (~500ms) | Duplo clique |
| 🤏 Pinça mantida 1.5s | Inicia drag (mão aberta solta) |
| ✊ Punho fechado | Pausa o cursor |

## Atalhos

| Tecla | Função |
|---|---|
| `S` | Abre/fecha painel de configurações |
| `T` | Toggle always-on-top |
| `H` | Toggle holograma da mão (overlay PySide6) |
| `1-4` | Profile: smooth / precise / snappy / stable |
| `D` | Mostra tabela Recommended vs Current |
| `R` | Reset para defaults do profile |
| `A` / `K` | Toggle aim assist / sticky targeting |
| `ESC` | Sai |

## Holograma da mão (opcional)

Renderização em tempo real de uma mão holográfica azul-ciano sobreposta ao desktop. Segue os movimentos da mão real captados pela webcam.

**Características:**
- Click-through nativo (não bloqueia interação com o desktop)
- OneEuroFilter por landmark (suave parado, responsivo em movimento)
- Click bursts visuais no ponto exato do pinch
- Cursor marker pulsante na posição do clique
- Pseudo-3D com gradient direcional + wireframe interno
- ~0.4ms/frame (negligenciável)

**Pra ligar:** aperta `H` em runtime. Default: desligado.

**Requer:** `PySide6` (opcional — projeto roda normalmente sem ele, só o holograma fica indisponível).

```bash
pip install PySide6
```

## Arquitetura

Separação clássica **core / services**:

```
ai-virtual-mouse-controller/
├── main.py                          # Entry point
├── config.py                        # Constantes (camera, MediaPipe, gestos, perf, hologram)
├── core/
│   ├── camera.py                    # ThreadedCamera (captura assíncrona)
│   ├── hand_tracker.py              # Wrapper MediaPipe Hands
│   ├── gesture_detector.py          # State machine de gestos
│   ├── cursor_controller.py         # Wrapper PyAutoGUI (+ *_at methods)
│   ├── smoothing.py                 # EMA + OneEuroFilter
│   ├── ui_overlay.py                # Painel de settings em OpenCV
│   ├── runtime_settings.py          # Profiles + sliders
│   ├── utils.py                     # FPS, clamp, map_range
│   ├── perf_telemetry.py            # TickProfiler — instrumentation
│   ├── hand_renderer.py             # Função pura: 21 landmarks → primitivas 2D
│   ├── click_burst.py               # Animação de bursts no clique
│   └── hologram_overlay.py          # PySide6 widget (holograma azul-ciano)
├── services/
│   └── virtual_mouse_service.py     # Orquestração + main loop
├── tests/                           # 116 testes (pytest)
├── scripts/
│   └── test_hologram_visual.py      # Validação standalone do overlay
└── docs/                            # Site (GitHub Pages)
```

### Pipeline (60 FPS alvo)

```
webcam ──→ MediaPipe Hands ──→ GestureDetector ──→ OneEuroFilter ──→ PyAutoGUI
(thread)   (21 landmarks 3D)    (state machine)     (smoothing)       (cursor)
                                       │
                                       ↓
                              HologramOverlay (opcional, PySide6)
                                       │
                                       ↓
                              Pose-aware rendering 2D pseudo-3D
```

### Decisões técnicas chave

- **Landmark 9 (base do dedo médio)** como âncora do cursor — evita pulo no clique
- **OneEuroFilter** (Casiez et al., 2012) — filtra agressivo em repouso, libera em movimento
- **Threading da câmera** com buffer mínimo — descarta frames atrasados
- **Press-to-click via edge detection** no raw shape — feedback imediato
- **Holograma PySide6** com `WindowTransparentForInput` — click-through nativo, sem ctypes hack
- **Pseudo-3D via QLinearGradient** direcional — volume sem OpenGL
- **Catmull-Rom curves** nos polígonos — contorno orgânico anatômico
- **Per-joint width factors** — knuckle bulge anatômico nos dedos
- **Perf telemetry opt-in** — p50/p99 por estágio sem overhead quando off

## Configuração

### Profiles

| Profile | Ideal para |
|---|---|
| `smooth` | Uso geral (padrão) |
| `precise` | Alvos pequenos, links e botões "X" |
| `snappy` | Movimento amplo, drags longos |
| `stable` | Tremor natural ou setups instáveis |

### Sliders (runtime via tecla `S`)

| Slider | Controla |
|---|---|
| Sensitivity | Multiplicador DPI (0.50× – 1.50×) |
| Aim assist | Desaceleração ao mirar (0 – 0.80) |
| Smoothness | Filtragem OneEuro (raw ↔ smooth) |
| Pinch | Threshold de detecção (0.050 – 0.110) |
| Sticky | Fricção em desaceleração (0 – 0.50) |
| Anchor freeze | Congelamento ao iniciar pinça (0 – 200ms) |

### Constantes principais (config.py)

```python
# Câmera
CAMERA_INDEX = 0                          # 0 = webcam padrão
CAMERA_WIDTH = 960
CAMERA_HEIGHT = 540
MODEL_COMPLEXITY = 0                      # 0=lite (rápido), 1=full

# Cursor
CURSOR_ANCHOR_LANDMARK = 9                # landmark âncora
SCREEN_MARGIN_PERCENTAGE = 0.18           # zona morta nas bordas
POSITION_HOLD_FRAMES = 2                  # frames de hold antes de mover

# Drag/clique
DRAG_HOLD_SECONDS = 1.5                   # tempo de pinch para drag
PINCH_DISTANCE_THRESHOLD = 0.075          # pinch threshold (CLICK)
PINCH_MIDDLE_INDEX_GUARD = 0.090          # guard p/ right click

# Holograma (opcional)
HOLOGRAM_ENABLED = False                  # liga no startup (False = só via H)
HOLOGRAM_VIEW_DORSAL = False              # palm view (alinhado com webcam)
HOLOGRAM_PARTICLES_ENABLED = False        # nuvem de particles (default off)
HOLOGRAM_HAND_SIZE_PX = 180

# Performance telemetry
PERF_TELEMETRY_ENABLED = True             # log p50/p99 a cada N ticks
PERF_TELEMETRY_REPORT_EVERY = 120
```

## Stack

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.11+ |
| Visão computacional | OpenCV `>=4.10` |
| Detecção de mão | MediaPipe `>=0.10.18, <0.11` |
| Controle de cursor | PyAutoGUI |
| Computação | NumPy |
| Holograma (opcional) | PySide6 `>=6.6` |

> **Nota:** MediaPipe 0.11+ removeu `mp.solutions.hands`. Mantenha pinned em `<0.11`.

## Tests

```bash
pytest tests/ -v
```

**116 testes** cobrindo:
- Hand renderer (geometria pura)
- Click bursts (lifecycle)
- Hologram overlay (smoke + state)
- OneEuro smoothing
- Cursor controller (click_at methods)
- Gesture detector
- Perf telemetry

## Troubleshooting

| Sintoma | Solução |
|---|---|
| `mediapipe` falha no `pip install` | Use Python 3.11/3.12 (não 3.13+) |
| Webcam não abre | Feche Teams/Zoom/OBS, verifique permissões de privacidade |
| Cursor treme parado | Use profile `stable` ou aumente Smoothness |
| Cliques falham | Aumente Pinch sensitivity, verifique iluminação |
| FPS < 30 | `MODEL_COMPLEXITY = 0` já é default. Confira `inference` no log PERF |
| Holograma não aparece | `pip install PySide6` + aperta `H` no runtime |
| Right click não dispara | Index dropping junto? Baixe `PINCH_MIDDLE_INDEX_GUARD` em config |
| Drag dispara sem querer | Suba `DRAG_HOLD_SECONDS` em config (1.5 → 2.0 ou 2.5) |

## Limitações conhecidas

- Testado primariamente em Windows (`cv2.CAP_DSHOW` como backend). macOS/Linux funcionam via fallback
- Detecção de uma mão por frame (`MAX_NUM_HANDS = 1` por design)
- Iluminação importa — ambientes muito escuros degradam o tracking
- Holograma requer PySide6 (LGPL) — não impacta se não instalado
- Sem persistência de configuração entre sessões (ainda)

## Contribuindo

Esta é uma alpha pública. Feedback em hardwares e iluminações diferentes é o que pavimenta a próxima iteração.

- 🐛 Bugs: [abrir issue](https://github.com/ognistie/ai-virtual-mouse-controller/issues)
- 💬 Discussões: [GitHub Discussions](https://github.com/ognistie/ai-virtual-mouse-controller/discussions)
- 💡 Sugestões de gestos: issue com label `enhancement`

PRs são bem-vindos. Antes de submeter, leia [CONTRIBUTING.md](CONTRIBUTING.md) — cobre setup, padrões de código, convenção de commits e workflow de PR.

### Para desenvolvedores

```bash
git clone https://github.com/ognistie/ai-virtual-mouse-controller.git
cd ai-virtual-mouse-controller
make dev          # instala deps + tooling + pre-commit hooks
make check        # roda lint + types + tests
```

Documentação técnica em [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) e decisões formais em [`docs/adr/`](docs/adr/).

## Segurança

Encontrou vulnerabilidade? Veja [SECURITY.md](SECURITY.md). **Não abra issue público.**

## Licença

MIT © [@ognistie](https://github.com/ognistie)

## Créditos

- **MediaPipe Hands** (Google) — detecção de landmarks
- **OneEuroFilter** — Casiez, Roussel & Vogel, 2012
- **OpenCV** — captura e renderização
- **PyAutoGUI** — interface com o cursor do sistema
- **ModernGL** — overlay holográfico (opcional)

---

🔗 [Site oficial](https://ognistie.github.io/ai-virtual-mouse-controller/) · [Portfolio](https://ognistie.github.io/portfolio/) · [@ognistie](https://github.com/ognistie)
