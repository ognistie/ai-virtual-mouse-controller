# Changelog

Todas as mudancas notaveis deste projeto vivem aqui.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Changed
- **Cursor bem mais responsivo, sem perder estabilidade.** O filtro
  OneEuro recebe coordenadas normalizadas (0–1), mas o `beta` estava na
  escala de pixels (~0.02–0.06) — a parte adaptativa do filtro ficava
  inerte e o cursor "arrastava" atrás da mão. Com `beta` na escala certa
  (perfis e slider *Suavização*), medido com `scripts/eval_motion.py`:
  atraso num movimento rápido cai de ~133 px para ~41 px e o tempo para
  o cursor assentar de ~670 ms para ~150 ms; jitter com a mão parada
  inalterado (~0.7 px).
- **Pinça com histerese** (`PINCH_RELEASE_RATIO = 1.25`): para soltar, os
  dedos precisam abrir um pouco além do limiar de entrada. Acaba o
  clique fantasma quando a pinça fica na borda do limiar (1,27 → 1,00
  clique por pinça no cenário com ruído) e o drag não cai sozinho. Dois
  cliques intencionais seguidos continuam funcionando.
- **Cada frame da webcam é processado uma vez.** O loop rodava mais
  rápido que a câmera e reprocessava o mesmo frame — inferência
  desperdiçada e frames repetidos distorcendo histerese, sticky e
  follow-through. Agora espera o frame novo e, enquanto isso, só mantém
  painel/holograma/teclado vivos (sem CPU girando à toa no startup).
- Slider **"Trava no clique"** (antes "Congelar âncora") agora funciona: controla quanto
  tempo o cursor fica parado após o clique. Antes não tinha efeito. O
  perfil padrão mantém os 120 ms de sempre.
- Ao fim da trava do clique o cursor alcança a mão em poucos frames em
  vez de saltar a distância percorrida durante a trava.
- `config.PROFILE` escolhe o perfil inicial do painel.
- **Cursor acompanha a mão sem "correr na frente".** Três causas medidas
  e corrigidas:
  - DPI adaptativo usava o tamanho da mão cru, frame a frame: só inclinar
    o pulso (mão parada) andava o cursor ~200 px em 2 s. Agora o tamanho
    (proxy da distância da câmera) é suavizado — `DPI_SIZE_SMOOTHING_S`.
    Trajeto fantasma cai para ~42 px.
  - Âncora da mão: votação por forma — dedos saindo pelo quadro não puxam
    mais o centro pra dentro. Borda lateral e inferior alcançadas sem o
    cursor passar da mão e voltar (overshoot lateral 40 → 0 px); salto ao
    perder dedos no quadro 83 → 0 px; deriva do clique 2,9 → 0,8 px.
  - Extrapolação por velocidade nas bordas recalibrada (¼ do ganho) após o
    fix da âncora calculada uma vez por frame.
  A amplificação com a mão perto da câmera foi mantida de propósito:
  reduzi-la deixaria 70–230 px sem alcance nas bordas.

- **Holograma da mão no estilo das mãos de headset VR (Quest).** Mão 3D
  lisa, azul-acinzentada, com luz suave, borda luminosa, antebraço que
  some em fade e pontas do polegar/indicador que brilham conforme a pinça
  fecha. Translucidez de camada única (depth pre-pass): dedo na frente da
  palma não "soma" transparência. Estilos em `HOLOGRAM_STYLE`: `quest`
  (padrão), `graphite`, `neon` (visual antigo).
- **Mão 3D segue a pose real** (`core/hand_pose.py`): aspecto correto da
  câmera (antes a mão saía achatada ~0,56× na horizontal num frame 16:9),
  escala pela palma (fechar a mão não "infla" mais a mão virtual), a mão
  translada 1:1 com o cursor e mexer os dedos não arrasta a mão inteira,
  profundidade real de cada articulação (dedos curvados ficam atrás do
  dorso, como na vista em primeira pessoa).
- Mesh nova (`core/hand_mesh.py`): palma segue a linha real dos nós,
  dedos nascem de dentro da palma, polegar sai da tenar, pontas
  arredondadas terminando no landmark; ~2000 vértices em ~3 ms.
- Holograma usa o OpenGL do próprio Qt (`core/hand_gl_renderer.py`) — a
  dependência `moderngl` saiu. O fallback 2D (`core/hand_soft_renderer.py`)
  desenha a mesma mão/paleta quando não há OpenGL 3.3.
- `scripts/render_hand_preview.py`: renderiza a mão offscreen (PNG) com
  poses sintéticas ou reais lado a lado com o frame da câmera.

### Fixed
- Holograma **invisível** no Windows com Smart App Control: a política
  bloqueava a DLL do `glcontext` (moderngl); a janela abria mas o contexto
  OpenGL falhava e não havia fallback. Agora o OpenGL 3.3 é testado na
  inicialização e, sem ele, o renderer 2D assume.
- Testes `--gpu` do holograma voltaram a rodar (antes davam access
  violation).
