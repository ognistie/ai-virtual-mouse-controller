# Smart Adaptive Holographic Keyboard — Specification

> Spec para integração de teclado virtual de nova geração ao projeto **AI Virtual Mouse Controller**.

**Referência do projeto:** https://ognistie.github.io/ai-virtual-mouse-controller/

O AI Virtual Mouse Controller transforma movimentos reais da mão em comandos do SO via Visão Computacional, Rastreamento de Mãos, Reconhecimento de Gestos e Interfaces Holográficas em tempo real. O teclado deve se tornar uma das funcionalidades centrais da plataforma e transmitir a sensação de um produto comercial premium.

---

## Como usar (quick start)

1. **Teste isolado (sem câmera)** — confirma que a janela aparece na sua máquina:
   ```
   python scripts/test_keyboard_visual.py
   python scripts/test_keyboard_visual.py --layout QWERTY --seconds 20
   ```
   Janela holográfica deve surgir na parte **inferior** da tela e fechar sozinha.

2. **No app completo** (`python main.py`):
   - **Clique na janela da webcam (preview) primeiro** — ela precisa estar focada
     para receber teclas (o OpenCV só captura `K`/`H`/`T` com foco nela).
   - Aperte **`K`** para abrir/fechar o teclado. Console mostra `[AVM] KEYBOARD = True`.
   - Mova o **indicador** → tecla mais próxima entra em hover (brilho + expansão).
   - Faça **pinça** (polegar + indicador) → confirma a tecla.
   - Enquanto o teclado está ativo, a pinça **digita** em vez de clicar no mouse.

3. **Ligar no startup**: `KEYBOARD_ENABLED = True` em `config.py`.

> **Se nada aparecer ao apertar K:** quase sempre é foco — a janela da webcam
> não estava em foco. Clique nela e tente de novo. Caso persista, rode o teste
> isolado do passo 1 para descartar problema de ambiente (driver/PySide6).

---

## Perfil do Arquiteto

Arquiteto de Software Sênior com expertise em UX/UI, Visão Computacional, HCI, IA, Three.js, WebGL, Electron e Sistemas de Reconhecimento de Gestos.

## Objetivo Principal

Criar a melhor experiência possível de digitação em ambiente controlado por gestos.

Prioridades (ordem):

1. Experiência do Usuário (máxima)
2. Precisão
3. Baixa Latência
4. Facilidade de Uso
5. Acessibilidade
6. Qualidade Visual
7. Escalabilidade
8. Performance

---

## Conceito do Teclado

Layout familiar **ABNT2/QWERTY** + recursos adaptativos de IA + interação por gestos + efeitos holográficos.

Uso intuitivo, sem treinamento prévio.

---

## Design Visual

Linguagem visual obrigatória:

- Futurista
- Holográfica
- Inspirada em Cyberpunk
- Interface estilo HUD
- Minimalista
- Premium
- Elegante
- Altamente legível

Características:

- Brilho neon cyan
- Glassmorphism
- Sombras suaves
- Iluminação dinâmica
- Cantos arredondados
- Bordas animadas
- Transições suaves
- Feedback visual em tempo real

### Paleta de Cores

| Função | Hex |
|---|---|
| Brilho Principal | `#00FFF0` |
| Brilho Secundário | `#00BFFF` |
| Destaque | `#33FFD1` |
| Fundo | `#050B16` |

---

## Modelo Principal de Interação

**Fluxo Hover + Pinch** (método principal):

1. Usuário move o dedo indicador.
2. Tecla mais próxima entra em estado de **Hover**.
   - Ampliação suave da tecla
   - Intensificação do brilho
   - Animação da borda
   - Ampliação da letra
   - Pulso holográfico
3. Usuário realiza gesto de **Pinça** (indicador + polegar).
4. Digitação confirmada.
   - Animação de compressão
   - Flash holográfico
   - Ripple luminoso
   - Feedback sonoro opcional

---

## Sistema de IA Adaptativa

