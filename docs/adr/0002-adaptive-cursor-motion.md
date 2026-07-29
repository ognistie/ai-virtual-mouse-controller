# ADR 0002 — Movimento adaptativo do cursor por distância da mão

- **Status**: Accepted
- **Data**: 2026-07-29
- **Decisor**: @ognistie
- **Substitui parcialmente**: mecanismos de borda inferior descritos em
  `config.py` (Y bottom boost, edge snap, border creep) e o gain de
  descida reforçado da ADR 0001.

## Contexto

O cursor devia adaptar a sensibilidade à distância da mão até a webcam:
longe → alcance, perto → precisão. O pipeline v1.1.1 fazia o oposto e
tinha três defeitos estruturais que nenhuma mudança de constante resolve.

### 1. Ganho invertido

`compute_dpi_multiplier()` (`core/gesture_detector.py`) crescia com o
tamanho aparente da mão:

```
ratio >= 1.0 (mão grande/perto)  -> até DPI_MULTIPLIER_MAX = 1.4
ratio <  1.0 (mão pequena/longe) -> até DPI_MULTIPLIER_MIN = 0.7
```

Medido com trajetória idêntica em três distâncias: 655 px (longe),
868 px (neutra), 1215 px (perto). Quem estava longe — com landmarks
menores e mais ruidosos — era exatamente quem tinha menos alcance.

### 2. Ganho aplicado a uma posição absoluta

`apply_dpi_to_position()` escalava `pos - (0.5, 0.5)`. Como a escala
estimada da mão oscila alguns por cento por frame (respiração, ruído de
landmark), o cursor se deslocava com a mão **parada**, proporcionalmente
à distância até o centro da tela. Medido com a mão imóvel a (0.80, 0.30)
e escala oscilando ±4%: **38 px de varredura em X**.

### 3. Aim assist que não reduzia sensibilidade

O pipeline de precisão calculava `delta = alvo − saída_anterior`: um
**erro** entre espaço de entrada e espaço de saída, não um delta de
entrada. Multiplicar esse erro por `AIM_ASSIST_SLOWDOWN_FACTOR` não
diminui o ganho — transforma o pipeline num filtro de lag de 1ª ordem.
Medido em regime permanente: **razão 1.00** entre com e sem aim assist
(o fator 0.40 era inócuo). O efeito real era outro: com a mão parada, o
cursor continuava escorregando **8,2 px** em direção à âncora.

### Defeitos secundários confirmados

- `RobustHandAnchor.compute()` (stateful) era chamado 2–3× no mesmo
  frame — histórico de estabilidade e velocidade de extrapolação
  medidos em passos falsos.
- `_boost_bottom()` era contínua em posição mas não em derivada: no
  joelho a inclinação saltava de 1.0 para `POWER` (1.5), mudando a
  velocidade aparente em degrau.
- `CURSOR_EDGE_SNAP_PX = 48` teleportava o cursor para o último pixel.
- `CURSOR_EDGE_CREEP` movia o cursor com a mão **parada** — o oposto do
  comportamento desejado.
- Quatro mecanismos de borda inferior (boost, snap, creep, gain de
  descida na âncora) somavam aceleração e predição em pontos diferentes
  do pipeline, sem teto conjunto.

## Decisão

Extrair toda a matemática de movimento para um módulo puro,
`core/cursor_motion.py`, e trocar o modelo absoluto por um **integrador
de deltas**:

```
saída += (âncora_t − âncora_{t−1}) × ganho_total
ganho_total = sensibilidade_base × ganho_distância × ganho_precisão
```

### Estimativa de distância

Não usa o `z` do MediaPipe (relativo ao pulso, subestimado, ruidoso).
Usa a **escala aparente da palma**: mediana de cinco segmentos estáveis
(`0↔9`, `5↔9`, `9↔13`, `13↔17`, `5↔17`), cada um normalizado pela sua
proporção anatômica aproximada, com rejeição de outlier, passa-baixa de
1,2 Hz, zona morta de 5% e limite de variação do ganho de 1,5/s.

### Ganho por distância

```
escala ≤ referência:  gain = lerp(1.0, gain_far,  smoothstep(t))
escala > referência:  gain = lerp(1.0, gain_near, smoothstep(t))
```

Monotônico e **inverso**. Como os dois ramos partem da referência com
`t = 0` e `smoothstep′(0) = 0`, a curva é C1 na emenda: atravessar a
distância neutra não muda a velocidade aparente em degrau.

### Precisão contínua

