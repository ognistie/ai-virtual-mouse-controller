# Arquitetura

Este documento descreve a arquitetura interna do AI Virtual Mouse Controller
para quem vai **manter ou estender** o codigo. Para uso, ver
[site oficial](https://ognistie.github.io/ai-virtual-mouse-controller/).

> A pasta `docs/` da raiz contem o **site publico** (HTML/CSS/JS).
> Este arquivo e' a fonte de docs **para desenvolvedores**.

## Camadas

```
┌──────────────────────────────────────────────────────────────┐
│  main.py                                                      │
│   └─ VirtualMouseService                                      │
└──────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  Camera      │      │  Detection   │      │  Output      │
│  ──────────  │      │  ──────────  │      │  ──────────  │
│  camera.py   │      │  hand_tracker│      │  cursor_ctrl │
│  (cv2)       │      │  gesture_det │      │  hologram_*  │
└──────────────┘      │  hand_anchor │      └──────────────┘
                      │  finger_post │
                      │  smoothing   │
                      └──────────────┘
```

### `services/`
Orquestracao. `VirtualMouseService` cola tudo via DI manual no `from_config`.
Nao contem logica de dominio — so coordena.

### `core/`
Logica de dominio. Subdividido por responsabilidade:

| Modulo | Responsabilidade | Dependencias externas |
|---|---|---|
| `camera.py` | Captura threaded da webcam | cv2 |
| `hand_tracker.py` | Wrapper do MediaPipe Hands | mediapipe |
| `gesture_detector.py` | State machine de gestos | (puro + hand_tracker) |
| `hand_anchor.py` | Ancora robusta multi-landmark | (puro) |
| `finger_posture.py` | Features anatomicas por dedo | (puro) |
| `smoothing.py` | OneEuro + EMA filters | (puro) |
| `cursor_controller.py` | Wrapper PyAutoGUI + freeze/drag-precision | pyautogui |
| `hologram_overlay.py` | Facade do overlay (QPainter / GL) | PySide6 (soft) |
| `hologram_gl_backend.py` | Renderizacao 3D OpenGL | moderngl + PySide6 (soft) |
| `hand_mesh.py` | Geracao de mesh 3D da mao | numpy |
| `click_burst.py` | State puro de aneis de click | (puro) |
| `runtime_settings.py` | Profiles + sliders em runtime | (puro) |
| `perf_telemetry.py` | Profiler p50/p99 | (puro) |
| `ui_overlay.py` | HUD em cima do preview cv2 | cv2 |
| `utils.py` | Helpers numericos | (puro) |

**Modulos puros** sao 100% testaveis sem deps pesadas (`mediapipe`,
`cv2`, `PySide6`). CI roda mypy strict so neles.

## Fluxo de um frame

```
┌──────┐    ┌──────────┐    ┌────────────┐    ┌──────────┐    ┌────────┐
│ cv2  │ -> │ mediapipe│ -> │ gesture    │ -> │ cursor   │ -> │ pyauto │
│ read │    │  Hands   │    │ detector   │    │ controller│    │ gui    │
└──────┘    └──────────┘    └────────────┘    └──────────┘    └────────┘
                                  │
                                  │ events: MOVE / CLICK / ...
                                  ▼
                            ┌──────────┐
                            │ hologram │
                            │ overlay  │
                            └──────────┘
```

Detalhes do tick (em `VirtualMouseService._tick`):

1. `camera.read()` -> frame BGR
2. `tracker.process_with_raw(rgb)` -> 21 landmarks normalizados [0,1]
3. `gesture_detector.update(hand)` -> lista de `GestureEvent` (`MOVE`, `CLICK`, etc)
4. `_handle_events(events, hand)`:
   - se `MOVE`: smoother global -> `cursor.move(nx, ny)`
   - se `CLICK/RIGHT/DOUBLE`: `cursor.click()` (que internamente faz `freeze`)
   - se `DRAG_START/END`: gerencia estado de drag
5. `_update_hologram(hand)` -> alimenta pose ao backend GL + `pump()` Qt events
6. (se preview ativo) overlay no frame, `cv2.imshow`, poll de keys

## Decisoes-chave

### Ancora robusta (RobustHandAnchor)
Ancora ponderada de todos os 21 landmarks em vez de UM ponto:
- pesos: anatomia (palma > fingertips), in-frame, estabilidade temporal
- histerese: blend com ultima ancora boa em quedas de confianca
- extrapolacao por velocidade nas bordas

Motivo: ancora unica falha em oclusao (mao de perfil, bordas do frame).
Detalhes: `core/hand_anchor.py`.

### Postura como gate (FingerPosture)
PINCH_MIDDLE so dispara com postura intencional (indicador estendido +
medio curvado). Distancia par-a-par sozinha gerava falsos positivos
devido ao acoplamento de tendoes FDP. Detalhes: `core/finger_posture.py`.

### Click freeze
Cursor trava no pixel atual por 120ms quando dispara click. Anula drift
causado pelo curl dos dedos. Mantem o **feel de mouse fisico**:
apertar botao nunca move o cursor.

### Holograma como overlay click-through
Janela Qt fullscreen com `WS_EX_LAYERED | WS_EX_TRANSPARENT`. Render
via ModernGL com shader de fresnel + depth-fade. MSAA 2x. Backend
fallback QPainter se ModernGL nao disponivel.

### Smoothing
Dois OneEuro independentes:
1. Global (`services` level): suaviza posicao do cursor
2. Por landmark (`hologram_gl_backend`): suaviza pose visual do holograma

Razao: cursor e holograma tem orcamentos de lag e jitter diferentes.

## Threading

| Thread | Responsabilidade |
|---|---|
| Main | Tick loop, MediaPipe inference, cursor moves |
| `ThreadedCamera._thread` | `cv2.VideoCapture.read()` continuo |
| Qt internal | paintGL no holograma (driven by QTimer) |

Nao ha lock entre main e camera — usamos *latest-frame-wins* pattern.

## Arquitetura de configuracao

```
config.py (constantes hardcoded)  ──┐
                                    ├──► VirtualMouseService.from_config()
runtime_settings.py (profiles)  ────┘             │
                                                  ▼
                                       Componentes via DI manual
```

`config.py` e' source-of-truth em build-time. `runtime_settings.py`
permite trocar valores em runtime via teclas (`Q/W` para profile, sliders).

## Decisoes pendentes (debt)

Listadas em ordem de prioridade pra refactor:

1. **`gesture_detector.py` (944 linhas)** — quebrar em
   `state_machine.py` + `precision_pipeline.py` + `aim_assist.py` +
   `sticky_targeting.py`.
2. **`hologram_gl_backend.py` (~970 linhas)** — quebrar em
   `window.py` + `mesh_pass.py` + `cursor_hide.py`.
3. **`hand_mesh.py` (800 linhas)** — separar geracao de palm, fingers, bridges.
4. **`services/virtual_mouse_service.py` (~830 linhas)** — extrair
   `_handle_events` e `_update_hologram` em modulos.

Ate la, regra do projeto: nao expandir esses files; codigo novo vai pra
modulo novo.

## ADRs (Architecture Decision Records)

Decisoes formais ficarao em `docs/adr/NNNN-titulo.md`. Template:
[adr-tools format](https://github.com/joelparkerhenderson/architecture-decision-record).
