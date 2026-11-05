# ADR 0003 — Visão em thread própria e filtro do que não é mão

- **Status**: Accepted
- **Data**: 2026-11-05

## Contexto

O modo duas mãos e o uso real expuseram problemas medidos com
`scripts/eval_latency.py` e gravações de uso:

1. **Tela atrasando o cursor.** Câmera, MediaPipe, gestos, cursor, holograma e
   preview rodavam em sequência. Com o holograma e a CPU ocupada, o loop caía
   para ~16 FPS e metade dos cliques do vídeo de teste se perdia.
2. **Detecções falsas.** Com `num_hands=2`, surge uma "segunda mão" pequena sobre
   os dedos da real. Fechar a mão aproxima o polegar do indicador, o que virava
   clique. Com a mão saindo da imagem, os pontos podem cair no rosto, com confiança
   de até 0,98.
3. **Mão 3D irregular.** Um timer próprio, fora de fase com a câmera, descartava
   poses: 21 desenhos/s com poses a 25/s, intervalos de até 76 ms.

## Decisão

```
thread de visão:  câmera -> MediaPipe -> HandGate -> gestos -> cursor
thread principal: resultado mais novo -> holograma, preview, painel, teclado
```

- **Visão em thread própria** (`VISION_THREAD_ENABLED`). A thread principal fica
  com tudo que é Qt/OpenCV de janela e pula resultados antigos. Um `RLock`
  protege rastreador e gestos de quem troca de modo. O caminho em sequência
  continua disponível em `_tick()`.
- **`HandGate`** descarta mão repetida ou contida em outra e exige persistência
  antes de a mão agir.
- **Mão 3D desenhada por pose nova**, sem timer: nenhuma pose perdida, intervalos
  regulares e nenhum gasto com a mão parada.
- **Postura na pinça** (`PINCH_OPEN_FINGERS_MIN`): pinça nova exige os outros
  dedos abertos. Quem já segura a pinça não é afetado.
- **Windows 11**: o processo sai da economia de energia (só ele, enquanto roda) e
  o OpenCV usa um thread, sem disputar núcleo com o MediaPipe.

## Alternativas descartadas

- **Cobrir o rosto na imagem antes do MediaPipe.** Zerou as mãos falsas no rosto,
  mas é detector a mais a cada frame e pode cortar a mão real perto da cabeça, onde
  o uso real acontece. O efeito colateral é que, com a mão real fora da imagem,
  landmarks podem aparecer no rosto; quando ela volta, saem em até 1 s.
- **Subir os limiares do MediaPipe.** Os falsos vêm com confiança alta; não resolve.
- **Recriar o rastreamento quando o que é seguido fica parado.** Em tempo real, em
  5 cenas, a mão real voltou a ser reconhecida em até 1 s com ou sem isso, e o ganho
  foi marginal. Custaria CPU com a mão parada e mais uma thread.
- **Prioridade alta do processo.** Ganho menor que sair da economia de energia e
  pior convivência com os outros programas.

## Consequências

- Atributos simples (ajustes do painel) são escritos pela thread principal e lidos
  pela visão sem lock: valores atômicos em CPython, no pior caso valem no próximo
  frame.
- `num_hands=2` com uma só mão em cena roda o detector de palma em todo frame: é
  o caso mais caro. Por isso o modo duas mãos é opcional.