`aim assist` e `sticky` viram pesos em [0, 1] com envelope
attack/release (τ = 100 ms / 220 ms). O holdover por timer sai; a
liberação progressiva do envelope faz o mesmo papel sem degrau.

```
precision = lerp(1, aim_slowdown, w_aim) × lerp(1, sticky_friction, w_sticky)
```

### Borda inferior: uma assistência, por ganho

A pilha de quatro mecanismos vira uma só, e ela é um **ganho**, não uma
velocidade injetada:

```
prox  = clamp((y − (borda − faixa)) / faixa, 0, 1)
drive = smoothstep(velocidade_descendente / velocidade_de_intenção)
ganho = 1 + (ganho_max − 1) × smoothstep(prox) × drive   [apenas dy > 0]
```

A escolha por ganho, e não por velocidade, é o que preserva o invariante
central: com a mão parada `dy = 0`, logo a assistência vale exatamente
zero — é impossível o cursor fugir sozinho. Subir devolve ganho 1.0 no
mesmo frame.

## Consequências

### Positivas

- Ganho na direção certa: 1143 px (longe) / 817 px (neutra) / 613 px
  (perto) para a mesma trajetória.
- Mão parada com escala oscilando: **0,0 px** de deslocamento (era 38).
- Aim assist com razão medida **0,40** e **0 px** de escorregamento
  após a mão parar (era 1,00 e 8,2 px).
- Sem teleporte: maior passo numa descida completa cai para 25 px/frame
  (o edge snap dava 48 px de uma vez).
- Comportamento igual por tempo físico a 30 e 60 FPS (≤ 2 px em 2 s).
- Matemática pura, com `dt` injetado: testável sem `sleep`, sem câmera
  e sem PyAutoGUI real.
- Custo medido: p50 13 µs, p99 17 µs por frame.

### Negativas

- **Movimento relativo desacopla mão e cursor.** Depois de muitos
  movimentos, a posição da mão não indica mais a posição do cursor —
  como num mouse físico, e diferente do comportamento anterior. O
  equivalente a "levantar o mouse" é congelar com ✊ / ✌️ e reposicionar
  (`hold()` re-alinha a entrada sem mover a saída).
- **Deriva residual.** O portão anti-tremor quebra o telescópio da soma
  de deltas, então ruído estacionário produz um passeio aleatório lento.
  Medido: ≤ 6 px em 10 s de mão parada com ruído de ±0.0015. Mitigado
  pelo passa-baixa de 8 Hz na velocidade que alimenta a curva balística.
- **Alcance em movimento lento com a mão perto.** Um arrasto milimétrico
  cai na zona de precisão da curva balística e não varre a tela inteira
  numa passada. É intencional (Fitts: fase balística rápida, homing
  lento), mas é uma mudança de comportamento.
- As proporções anatômicas dos segmentos da palma são aproximadas. A
  mediana absorve o erro e o estimador continua monotônico na distância,
  mas a unidade absoluta depende delas.

### Compatibilidade

- `compute_dpi_multiplier()` e `apply_dpi_to_position()` continuam
  exportadas, marcadas como DEPRECATED. Não são mais chamadas.
- Os três mecanismos legados de borda continuam implementados no
  `CursorController`; só vêm desligados por padrão
  (`CURSOR_EDGE_SNAP_PX = 0`, boost e creep em `False`).
- Sliders e perfis não mudam de significado. `dpi_fixed_multiplier`
  passa a ser explicitamente o **ganho base** da composição.

## Alternativas consideradas

1. **Apenas inverter `compute_dpi_multiplier`.** Corrigiria o sintoma 1
   e nenhum dos outros dois — o cursor continuaria se deslocando com a
   mão parada.
2. **Manter posição absoluta e filtrar a escala mais forte.** Reduz mas
   não elimina o deslocamento com mão parada, e adiciona lag na resposta
   à distância.
3. **Recentragem lenta para conter a deriva do modo relativo.** Violaria
   o invariante "âncora constante ⇒ cursor parado". Rejeitado.
4. **Manter o edge snap com distância menor.** Continua sendo salto por
   definição. Rejeitado.
5. **Assistência de borda por velocidade injetada (primeira versão
   implementada).** Funciona, mas move o cursor com a mão parada e
   precisa de tetos de velocidade e aceleração próprios. Substituída
   pelo modelo de ganho, que não tem dinâmica própria.

## Referências

- Implementação: `core/cursor_motion.py`
- Integração: `core/gesture_detector.py`, `services/virtual_mouse_service.py`
- Testes: `tests/test_cursor_motion.py`, `tests/test_cursor_pipeline.py`
- ADR anterior: `docs/adr/0001-robust-hand-anchor.md`