Aprende continuamente com o usuário.

**Monitorar:**

- Velocidade de digitação
- Taxa de erro
- Tempo médio de Hover
- Teclas mais utilizadas
- Teclas frequentemente erradas

**Adaptar automaticamente:**

- Área de ativação das teclas
- Sensibilidade do Hover
- Sensibilidade do Pinch
- Precisão da predição

**Exemplo:** usuário tenta `T` mas acerta `R` → sistema amplia internamente a área de ativação de `T`.

Ajuste **invisível** ao usuário (não altera visual).

---

## Motor de Predição de Texto

Funcionalidades:

- Autocomplete
- Correção automática
- Predição contextual
- Sugestão de próximas palavras
- Histórico de uso

Sugestões aparecem **acima do teclado**, selecionáveis via Hover + Pinch.

**Exemplo:** `intel` → `Inteligência`, `Interface`, `Integrado`.

---

## Expansão Dinâmica das Teclas

Aproximação do dedo → expansão visual da tecla.

Objetivos: melhor precisão, menos erros, mais confiança. Animação fluida e natural.

---

## Acessibilidade

- Escala ajustável do teclado
- Transparência ajustável
- Modo alto contraste
- Modo de animações reduzidas
- Feedback sonoro
- Compensação para tremores leves

---

## Modos de Teclado

Suportar e trocar em tempo real:

- ABNT2
- QWERTY
- Compacto
- Completo

---

## Requisitos de Performance

Metas mínimas:

- 60 FPS
- Baixo consumo de CPU/GPU
- Baixa latência
- Renderização rápida

Otimizar para: MediaPipe, OpenCV, Three.js, WebGL, Electron.

---

## Reconhecimento de Gestos

Usar landmarks do MediaPipe.

Implementar:

- Hover Detection
- Pinch Detection
- Confidence Score
- Suavização de movimentos
- Filtros contra ruído

Evitar: cliques acidentais, tremulações, falsos positivos.

---

## Experiência do Usuário

Sensação: inteligente, confiável, responsivo, confortável, profissional.

Evitar: animações excessivas, poluição visual, fluxos complexos, curva de aprendizado desnecessária.

---

## Arquitetura e Entregáveis

1. Arquitetura completa da solução
2. Fluxo de UX detalhado
3. Estrutura de componentes
4. Estrutura de pastas
5. Gerenciamento de estado
6. Pipeline de reconhecimento de gestos
7. Sistema de renderização do teclado
8. Arquitetura da IA adaptativa
9. Arquitetura do motor de predição
10. Estratégia de acessibilidade
11. Estratégia de otimização de performance
12. Bibliotecas recomendadas
13. Modelos de dados
14. Fluxo de eventos
15. Código inicial de implementação
16. Estrutura modular e escalável

---

## Diferencial Inovador

Propostas além do core (sem comprometer usabilidade):

- Sugestões inteligentes contextuais
- Ajuste automático de sensibilidade
- Personalização por perfil
- Adaptação baseada em comportamento
- Feedback holográfico avançado

---

## Arquitetura Final (stack Python real)

> Stack real do projeto: **Python 3.12 + MediaPipe + OpenCV + PySide6 + ModernGL + pyautogui**. Não há Three.js/WebGL/Electron — renderização holográfica usa PySide6/QPainter (e ModernGL no backend opcional), já validado em [core/hologram_overlay.py](../core/hologram_overlay.py) e [core/hologram_gl_backend.py](../core/hologram_gl_backend.py).

### Estrutura de pastas

