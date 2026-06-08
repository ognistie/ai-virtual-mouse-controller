# ADR 0001 — Ancora robusta multi-landmark para o cursor

- **Status**: Accepted
- **Data**: 2026-06-07
- **Decisor**: @ognistie

## Contexto

O cursor seguia um landmark unico do MediaPipe Hands (`landmark 9` = palma).
Esse design tem dois modos de falha graves:

1. **Oclusao do landmark ancora** — quando a webcam nao enxerga o centro
   da palma (mao de perfil, cantos do frame, dedos cobrindo o centro), o
   MediaPipe preve / extrapola o landmark com baixa confianca. O cursor
   pula erraticamente porque o ponto que ele segue e' uma estimativa ruim.

2. **Sensibilidade desigual nas bordas** — quanto mais proximo da borda
   do frame, mais erratico o landmark predito fica. Usuarios reportavam
   dificuldade de levar o cursor ate cantos da tela / taskbar.

## Decisao

Adotar **ancora robusta ponderada multi-landmark** (`RobustHandAnchor`)
como default do `CURSOR_ANCHOR_LANDMARK` (sentinel `-2`).

A ancora e' computada como soma ponderada dos 21 landmarks:

```
weight_i = w_anatomia(i) × w_in_frame(x_i, y_i) × w_estabilidade(history_i)
ancora   = Σ(weight_i × pos_i) / Σ(weight_i)
```

Componentes:

- **w_anatomia**: tabela fixa baseada em biomecanica. MCPs da palma
  (5, 9, 13, 17) pesam 1.0; fingertips pesam 0.55–0.70; pulso 0.55;
  intermediarios 0.40–0.60.
- **w_in_frame**: taper linear nas bordas do frame (margem 6%). Geometric
  AND em x,y. Landmark cortado pelo enquadramento perde peso suavemente.
- **w_estabilidade**: `1 / (1 + 220 × var)` sobre janela rolante de 8
  frames. Landmark "tremendo" (sintoma de oclusao / chute do MediaPipe)
  e' desvalorizado.
- **Histerese**: queda de confianca >50% em valor absoluto <0.4 produz
  blend 35% com a ultima ancora boa por alguns frames.
- **Extrapolacao por velocidade nas bordas**: quando confianca cai E
  ancora esta a <20% da borda E velocidade aponta pra borda, projetamos
  pela velocidade recente. Resolve "perder a borda".

## Consequencias

### Positivas

- Cursor estavel quando landmarks individuais falham.
- Comportamento gracioso nas bordas — sem saltos.
- Modulo puro (`core/hand_anchor.py`), testavel sem mediapipe.
- Backward compatible: `CURSOR_ANCHOR_LANDMARK = -1` (pinch midpoint) e
  `0..20` (landmark unico) continuam funcionando.

### Negativas

- Cursor segue um ponto "calculado" da mao, nao um landmark fisicamente
  identificavel. Usuario nao consegue mais "saber" qual ponto do dedo e'
  o cursor.
- Custo computacional levemente maior: 21 pesos por frame em vez de 1
  lookup. Medido em ~30µs/frame em hardware modesto — desprezivel.

### Mitigacoes

- Para casos que precisam de ancora previsivel (testes, calibracao),
  modos `-1` (pinch midpoint) e `0..20` (landmark especifico) ficam
  acessiveis via config.

## Alternativas consideradas

1. **Trocar para `landmark 0` (pulso)** — mais estavel mas longe da
   regiao onde gestos acontecem. Rejeitado.
2. **Soma simples de todos os landmarks (centroide)** — sem pesos
   anatomicos, fingertips dominariam. Rejeitado.
3. **Kalman filter na pose inteira** — sobrecarga e' alta e nao resolve
   o problema de bordas. Adiado.
4. **Filtragem temporal mais agressiva no landmark unico** — adicionaria
   lag perceptivel. Rejeitado.

## Referencias

- Implementacao: `core/hand_anchor.py`
- Tests: `tests/test_hand_anchor.py` (TODO)
- Discussao original: conversa de iteracao do projeto (jun/2026)
