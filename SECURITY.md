# Politica de Seguranca

## Versoes suportadas

| Versao | Suporte |
|--------|---------|
| 6.9.x  | ✅ Patches de seguranca |
| < 6.9  | ❌ Sem suporte |

Mantemos apenas a serie minor mais recente. Atualize para a versao
suportada antes de reportar uma vulnerabilidade em versoes antigas.

## Superficie de risco

O AI Virtual Mouse Controller opera com privilegios elevados em sentido
funcional (mesmo sem rodar como admin):

| Recurso | Risco |
|---|---|
| **Webcam** | Captura continua do video da camera frontal do usuario. Frames nao sao persistidos em disco em condicoes normais. |
| **Cursor do sistema** | Movimentacao automatica via PyAutoGUI. Capaz de clicar em qualquer elemento visivel. |
| **Sistema de arquivos** | Apenas leitura de `config.py` e logs em stdout/stderr. |
| **Rede** | Nenhuma comunicacao de rede. Sem telemetria. Sem update check. |
| **Hologram overlay (opcional)** | Cria janela Qt fullscreen com click-through; substitui cursor de sistema via Win32 `SetSystemCursor`. |

Vulnerabilidades nessa superficie sao especialmente sensiveis.

## Reportando uma vulnerabilidade

**Por favor NAO abra issue publico** para vulnerabilidades de seguranca.

Use o canal privado de Security Advisories do GitHub:

🔒 [github.com/ognistie/ai-virtual-mouse-controller/security/advisories/new](https://github.com/ognistie/ai-virtual-mouse-controller/security/advisories/new)

Ao reportar, inclua:

1. Descricao da vulnerabilidade
2. Passos para reproduzir (POC se possivel)
3. Impacto potencial (escalacao, exfiltrating, denial of service)
4. Versao afetada (commit hash ou tag)
5. Ambiente (OS, Python version, dependencias)
6. Sua sugestao de mitigacao, se houver

## Processo de resposta

- **Confirmacao**: ate 72h apos receber o report.
- **Investigacao + fix**: depende da severidade (CVSS-like):
  - Critica: 7 dias
  - Alta: 14 dias
  - Media: 30 dias
  - Baixa: proxima minor release
- **Disclosure coordenada**: publicamos o advisory apos o fix estar disponivel.
- **Credito**: dado a quem reportou (a menos que prefira anonimato).

## O que nao classificamos como vulnerabilidade

- Falsos positivos / negativos em deteccao de gestos (e' bug de UX, abra issue normal).
- Performance degradada em hardware antigo.
- Comportamento esperado de PyAutoGUI / MediaPipe / OpenCV (reporte upstream).
- Privacy de webcam: o projeto e' local-first, frames nao saem da maquina. Caso voce ache uma rota de exfiltracao, ai sim e' vuln.

## Hardening recomendado para o usuario

- Rode em ambiente nao-admin sempre que possivel.
- Use `LOG_LEVEL=INFO` (default) — `DEBUG` pode logar dados visualmente sensiveis em raros casos.
- Desligue o overlay holografico quando nao estiver usando (`H` para toggle) — minimiza a superficie de Win32 hooks.
- Mantenha mediapipe e opencv atualizados (Dependabot abre PRs automaticos).

## Reconhecimentos

Lista de pesquisadores que reportaram vulnerabilidades ficara mantida aqui
apos o primeiro report aceito.
