<div align="center">

# AI Virtual Mouse Controller

### *Controle seu computador com gestos da mão.*

[![CI](https://github.com/ognistie/ai-virtual-mouse-controller/actions/workflows/ci.yml/badge.svg)](https://github.com/ognistie/ai-virtual-mouse-controller/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ai-virtual-mouse-controller?style=flat-square&color=22d3ee&label=pypi)](https://pypi.org/project/ai-virtual-mouse-controller/)
[![Python](https://img.shields.io/badge/python-3.11_|_3.12-22d3ee?style=flat-square)](https://pypi.org/project/ai-virtual-mouse-controller/)
[![License](https://img.shields.io/badge/license-MIT-22d3ee?style=flat-square)](LICENSE)

Webcam → 21 landmarks → cursor do sistema. Sem hardware extra. Sem GPU.

**AI-powered virtual mouse controller for Windows, macOS, and Linux.** Control the
mouse cursor, left and right clicks, drag-and-drop, and presentations with real-time
hand gestures captured by a standard webcam. Built with Python, OpenCV, MediaPipe
Hands, and PyAutoGUI; runs locally without sending camera images to the cloud.

<br/>

<table>
<tr>
<td align="center" width="20%"><img src="docs/assets/img/gesture_move_real.png" width="100%"/><br/><sub><b>🖐️ mover</b></sub></td>
<td align="center" width="20%"><img src="docs/assets/img/gesture_left_click_real.png" width="100%"/><br/><sub><b>🤏 clique</b></sub></td>
<td align="center" width="20%"><img src="docs/assets/img/gesture_left_click_real.png" width="100%"/><br/><sub><b>🤞 clique direito</b></sub></td>
<td align="center" width="20%"><img src="docs/assets/img/gesture_double_click_real.png" width="100%"/><br/><sub><b>✌️ duplo clique</b></sub></td>
<td align="center" width="20%"><img src="docs/assets/img/gesture_pause_real.png" width="100%"/><br/><sub><b>✊ pausa</b></sub></td>
</tr>
</table>

<br/>

[**Site oficial →**](https://ognistie.github.io/ai-virtual-mouse-controller/) &nbsp;·&nbsp; [**Guia de uso →**](https://ognistie.github.io/ai-virtual-mouse-controller/usage.html) &nbsp;·&nbsp; [**Changelog →**](CHANGELOG.md)

</div>

---

## O que é

O **AI Virtual Mouse Controller** é um mouse virtual com reconhecimento de gestos pela webcam. O MediaPipe identifica 21 pontos da mão em tempo real, uma state machine traduz gestos em eventos do sistema operacional (move, click, drag, scroll), e um holograma 3D opcional renderiza a mão virtual sobre o desktop.

100% local. Sem nuvem, sem telemetria, sem persistir nada da webcam.

**Stack:** Python 3.11+ · OpenCV · MediaPipe · PyAutoGUI · NumPy · ModernGL/PySide6 (opcional)

---

## Instalar

Requer **Python 3.11 ou 3.12** + webcam.

```bash
pip install ai-virtual-mouse-controller
avmc
```

Posicione a mão a ~50cm da câmera. Atalhos em runtime:

| Tecla | Ação |
|---|---|
| `H` | liga/desliga o holograma 3D |
| `S` | abre/fecha o painel de configurações |
| `T` | alterna janela sempre-no-topo |
| `ESC` | sai |

<details>
<summary><b>Instalação via clone do repositório</b></summary>

```powershell
git clone https://github.com/ognistie/ai-virtual-mouse-controller.git
cd ai-virtual-mouse-controller
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Para desenvolvimento: `make dev` instala dependências + pre-commit hooks.

</details>

---

## Atualizar

```bash
pip install --upgrade ai-virtual-mouse-controller
```

Conferir a versão instalada:

```bash
pip show ai-virtual-mouse-controller
```

Se quiser pular o cache do pip e baixar do PyPI direto:

```bash
pip install --no-cache-dir --upgrade ai-virtual-mouse-controller
```

Para quem instalou via clone do repo:

```bash
cd ai-virtual-mouse-controller
git pull origin main
pip install -r requirements.txt
```

Histórico de mudanças em [CHANGELOG.md](CHANGELOG.md).

---

## Gestos

| Gesto | Símbolo | Ação |
|---|:---:|---|
| Mão aberta — 4 dedos pra cima | 🖐️ | Move o cursor |
| Pinça polegar + indicador | 🤏 | Clique simples |
| Pinça polegar + indicador (1,5s+) | 🤏 | Iniciar arrasto (drag) |
| Pinça polegar + médio | 🤞 | Clique direito |
| Dois dedos (paz) | ✌️ | Duplo clique |
| Punho fechado | ✊ | Cursor congelado |
| Mão fora do frame | — | Pausa automática |

Os cliques (esquerdo e direito) disparam no **press** — assim que os dedos se encostam — replicando o feedback de um botão físico.

---

## Configuração

Toda a calibração vive em [`config.py`](config.py) — constantes nomeadas com docstrings explicando o quê, o porquê e o intervalo de ajuste. Principais grupos:

| Seção | O que controla |
|---|---|
| `CAMERA` | índice da câmera, resolução, FPS alvo |
| `CURSOR_ANCHOR` | qual ponto da mão o cursor segue (`-2` = âncora robusta, default) |
| `PINCH` | thresholds de detecção de clique (com escala adaptativa por tamanho da mão) |
| `DPI` / `SMOOTHING` | sensibilidade do cursor + filtro OneEuro |
| `SCREEN_MARGIN_*` | margens assimétricas (top/bottom < lateral) pra alcançar cantos |
| `CURSOR_FOLLOWTHROUGH_*` | cinematic prediction quando a mão sai do FOV |
| `HOLOGRAM` | backend (GL/QPainter), cor, opacidade, FPS |

Ajustes em tempo de execução também ficam disponíveis no painel (tecla `S`) — perfis prontos: *smooth*, *precise*, *responsive*, *stable*.

---

## Compatibilidade

| OS | Setup adicional |
|---|---|
| 🪟 **Windows 10/11** | Nada. Funciona out-of-the-box após `pip install`. |
| 🍎 **macOS** | Após primeiro run, liberar **System Settings → Privacy & Security → Accessibility** (cursor) e **Camera** (webcam). Em Apple Silicon, se PyAutoGUI reclamar: `pip install pyobjc-core pyobjc`. |
| 🐧 **Linux (X11)** | `sudo apt install scrot python3-tk python3-dev` (Ubuntu/Debian). PyAutoGUI precisa desses pra capturar a tela. |
| 🐧 **Linux (Wayland)** | Suporte limitado de PyAutoGUI. Recomendado mudar pra sessão X11. |

> **Mediapipe pin:** o projeto usa `mediapipe < 0.10.30`. Versões 0.10.30+ removeram o módulo `mp.solutions.hands` que sustenta o pipeline. O `pyproject.toml` já fixa o limite — não há nada manual pra fazer.

---

## Como funciona

```
webcam → MediaPipe Hands (21 landmarks)
          ↓
     RobustHandAnchor  ← combina anatomia + estabilidade + edge extrapolation
          ↓
     GestureDetector   ← state machine: shape → event (CLICK, MOVE, DRAG…)
          ↓
     OneEuroSmoother   ← filtro adaptativo de cursor
          ↓
     CursorController  ← PyAutoGUI move o cursor real
```

Componentes auxiliares: `HologramOverlay` (renderização 3D opcional via ModernGL), `RuntimeSettings` (perfis e sliders), `PerfTelemetry` (timing p50/p99 por estágio).

Estrutura de pastas:

```
core/        # camera, hand_tracker, gesture_detector, hand_anchor, smoothing
services/    # orquestração do loop principal
config.py    # constantes de calibração documentadas
main.py      # entry point
tests/       # cobertura dos módulos puros (sem cv2/mediapipe)
docs/        # site estático + assets
```

---

## Contribuir

Issues, PRs e feedback técnico são bem-vindos. Comece em [CONTRIBUTING.md](CONTRIBUTING.md). Bugs em [issues](https://github.com/ognistie/ai-virtual-mouse-controller/issues/new?template=bug_report.yml).

Workflow local rápido:

```bash
make dev        # instala deps + hooks
make check      # ruff + mypy + pytest
make test-fast  # só testes rápidos (sem GPU/integration/slow)
make test-gpu   # só testes que precisam de display/GPU real
make test-cov   # com coverage HTML
```

Testes marcados com `gpu` (overlay PySide6/ModernGL) ficam fora do `pytest` default — eles requerem display real e podem crashar em ambientes headless. Rode-os explicitamente com `make test-gpu` quando estiver mexendo no holograma.

---

<div align="center">

MIT © [Guilherme Moraes Franco](https://github.com/ognistie)

</div>