- Webcam desconectada ou tomada por outro app no meio do uso: o app
  avisa no preview, solta qualquer botão do mouse que estivesse
  pressionado (drag) e reconecta sozinho quando a câmera volta. Antes
  seguia processando a última imagem congelada.
- Webcam ocupada/inexistente ao abrir: mensagem clara com dicas e
  código de saída 1, em vez de traceback. A câmera também não fica presa
  se outra etapa da inicialização falhar.
- Âncora robusta da mão calculada uma vez por frame (era 2–3 vezes; o
  estado interno — histórico, velocidade, histerese — era corrompido).
- Defaults internos do service alinhados com `config.py` (cores e
  opacidade do holograma, zona morta da apresentação, assistência da
  borda inferior).

### Added
- `scripts/eval_motion.py`: harness que roda o pipeline real com mãos
  sintéticas, relógio simulado e mouse falso — mede jitter, atraso,
  assentamento, cliques fantasma e alcance da taskbar.
- Testes: máquina de estados do `GestureDetector`, câmera (frames novos,
  queda e reconexão), sensação do cursor, perfis do painel, câmera
  perdida no service e um gate de qualidade de movimento
  (`tests/test_motion_quality.py`).

### Removed
- Fallback de "clique ao soltar" do detector — era código inalcançável
  (nunca disparava). `PINCH_MIN_HOLD_SECONDS` fica sem efeito.

## [1.2.0] — 2026-10-13

### Changed
- **Suporte a Python 3.10 até 3.14** (antes: 3.11–3.13, e na prática só até
  3.12). `requires-python` passa a ser `>=3.10`, sem teto.
- Rastreamento da mão migrado de `mp.solutions.hands` (removido no
  mediapipe 0.10.30) para a Tasks API (`HandLandmarker`). O modelo
  `hand_landmarker.task` vai empacotado em `core/models/`. Pontos da mão,
  lateralidade e dedos levantados conferidos contra a API antiga.
- Dependências sem teto artificial: `mediapipe>=0.10.18`, `numpy>=1.26.0`.
- `opencv-python` trocado por `opencv-contrib-python` (mesmo `cv2`): o
  mediapipe novo já depende dele, e os dois pacotes juntos em versões
  diferentes misturavam a pasta `cv2`. Quem atualiza da 1.1.2 pode rodar
  `pip uninstall -y opencv-python` (ver README → Atualizar).
- Erro claro quando falta `libEGL` no Linux (exigido pelo mediapipe novo).
- Testes do `HandTracker`, incluindo um quadro real do vídeo de demo.

### Added
- `py -m avmc` / `python -m avmc` roda o projeto mesmo sem a pasta
  `Scripts` do Python no PATH.
- Windows: na primeira execução, se a pasta do `avmc.exe` não estiver no
  PATH (caso do instalador novo do Python 3.14), ela é adicionada ao PATH do
  usuário — `avmc` passa a funcionar em terminais novos. Desliga com
  `AVMC_NO_PATH_SETUP=1`.
- CI roda os testes em Python 3.10–3.14 (Ubuntu) e 3.12/3.14 (Windows).

### Deprecated
- `MODEL_COMPLEXITY` em `config.py` não tem mais efeito: a Tasks API tem
  um único modelo (equivalente ao antigo `1`).

## [1.1.2] — 2026-07-29

### Changed
- **Acesso da barra de tarefas**: o gap terminal acima da taskbar passa a
  ser fechado por UMA assistencia suave em `CursorController`
  (`CURSOR_BOTTOM_REACH_*`). Perto da borda, o cursor ganha um empurrao
  EXTRA proporcional ao quanto o usuario ja esta descendo e a proximidade
  do fundo (smoothstep, C1). Nao teleporta, nao move cursor parado e
  subir cancela na hora. Resto do movimento inalterado.
- Secao **MODOS** no painel de ajustes (Qt): botoes para ativar o modo
  apresentacao e o modo holograma da mao, sincronizados com as teclas Z/H.
  Altura do painel passa a acompanhar o conteudo (nao corta os toggles).

### Deprecated
- `CURSOR_EDGE_SNAP_PX` agora e' `0` por padrao (era 48): saltava o cursor
  direto pro ultimo pixel (teleporte).
- `CURSOR_EDGE_CREEP_ENABLED` agora e' `False` por padrao: deslizava o
  cursor com a mao parada. Ambos os mecanismos seguem implementados no
  `CursorController` pra quem quiser religar.

## [1.1.1] — 2026-07-28

### Added
- Painel nativo de ajustes em PySide6, acessível pela tecla `S` ou pelo
  handle lateral, com perfis, sliders e sincronização em tempo real.
- Testes dedicados ao painel Qt e ao alcance inferior do cursor.

### Fixed
- Melhor acesso à barra de tarefas com boost progressivo no eixo Y,
  snap de borda e deslocamento assistido na região inferior.
- Âncora da mão com tratamento assimétrico na borda inferior, reduzindo
  perda de alcance quando landmarks próximos ao pulso saem do frame.

### Changed
- Site do projeto reformulado com apresentação mais limpa, demonstração
  e instruções de instalação atualizadas.

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
