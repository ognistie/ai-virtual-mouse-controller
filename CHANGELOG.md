# Changelog

Todas as mudancas notaveis deste projeto vivem aqui.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Added
- **`core/cursor_motion.py`**: pipeline de movimento adaptativo puro e
  testavel (ganho por distancia, precisao continua, assistencia da borda
  inferior). `dt` injetado — sem relogio proprio, sem I/O. Ver
  `docs/adr/0002-adaptive-cursor-motion.md`.
- **Ganho adaptativo por distancia**: escala aparente da palma estimada
  por mediana de cinco segmentos estaveis (nao usa o `z` do MediaPipe),
  com filtro temporal, zona morta e limite de variacao. Mao longe ganha
  alcance (`DISTANCE_GAIN_FAR = 1.40`), mao perto ganha precisao
  (`DISTANCE_GAIN_NEAR = 0.75`).
- `MOTION_DEBUG_ENABLED` / `MOTION_DEBUG_EVERY_N_FRAMES`: log de escala,
  ganhos, pesos de precisao, assistencia inferior e re-ancoragem. So
  `logger.debug`, sem salvar frame e sem telemetria externa.
- `tests/test_cursor_motion.py` e `tests/test_cursor_pipeline.py`:
  monotonicidade e limites do ganho, invariancia com ancora parada,
  continuidade nas transicoes, ausencia de degrau no joelho, jitter em
  pixel, equivalencia 30/60 FPS, alcance e liberacao da borda inferior,
  perda/re-ancoragem da mao, semantica dos gestos e propagacao de
  perfis. Deterministas, sem `sleep`.

### Changed
- **BREAKING (comportamento)**: o cursor passa a usar movimento
  RELATIVO (integra deltas da ancora) em vez de posicao absoluta
  escalada. Mudanca de ganho, distancia, perfil ou aim assist nao
  reposiciona mais o cursor — so afeta movimentos futuros. Em
  contrapartida, a posicao da mao nao indica mais 1-pra-1 a posicao do
  cursor; congelar com ✊/✌️ e' o equivalente a levantar o mouse.
- **Aim assist e sticky viraram continuos** (peso em [0,1] com envelope
  attack/release de 100/220 ms). O holdover por timer foi substituido
  por liberacao progressiva. Em regime, o fator 0.40 passa a de fato
  reduzir a sensibilidade — antes a razao medida era 1.00.
- **Borda inferior**: os quatro mecanismos empilhados (Y boost, edge
  snap, border creep e gain de descida reforcado na ancora) dao lugar a
  UMA assistencia por ganho, C1 na entrada e proporcional ao movimento
  do proprio usuario.
- `RobustHandAnchor.compute()` passa a ser chamado exatamente uma vez
  por mao/frame (eram ate tres).
- Os thresholds da curva balistica passam a valer por SEGUNDO (com
  `velocity_reference_fps = 60` preservando o feel a 60 FPS). O
  comportamento agora e' identico a 30 e a 60 FPS.
- `DPI_FIXED_MULTIPLIER` (slider de sensibilidade) e' explicitamente o
  ganho BASE da composicao `base x distancia x precisao`.
- `RobustHandAnchor._EDGE_CRITICAL_GAIN_DOWN`: 1.4 -> 1.0. A
  assistencia especifica da borda inferior passa a viver em um lugar so.

### Deprecated
- `compute_dpi_multiplier()` e `apply_dpi_to_position()`: continuam
  exportadas mas nao sao mais usadas pelo pipeline.
- `CURSOR_EDGE_SNAP_PX` agora e' `0` por padrao (era 48) — o snap
  teleportava o cursor pro ultimo pixel.
- `CURSOR_Y_BOTTOM_BOOST_ENABLED` e `CURSOR_EDGE_CREEP_ENABLED` agora
  sao `False` por padrao. A implementacao continua no `CursorController`
  pra quem quiser religar.
- `AIM_ASSIST_HOLDOVER_SECONDS` e `DPI_MULTIPLIER_MIN`/`_MAX`: mantidos
  por compatibilidade, sem efeito no pipeline atual.

### Fixed
- Cursor se deslocava com a mao PARADA quando a escala estimada da mao
  oscilava (o ganho escalava uma posicao absoluta em torno do centro da
  tela). Medido: 38 px de varredura com ±4% de oscilacao; agora 0 px.
- Ganho por distancia estava INVERTIDO: mao perto da webcam recebia
  multiplicador alto e mao longe, baixo.
- Aim assist multiplicava um ERRO (alvo − saida) em vez de um delta de
  entrada, o que fazia dele um filtro de lag: nao reduzia sensibilidade
  e ainda deixava o cursor escorregar ~8 px depois da mao parar.
