# Changelog

Todas as mudancas notaveis deste projeto vivem aqui.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

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
