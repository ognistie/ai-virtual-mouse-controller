# AI Virtual Mouse Controller

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

> Controle o cursor do sistema operacional com gestos da mão, usando apenas uma webcam.

Sistema gestual de controle de cursor com qualidade de input comparável a periféricos físicos. Sem hardware extra, sem GPU, sem treinar modelo.

🔗 **[Documentação completa →](https://ognistie.github.io/ai-virtual-mouse-controller/)**

---

## Demo

> _Adicione um GIF/vídeo demonstrativo aqui — `docs/assets/img/demo.gif`_

```
┌──────────────────────────────────────────────────────────┐
│ FPS 58  SHAPE PINCH  DPI 1.05x       PRECISION  FROZEN  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│              [vídeo da webcam — 480×270]                 │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Features

| Recurso | Descrição |
|---|---|
| 🖐️ **6 gestos canônicos** | Move, click, right-click, double-click, drag, pause |
| ⚡ **Press-to-click** | Clique no momento exato em que os dedos se tocam |
| 🎯 **Aim assist + sticky** | Desaceleração inteligente em alvos pequenos |
| 🪟 **Janela compacta** | 480×270 always-on-top, estilo OBS streamer |
| ⚙️ **Painel runtime** | 4 profiles + 6 sliders sem reiniciar |
| 📊 **Performance** | ~58 FPS estáveis em CPU, sem GPU |

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
| 🤏 Pinça mantida 3s | Inicia drag (mão aberta solta) |
| ✊ Punho fechado | Pausa o cursor |

## Atalhos

| Tecla | Função |
|---|---|
| `S` | Abre/fecha painel de configurações |
| `T` | Toggle always-on-top |
| `1-4` | Profile: smooth / precise / snappy / stable |
| `D` | Mostra tabela Recommended vs Current |
| `R` | Reset para defaults do profile |
| `A` / `K` | Toggle aim assist / sticky targeting |
| `ESC` | Sai |

## Arquitetura

Separação clássica **core / services**:

```
ai-virtual-mouse-controller/
├── main.py                          # Entry point
├── config.py                        # Constantes (camera, MediaPipe, gestos)
├── core/
│   ├── camera.py                    # ThreadedCamera (captura assíncrona)
│   ├── hand_tracker.py              # Wrapper MediaPipe Hands
│   ├── gesture_detector.py          # State machine de gestos
│   ├── cursor_controller.py         # Wrapper PyAutoGUI
│   ├── smoothing.py                 # EMA + OneEuroFilter
│   ├── ui_overlay.py                # Painel de settings em OpenCV
│   ├── runtime_settings.py          # Profiles + sliders
│   └── utils.py                     # FPS, clamp, map_range
├── services/
│   └── virtual_mouse_service.py     # Orquestração + main loop
├── tests/
└── docs/                            # Site (GitHub Pages)
```

### Pipeline (60 FPS alvo)

```
webcam ──→ MediaPipe Hands ──→ GestureDetector ──→ OneEuroFilter ──→ PyAutoGUI
(thread)   (21 landmarks 3D)    (state machine)     (smoothing)       (cursor)
```

### Decisões técnicas chave

- **Landmark 9 (base do dedo médio)** como âncora do cursor, em vez da ponta do indicador — evita pulo no momento do clique
- **OneEuroFilter** (Casiez et al., 2012) substitui EMA com α fixo — filtra agressivo em repouso, libera em movimento
- **Threading da câmera** com buffer mínimo — descarta frames atrasados, mantém 58 FPS estáveis vs 25 FPS bloqueante
- **Press-to-click via edge detection** no raw shape — feedback imediato, sem janela apertada de release
- **Composição limitada** de aim assist + sticky com `min()` em vez de produto — evita cursor travar perto de botões
- **Histerese 2/3 frames** (entrada/saída) — elimina cliques fantasma do ruído MediaPipe

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

### Constantes (config.py)

Para mudanças permanentes:

```python
CAMERA_INDEX = 0                    # 0 = webcam padrão
CAMERA_WIDTH = 960                  # resolução de captura
CAMERA_HEIGHT = 540
MODEL_COMPLEXITY = 1                # 0 = lite, 1 = full
CURSOR_ANCHOR_LANDMARK = 9          # landmark âncora
SCREEN_MARGIN_PERCENTAGE = 0.20     # zona morta nas bordas
```

## Stack

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.11+ |
| Visão computacional | OpenCV `>=4.8` |
| Detecção de mão | MediaPipe `>=0.10.9, <0.11` |
| Controle de cursor | PyAutoGUI |
| Computação | NumPy |

> **Nota:** MediaPipe 0.11+ removeu `mp.solutions.hands`. Mantenha pinned em `<0.11`.

## Troubleshooting

| Sintoma | Solução |
|---|---|
| `mediapipe` falha no `pip install` | Use Python 3.11/3.12 (não 3.13+) |
| Webcam não abre | Feche Teams/Zoom/OBS, verifique permissões de privacidade |
| Cursor treme parado | Use profile `stable` ou aumente Smoothness |
| Cliques falham | Aumente Pinch sensitivity, verifique iluminação |
| FPS < 30 | Reduza `MODEL_COMPLEXITY = 0` em `config.py` |

## Limitações conhecidas

- Testado primariamente em Windows (`cv2.CAP_DSHOW` como backend). macOS/Linux funcionam via fallback
- Detecção de uma mão por frame (`MAX_NUM_HANDS = 1` por design)
- Iluminação importa — ambientes muito escuros degradam o tracking
- Sem persistência de configuração entre sessões (ainda)

## Contribuindo

Esta é uma alpha pública. Feedback em hardwares e iluminações diferentes é o que pavimenta a próxima iteração.

- 🐛 Bugs: [abrir issue](https://github.com/ognistie/ai-virtual-mouse-controller/issues)
- 💬 Discussões: [GitHub Discussions](https://github.com/ognistie/ai-virtual-mouse-controller/discussions)
- 💡 Sugestões de gestos: issue com label `enhancement`

PRs são bem-vindos. Mantenha mudanças cirúrgicas e cobertas por testes quando possível.

## Licença

MIT © [@ognistie](https://github.com/ognistie)

## Créditos

- **MediaPipe Hands** (Google) — detecção de landmarks
- **OneEuroFilter** — Casiez, Roussel & Vogel, 2012
- **OpenCV** — captura e renderização
- **PyAutoGUI** — interface com o cursor do sistema

---

🔗 [Site oficial](https://ognistie.github.io/ai-virtual-mouse-controller/) · [Portfolio](https://ognistie.github.io/portfolio/) · [@ognistie](https://github.com/ognistie)