- Descontinuidade de derivada no joelho da curva da borda inferior
  (inclinacao saltava de 1.0 para 1.5).

## [1.1.0] — 2026-06-17

### Added
- **Modo apresentacao** (`PresentationController`): mao aberta cruzando
  do meio do frame pra um lado dispara seta esquerda/direita
  (PowerPoint, Google Slides, Keynote, Canva, PDFs). Toggle via tecla Z
  ou botao "Apresentacao" no painel S. Modos sao mutuamente exclusivos
  — detector de mouse fica suspenso enquanto apresentacao esta on.
- `CursorController.press_key(key)`: wrapper de `pyautogui.press()`
  pra disparar teclado sem mover o cursor.
- `CursorController.last_position` e `HologramOverlay.screen_size`:
  properties pra acessar estado interno sem violar encapsulamento.
- `UIState.action_buttons()` / `toggle_buttons()`: helpers que
  eliminam listas de botoes duplicadas em 4 lugares do `ui_overlay`.
- `tests/conftest.py` com flag `--gpu`: testes do hologram que
  precisam de display real ficam fora do `pytest` default.

### Fixed
- Removida duplicacao silenciosa de `_update_hologram` no
  `VirtualMouseService` (2 copias identicas no mesmo arquivo).
- `requirements.txt` agora alinha com `pyproject.toml` (PySide6
  adicionado, `pytest` removido da lista de runtime).
- `except Exception: pass` em paths nao-hot agora logam em
  `logger.debug` em vez de engolir o erro silenciosamente.

### Changed
- Imports nao usados removidos em `core/ui_overlay.py`.
- README ganha secao "Testes" explicando `test-fast` / `test-gpu`.
- `data/` adicionado ao `.gitignore` (runtime state).

## [1.0.6] — 2026-06-10

### Added
- **Margens assimetricas** no mapeamento camera → tela
  (`SCREEN_MARGIN_X=0.08`, `SCREEN_MARGIN_TOP/BOTTOM=0.04`). Alcance
  vertical mais facil pra atingir abas/taskbar sem forcar a mao ate o
  limite do FOV.
- **Edge velocity extrapolation reforcado** no `RobustHandAnchor`:
  duas zonas (normal < 30% da borda + critica < 8%), gain 1.4 → 2.2.
  Cursor escorrega pro canto sem precisar a mao ir ao extremo do quadro.
- **Cinematic follow-through** quando a mao sai do FOV: cursor
  continua na direcao da ultima velocidade por ate 200ms com decay
  exponencial. Termina o gesto em vez de travar abrupto.
- **Pinch 3D-aware** (`_pinch_distance`): metrica de pinch passa a
  incluir o eixo Z. Mata falso PINCH quando a mao esta de lado e os
  dedos aparecem sobrepostos no plano da imagem.

### Changed
- DPI inicial 0.85 → **1.0** (perfil smooth + RECOMMENDED).
- `CLICK_COOLDOWN_SECONDS` 0.25 → **0.15**. Permite sequencias rapidas
  de clique (abrir menu → opcao → fechar) sem descartar cliques.
- Limpeza ampla de comentarios obsoletos (refs `v6.x.y`, `NOVO vX`,
  `PERF:`) em 12 arquivos sem mudar comportamento.

## [1.0.5] — 2026-06-08

### Fixed
- `test_available_true_with_pyside` agora skipa em CI. O atributo
  `available` da overlay depende de GL context REAL — em offscreen
  retorna False legitimamente, o que falsamente quebrava o build.
- Consolida tudo do v1.0.4 que tambem nao chegou ao PyPI (CI bloqueou).

## [1.0.4] — 2026-06-08

### Fixed
- Tests de rendering Qt+OpenGL (`TestRenderingNoCrash`) agora skipam
  em ambiente CI (offscreen sem GL context real). Antes crashavam o
  pytest no Ubuntu CI, impedindo o publish.
- Consolida fixes do 1.0.3 que nao chegaram ao PyPI por falha
  transitoria de sigstore (ChunkedEncodingError).

## [1.0.3] — 2026-06-08

### Fixed
- **CRITICAL**: pin de mediapipe apertado de `<0.11.0` para `<0.10.30`.
  Mediapipe 0.10.30+ removeu silenciosamente o modulo `mp.solutions.hands`
  que esse projeto usa. Versao 0.10.21 e' o ultimo release seguro.
  Sem essa correcao, `pip install` instalava mediapipe 0.10.35 e quebrava
  no startup com AttributeError obscuro.
- main.py agora verifica versao do mediapipe ANTES de importar o resto.
  Se mp.solutions.hands nao existe, mostra mensagem clara com comando
  de fix em vez do AttributeError obscuro.

## [1.0.2] — 2026-06-08

