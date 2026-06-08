# Contribuindo

Valeu por querer ajudar — esse projeto fica melhor quando mais gente bota a mão. Esse guia é curto: só o essencial pra você abrir um PR sem fricção.

## Setup rápido

Precisa de Python 3.11 ou 3.12 (mediapipe ainda não brincou bem com 3.13+).

```bash
git clone https://github.com/ognistie/ai-virtual-mouse-controller.git
cd ai-virtual-mouse-controller
make dev          # instala tudo + hooks de pre-commit
```

Sem `make`? Sem stress:

```bash
pip install -e ".[dev,hologram]"
pre-commit install
```

## Antes de abrir o PR

Roda isso e garante que nada quebrou:

```bash
make check        # lint + tipos + testes
```

Se passou aqui, vai passar no CI.

## Como escrever o commit

Usamos [Conventional Commits](https://www.conventionalcommits.org/pt-br/). Resumindo:

```
feat: nova feature
fix: bug fix
docs: só documentação
refactor: reorganiza sem mudar comportamento
perf: otimização
test: só testes
chore: manutenção (deps, config)
```

Exemplo bom: `fix(cursor): trava cursor durante click pra anular drift do pinch`

Exemplo ruim: `aaa`, `atualizei umas coisas`, `update`

## Bug? Sugestão?

- 🐛 [Abre issue de bug](https://github.com/ognistie/ai-virtual-mouse-controller/issues/new?template=bug_report.yml) — o template guia o que incluir
- 💡 [Abre issue de feature](https://github.com/ognistie/ai-virtual-mouse-controller/issues/new?template=feature_request.yml)
- 💬 Pra ideias maiores ou conversa aberta, prefira [Discussions](https://github.com/ognistie/ai-virtual-mouse-controller/discussions) antes do issue

## Antes de mais nada

Vulnerabilidade de segurança? **Não abre issue público.** Veja [SECURITY.md](SECURITY.md).

Conduta na comunidade? [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Versão TL;DR: seja gente boa.

---

Qualquer dúvida que esse guia não cobrir, abre uma Discussion. Melhor perguntar do que assumir.