```
core/
  keyboard/
    __init__.py
    models.py          # Key, KeyLayout, KeyState, KeyboardState, KeyEvent
    layouts.py         # ABNT2, QWERTY, COMPACT, FULL (dicionários KeyLayout)
    hover.py           # HoverDetector — fingertip → tecla mais próxima
    adaptive.py        # AdaptiveModel — ajuste invisível de hit area
    prediction.py      # TextPredictor — autocomplete/correção/next-word
    controller.py      # KeyboardController — máquina de estados Hover→Pinch→Press
    renderer.py        # KeyboardRenderer — overlay PySide6 (glow, ripple, glass)
    output.py          # SystemTyper — envia teclas ao SO (pyautogui/keyboard)
    accessibility.py   # AccessibilitySettings — escala, contraste, tremor
    persistence.py     # JSON save/load de adaptive + histórico
    keyboard_overlay.py# Façade (drop-in análogo a HologramOverlay)
data/
  keyboard/
    dict_pt_br.txt     # dicionário de predição
    adaptive_profile.json
docs/
  smart-adaptive-holographic-keyboard-spec.md
```

### Fluxo de eventos

```
HandTracker (MediaPipe)
   │ landmarks (21x3)
   ▼
GestureDetector ────► Gesture (PINCH/PEACE/...)
   │
   ▼
KeyboardController.on_frame(landmarks, gesture, screen_xy)
   │
   ├─► HoverDetector.update(index_tip_xy) ─► (hovered_key, proximity[0..1])
   │
   ├─► AdaptiveModel.apply(hovered_key, finger_xy) ─► expand hit area invisível
   │
   ├─► PinchEdge (reusa estado do GestureDetector v6.9.1 press-to-click)
   │       └─► dispara KeyEvent(key, x, y, t)
   │
   ├─► TextPredictor.feed(key_event) ─► sugestões atualizadas
   │
   ├─► SystemTyper.type(key)
   │
   └─► KeyboardRenderer.set_state(state) ─► paintEvent (60 FPS)
```

### Modelos de dados (resumo)

```python
@dataclass(frozen=True)
class Key:
    code: str            # 'a', 'shift', 'space', 'enter', 'F1', ...
    label: str           # texto desenhado
    label_shift: str = ""# texto quando shift ativo
    row: int = 0
    col: float = 0       # float pra teclas largas (space=col=4, width=5)
    width: float = 1.0
    height: float = 1.0
    modifier: bool = False  # shift/ctrl/alt/altgr/caps

@dataclass(frozen=True)
class KeyLayout:
    name: str            # 'ABNT2' | 'QWERTY' | 'COMPACT' | 'FULL'
    keys: tuple[Key, ...]
    rows: int
    cols: float

@dataclass
class KeyState:
    key: Key
    hover_score: float = 0.0      # [0,1]
    pressed_t: float = 0.0        # timestamp último press
    expansion: float = 1.0        # escala visual atual
    ripple_t: float = 0.0
    error_count: int = 0
    hit_count: int = 0

@dataclass
class KeyboardState:
    layout: KeyLayout
    keys: dict[str, KeyState]
    hovered_code: str | None
    shift_on: bool = False
    caps_on: bool = False
    altgr_on: bool = False
    suggestions: tuple[str, ...] = ()
    visible: bool = False

class KeyEvent:
    code: str
    char: str
    timestamp: float
    confidence: float    # score do hover no momento do pinch
```

### Pipeline de gestos (reuso, zero duplicação)

- **Hover**: reusa landmark `LM_INDEX_TIP=8`, projeta em screen coords (mesma transform usada em [cursor_controller.py](../core/cursor_controller.py)). Smoother por-tecla: `OneEuroSmoother2D(min_cutoff=0.8, beta=1.2)`.
- **Pinch**: reusa `GestureDetector.PINCH` (press-to-click, edge detection já existente em [gesture_detector.py](../core/gesture_detector.py)). Edge `not_pinched → pinched` ⇒ `KeyEvent`.
- **Confidence Score**: `confidence = clamp(1 - dist_to_center / hit_radius, 0, 1)`.
- **Filtros**: cooldown 80 ms por tecla, debounce 2 frames anti-bounce, `tremor_compensation` opcional via OneEuro mais agressivo (acessibilidade).

### IA Adaptativa

Modelo leve (sem ML pesado — mantém latência < 1 ms):

