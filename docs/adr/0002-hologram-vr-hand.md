# ADR 0002 — Mão do holograma estilo VR com OpenGL do Qt

- **Status**: Accepted
- **Data**: 2026-10-13

## Contexto

O holograma desenhava a mão com ModernGL (mesh 3D) ou QPainter (2D neon).
Problemas medidos:

1. **Pose infiel.** Os landmarks do MediaPipe sao normalizados pela
   largura (x) e pela altura (y) do frame; usar a mesma escala nos dois
   eixos achatava a mão ~0,56× num frame 16:9. A escala vinha do bounding
   box: fechar a mão a fazia crescer.
2. **Invisível no Windows com Smart App Control.** A DLL `glcontext/wgl`
   (dependência do ModernGL) era bloqueada; a janela abria, o contexto GL
   falhava dentro do `initializeGL` e nada aparecia — sem fallback.
3. **Visual.** Cilindros com emendas, translucidez "raio-x" (sem depth
   test), sem fade de antebraço.

## Decisão

Pipeline em três módulos puros/independentes, compartilhados pelos dois
renderers:

```
landmarks -> HandPoseSolver (hand_pose) -> PosedHand
PosedHand -> build_hand_mesh (hand_mesh) -> HandMesh -> HandGLRenderer
PosedHand -> SoftHandRenderer (fallback QPainter, mesma paleta)
```

- `HandPoseSolver`: coordenadas isotrópicas, escala pela palma 3D
  (suavizada), OneEuro 3D por landmark, palma ancorada no cursor com
  offset suavizado até o ponto da pinça, profundidade vista por trás da
  mão, força da pinça.
- OpenGL via `QOpenGLShaderProgram`/`QOpenGLBuffer` do PySide6 (mesmas
  DLLs da janela); `moderngl` removido das dependências.
- Disponibilidade: contexto OpenGL 3.3 core é criado num probe offscreen
  antes de escolher o backend; falhou -> renderer 2D.
- Depth pre-pass + passada de cor com `LEQUAL` e alpha pré-multiplicado:
  translucidez de camada única.

## Consequências

- A mão virtual reproduz aspecto, proporções e gestos da mão real
  (verificado com `scripts/render_hand_preview.py` contra frames reais).
- Custo medido no loop: ~4-10 ms/frame (p50) com o holograma ligado
  (mesh ~3 ms), VSync desligado na janela para não bloquear o loop.
- Sem anéis de clique no estilo VR (feedback = brilho das pontas); o
  estilo `neon` mantém o comportamento antigo.
- Contrapartida: mesh procedural, não um modelo esculpido com skinning;
  fidelidade de silhueta boa, detalhes finos (unhas, rugas) ausentes.

## Alternativas consideradas

1. **Manter ModernGL** — bloqueado pela política em máquinas reais.
2. **SDF raymarching no shader** (união suave de cápsulas) — visual mais
   orgânico, mas custo de GPU por pixel maior e fallback 2D sem paridade.
   Pode voltar como estilo opcional.
3. **Modelo glTF com skinning** — exige asset + rig + solver de rotações
   por osso; adiado.