### Changed
- Holograma 3D (PySide6 + ModernGL) **agora vem incluido por padrao** no
  install. Quem rodar `pip install ai-virtual-mouse-controller` ja tem
  o overlay pronto pra usar via tecla H — sem precisar do extra
  `[hologram]`. O extra continua existindo pra back-compat.
- Trade-off: +500MB no download base. Compensado pela UX zero-friction.

### Fixed
- Documenta workaround pro caso de usuario ter mediapipe 0.11+ instalado
  no ambiente global (downgrade explicito).

## [1.0.1] — 2026-06-08

### Added
- Primeira release publica oficial no PyPI: `pip install ai-virtual-mouse-controller`
- Entry point CLI: comando `avmc` disponivel apos install
- Workflow de auto-publish via Trusted Publishing (OIDC, sem token guardado)
- Pre-requisitos por OS documentados no README (Windows / macOS / Linux X11/Wayland)
- Extra opcional `[hologram]` (PySide6 + ModernGL) para o overlay 3D
- Suite consolidada de melhorias incrementais de v0.2.0 (ancora robusta,
  finger posture, click freeze, drag precision, holograma otimizado,
  edge velocity extrapolation, etc.)

### Changed
- Versao bumpada de 0.2.0 para 1.0.1 marcando primeira release publica

## [0.2.0] — 2026-06-07

### Added
- `core/finger_posture.py`: features anatomicas por dedo (score de extensao, rotation-invariant) usadas pra desambiguar PINCH vs PINCH_MIDDLE.
- `core/hand_anchor.py`: `RobustHandAnchor` — ancora ponderada multi-landmark resistente a oclusao + extrapolacao por velocidade nas bordas.
- `CursorController.freeze(duration)`: trava o cursor no pixel atual durante N segundos. Usado pelos metodos de click para anular o drift causado pelo curl dos dedos no pinch.
- `CursorController.drag_precision_factor`: DPI baixo durante drag (selecao de texto sem tremor).
- Sentinel `LM_ROBUST_HAND = -2` em `gesture_detector` (default novo do `CURSOR_ANCHOR_LANDMARK`).
- Sentinel `LM_PINCH_MIDPOINT = -1` em `gesture_detector` (alinha cursor com mesh do holograma).
- Configs `CLICK_FREEZE_SECONDS`, `DRAG_PRECISION_FACTOR`, `PINCH_MIDDLE_INDEX_EXTENSION_MIN`, `PINCH_MIDDLE_MIDDLE_EXTENSION_MAX` em `config.py`.
- `pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue/PR templates.

### Changed
- `SCREEN_MARGIN_PERCENTAGE`: 0.18 -> 0.10 (alcance facil ate bordas da tela sem forcar mao no limite do frame).
- `CURSOR_ANCHOR_LANDMARK`: 9 (palma) -> -2 (ancora robusta da mao toda).
- Holograma: cursor do sistema Windows escondido quando overlay ativo, restaurado no desligamento/atexit.
- Holograma: smoothing per-landmark afinado (`min_cutoff` 0.8 -> 0.55; `beta` 1.5 -> 1.8) — menos tremor em repouso, mesma responsividade.
- MSAA do holograma: 4x -> 2x (custo de fragment shader ~50% menor; visual identico em rim suave).
- Timer de repaint do holograma agora e adaptativo: 30 fps quando ha mao/burst, ~6 fps idle.
- `classify_shape`: PINCH_MIDDLE agora exige postura anatomica intencional (indicador estendido + medio curvado) — elimina falsos positivos de RIGHT_CLICK causados por acoplamento de tendoes durante PINCH normal.
- `_smooth_landmarks` e `generate_hand_mesh` reescritos com buffers pre-alocados (vetorizacao numpy + zero-alloc no caminho quente).

### Fixed
- Hologram: `_update_hologram` era chamado **2x por tick** no service (bug de copy-paste) — corrigido, ~50% do custo de CPU do caminho do holograma.
- Hologram: alinhamento cursor-holograma em telas Windows com escala 125/150/175% (DPR aplicado no ortho).
- `tests/test_gesture_detector.py` nao coletava por `ImportError` em ambientes sem mediapipe; usa `pytest.importorskip`.
- LICENSE consistente com `MIT` declarado nos badges/README (era "all rights reserved" contraditorio).

### Removed
- Aneis vermelhos de burst no click do holograma (paradigma de mouse fisico: feedback vem da UI reagindo, nao do cursor brilhando).

---

## Versoes anteriores

Versionamento formal comeca a partir da `[Unreleased]` acima. Mudancas
anteriores estao registradas em commits e nas docstrings de `config.py`
(historico v6.4, v6.5, v6.9.x). Backfill para tags retroativas pendente.