- Para cada `Key`: contador `(hits, misses_para_cada_vizinha)` com janela exponencial.
- Quando `misses_to_neighbor[X] / hits > 0.15`: **expandir hit_radius interno** em direção a X em ~12% (cap em 25%).
- **Não altera o desenho**, só o `nearest_key` query.
- Persistência: `data/keyboard/adaptive_profile.json` (perfil por usuário).
- Reset/decay: decai 1% ao dia (evita rigidez).

### Motor de predição

- Trie compactado (PT-BR ~50k palavras) carregado lazy.
- N-gram bigrama leve em RAM (top 5k bigramas) para next-word.
- Levenshtein (dist ≤ 2) para correção.
- Top-K=3 sugestões acima do teclado; seleção via Hover+Pinch nas pílulas de sugestão.

### Renderização (PySide6 / QPainter)

Reusa pipeline de [hologram_overlay.py](../core/hologram_overlay.py):

- `QWidget` fullscreen `WindowTransparentForInput` (click-through).
- `QTimer` a 60 Hz.
- Cada tecla = `QPainterPath` com `cornerRadius=12`.
- Glow: `_draw_glowing_stroke` (já existe — 2 layers).
- Hover: `expansion = lerp(1.0, 1.18, hover_score)`, `glow_alpha *= (1+hover_score)`.
- Press: `ripple` via `QRadialGradient` expandindo (reusa `BurstManager`).
- Fundo glass: `QLinearGradient` cyan→dark + `setOpacity(0.85)`.
- Sugestões: pílulas acima do teclado, mesmo glow.

### Performance

- Cache de `QPainterPath` por tecla (recomputa só em resize/layout change).
- Cache de gradients (`QLinearGradient` é caro — reusa).
- Spatial index: grid 8×4 para `nearest_key` em O(1).
- Hover smoothing por-tecla compartilha um único timer.
- Meta: `paint < 4 ms`, `hover < 0.5 ms`, `predict < 2 ms` ⇒ 60 FPS folgado.

### Acessibilidade

| Setting | Range | Default |
|---|---|---|
| `keyboard_scale` | 0.6 – 1.8 | 1.0 |
| `keyboard_opacity` | 0.4 – 1.0 | 0.85 |
| `high_contrast` | bool | False |
| `reduced_motion` | bool | False |
| `audio_feedback` | bool | False |
| `tremor_compensation` | 0 – 3 | 0 |

### Integração no `VirtualMouseService`

- Novo membro: `self.keyboard = KeyboardOverlay(...)`.
- Toggle via tecla `K` (config: `KEYBOARD_TOGGLE_KEY='k'`).
- Em `_tick`: `self.keyboard.on_frame(landmarks, gesture, screen_xy)`.
- Quando teclado visível: cursor **não** comanda click no SO (apenas hover); pinch dispara KeyEvent em vez de mouse click. Modo "keyboard exclusivo".

### Bibliotecas (já no projeto)

- `PySide6` (overlay) — já presente
- `pyautogui` (typing) — já presente
- `mediapipe`, `opencv-python` — já presente
- **Nova opcional**: `python-Levenshtein` (correção rápida). Fallback `difflib` se ausente.

### Roadmap pós-MVP

- Swipe typing (path → palavra via Viterbi sobre trie).
- Voice input (Whisper local).
- Eye tracking integration (gaze → hover bias).
- Perfis IA por usuário (k-NN sobre padrões de erro).

---

## Critérios de Sucesso

O Smart Adaptive Holographic Keyboard deve:

- Parecer um produto comercial premium
- Ser intuitivo para novos usuários
- Possuir alta precisão de digitação
- Reduzir fadiga durante o uso
- Entregar experiência holográfica futurista
- Integrar-se perfeitamente ao AI Virtual Mouse Controller
- Permitir evolução futura para:
  - Digitação por Swipe
  - Comandos por Voz
  - Eye Tracking
  - IA Personalizada
  - Predição Avançada

> Em conflito entre estética e UX, **priorizar UX**.
