<div align="center">

# AI Virtual Mouse Controller

### *Controle seu computador com gestos da mão.*

Sistema gestual de cursor com qualidade de input comparável a periféricos físicos.<br/>
Sem hardware extra. Sem GPU. Sem treinar modelo.

[![CI](https://github.com/ognistie/ai-virtual-mouse-controller/actions/workflows/ci.yml/badge.svg)](https://github.com/ognistie/ai-virtual-mouse-controller/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-3b82f6?style=flat-square)
![Version](https://img.shields.io/badge/version-0.2.0-22d3ee?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-10b981?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows-6b7280?style=flat-square)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000?style=flat-square)](https://github.com/astral-sh/ruff)

[**Site oficial →**](https://ognistie.github.io/ai-virtual-mouse-controller/) · [**Guia de uso →**](https://ognistie.github.io/ai-virtual-mouse-controller/usage.html) · [**Discussions →**](https://github.com/ognistie/ai-virtual-mouse-controller/discussions)

</div>

---

## O que é

Uma camada entre **mão, câmera e sistema**. A webcam captura, o MediaPipe interpreta 21 landmarks por frame, um motor de gestos traduz movimento em comandos do SO. Em paralelo, um holograma 3D em ModernGL renderiza a mão sobre o desktop em tempo real.

```
VER ────────→ ENTENDER ────────→ AGIR
webcam        21 landmarks        cursor + click
MediaPipe     state machine       PyAutoGUI
              postura anatômica   feel de mouse físico
```

---

## Quick Start

> Requer Python 3.11 ou 3.12 (mediapipe ainda não estabilizou em 3.13+) e webcam.

```powershell
git clone https://github.com/ognistie/ai-virtual-mouse-controller.git
cd ai-virtual-mouse-controller

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python main.py
```

Janela 480×270 aparece no canto inferior direito. Mão a ~50cm da câmera, palma para frente. **`ESC`** para sair.

---

## Gestos

| Gesto | Ação | Como funciona |
|---|---|---|
| 🖐️ **Mão aberta** | Move o cursor | Âncora robusta multi-landmark — segue a mão mesmo com oclusão parcial |
| 🤏 **Pinça polegar + indicador** | Clique esquerdo | Press-to-click no toque + freeze de 120ms para anular drift |
| 🤞 **Polegar + médio, indicador estendido** | Clique direito | Postura anatômica obrigatória — sem falso positivo durante pinch normal |
| ✌️ **Peace (~500ms)** | Duplo clique | Sinal de V sustentado |
| 🤏 **Pinça mantida 1.5s** | Inicia drag | DPI baixo automático para seleção fina de texto |
| ✊ **Punho fechado** | Pausa | Cursor congela na posição atual |

---

## Recursos

<table>
<tr>
<td valign="top">

**Precisão**
- Âncora robusta multi-landmark
- Postura anatômica anti acoplamento de tendões
- Click freeze (mouse-like feel)
- Drag precision factor para seleção fina
- OneEuroFilter adaptativo

</td>
<td valign="top">

**Imersão**
- Holograma 3D ModernGL opcional
- Pseudo-3D com fresnel rim + depth fade
- Click-through nativo (não bloqueia desktop)
- MSAA 2× otimizado
- Timer adaptativo (idle/ativo)

</td>
</tr>
<tr>
<td valign="top">

**Performance**
- ~58 FPS estáveis em CPU
- Sem GPU dedicada
- Pre-allocated buffers (zero-alloc no hot path)
- Mesh cache + landmark smoothing
- Telemetry p50/p99 opt-in

</td>
<td valign="top">

**Controle em runtime**
- 4 profiles (smooth · precise · snappy · stable)
- 6 sliders ajustáveis sem reiniciar
- Always-on-top configurável
- Toggle holograma via tecla `H`
- Atalhos para tudo

</td>
</tr>
</table>

---

## Atalhos

| Tecla | Função |
|:---:|---|
| `S` | Painel de configurações |
| `T` | Always-on-top |
| `H` | Holograma da mão |
| `1`–`4` | Profile: smooth / precise / snappy / stable |
| `D` | Tabela Recommended vs Current |
| `R` | Reset para defaults do profile |
| `A` / `K` | Toggle aim assist / sticky targeting |
| `ESC` | Sair |

---

## Holograma da mão

Renderização 3D em tempo real sobre o desktop. Mesh anatômico com 21 joints + fresnel rim + depth fade. Acompanha sua mão real com latência mínima.

```bash
pip install PySide6 moderngl
```

Aperta **`H`** em runtime. Default desligado. Sem PySide6/ModernGL o projeto roda normalmente, só o holograma fica indisponível.

**Características técnicas:**
- ModernGL backend com shader GLSL custom
- Per-landmark OneEuroFilter (`min_cutoff=1.2`, `beta=2.5`) — sem lag perceptível
- Adaptive timer (idle 20fps / ativo 30fps)
- Pose anchor no midpoint(4,8) — alinhamento cirúrgico com pinch

---

## Arquitetura

```
ai-virtual-mouse-controller/
├── main.py                          # Entry point
├── config.py                        # Constantes (camera, MediaPipe, gestos, perf)
├── core/
│   ├── camera.py                    # ThreadedCamera (captura assíncrona)
│   ├── hand_tracker.py              # Wrapper MediaPipe Hands
│   ├── hand_anchor.py               # Âncora robusta multi-landmark
│   ├── finger_posture.py            # Features anatômicas (extensão por dedo)
│   ├── gesture_detector.py          # State machine de gestos
│   ├── cursor_controller.py         # PyAutoGUI + freeze + drag precision
│   ├── smoothing.py                 # EMA + OneEuroFilter
│   ├── hologram_overlay.py          # Facade Qt
│   ├── hologram_gl_backend.py       # Backend ModernGL 3D
│   ├── hand_mesh.py                 # Geração de mesh anatômico
│   ├── click_burst.py               # State de bursts de feedback
│   ├── perf_telemetry.py            # TickProfiler p50/p99
│   ├── runtime_settings.py          # Profiles + sliders
│   ├── ui_overlay.py                # HUD OpenCV
│   └── utils.py                     # Helpers numéricos
├── services/
│   └── virtual_mouse_service.py     # Orquestração + main loop
├── tests/                           # 116 testes pytest
├── docs/                            # Site GitHub Pages + ADRs
│   └── adr/                         # Architecture Decision Records
└── scripts/
    └── test_hologram_visual.py      # Smoke do overlay
```

### Decisões técnicas (extrato)

- **Âncora robusta** ponderada por anatomia + visibilidade + estabilidade temporal — resiliente a oclusão. Detalhes em [`docs/adr/0001`](docs/adr/0001-robust-hand-anchor.md).
- **Postura anatômica** valida intenção em PINCH_MIDDLE — elimina falso positivo causado por acoplamento de tendões FDP.
- **Click freeze** trava cursor no pixel durante 120ms após click — anula drift do curl dos dedos.
- **Drag precision factor** reduz DPI a 0.55× durante drag — seleção fina de texto sem tremor.
- **Edge velocity extrapolation** estende cursor para borda quando confidence cai — alcance fácil de cantos da tela.

Documentação técnica completa em [`docs/adr/`](docs/adr/) — Architecture Decision Records.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.11 ou 3.12 |
| Visão computacional | OpenCV `>=4.10` |
| Detecção de mão | MediaPipe `>=0.10.18, <0.11` |
| Controle de cursor | PyAutoGUI |
| Computação | NumPy |
| Holograma 3D | ModernGL + PySide6 (opcional) |

> MediaPipe `0.11+` removeu `mp.solutions.hands`. Mantenha pinned em `<0.11`.

---

## Profiles e sliders

<details>
<summary><b>4 profiles para diferentes usos</b></summary>

| Profile | Ideal para |
|---|---|
| `smooth` | Uso geral (padrão) |
| `precise` | Alvos pequenos, links e botões "X" |
| `snappy` | Movimento amplo, drags longos |
| `stable` | Tremor natural ou setups instáveis |

</details>

<details>
<summary><b>6 sliders em runtime via tecla S</b></summary>

| Slider | Controla |
|---|---|
| Sensitivity | Multiplicador DPI (0.50× – 1.50×) |
| Aim assist | Desaceleração ao mirar (0 – 0.80) |
| Smoothness | Filtragem OneEuro (raw ↔ smooth) |
| Pinch | Threshold de detecção (0.050 – 0.110) |
| Sticky | Fricção em desaceleração (0 – 0.50) |
| Anchor freeze | Congelamento ao iniciar pinça (0 – 200ms) |

</details>

---

## Tests

```bash
pytest tests/
```

**116 testes** cobrindo: hand renderer, click bursts, hologram overlay, OneEuro smoothing, cursor controller, perf telemetry. Para desenvolvimento completo:

```bash
make dev          # instala deps + tooling + pre-commit hooks
make check        # lint + types + tests
```

---

## Troubleshooting

| Sintoma | Solução |
|---|---|
| `mediapipe` falha no `pip install` | Use Python 3.11 ou 3.12 (não 3.13+) |
| Webcam não abre | Feche Teams/Zoom/OBS + verifique permissões |
| Cursor treme parado | Profile `stable` ou aumente Smoothness |
| Cliques falham | Aumente Pinch sensitivity + verifique iluminação |
| FPS < 30 | Confira `inference` no log PERF (`MODEL_COMPLEXITY=0` é default) |
| Holograma não aparece | `pip install PySide6 moderngl` + `H` em runtime |
| Right click não dispara | Ajuste `PINCH_MIDDLE_INDEX_EXTENSION_MIN` em config |
| Drag dispara sem querer | Suba `DRAG_HOLD_SECONDS` (1.5 → 2.0) |

---

## Contribuindo

Esta é uma alpha pública. Feedback em hardwares e iluminações diferentes pavimenta a próxima iteração.

- 🐛 [Reportar bug](https://github.com/ognistie/ai-virtual-mouse-controller/issues/new?template=bug_report.yml)
- 💡 [Sugerir feature](https://github.com/ognistie/ai-virtual-mouse-controller/issues/new?template=feature_request.yml)
- 💬 [Discussions](https://github.com/ognistie/ai-virtual-mouse-controller/discussions)

PRs são bem-vindos. Leia [CONTRIBUTING.md](CONTRIBUTING.md) — cobre setup, padrões, Conventional Commits.

---

## Segurança e privacidade

Projeto **offline by-design**:

- ❌ Sem rede / HTTP / sockets
- ❌ Sem telemetria / analytics
- ❌ Sem persistência da webcam em disco
- ❌ Sem cloud / serviços externos

Vulnerabilidade? Veja [SECURITY.md](SECURITY.md) — canal privado de report.

---

## Licença

MIT © [Guilherme Moraes Franco](https://github.com/ognistie)

## Créditos

[MediaPipe Hands](https://google.github.io/mediapipe/) (Google) · [OneEuroFilter](https://gery.casiez.net/1euro/) (Casiez, Roussel & Vogel — 2012) · [OpenCV](https://opencv.org/) · [PyAutoGUI](https://pyautogui.readthedocs.io/) · [ModernGL](https://moderngl.readthedocs.io/)

---

<div align="center">

[**Site**](https://ognistie.github.io/ai-virtual-mouse-controller/) · [**Guia de uso**](https://ognistie.github.io/ai-virtual-mouse-controller/usage.html) · [**Portfolio**](https://ognistie.github.io/portfolio/)

</div>
