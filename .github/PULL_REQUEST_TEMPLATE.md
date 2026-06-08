<!--
Antes de submeter:
- Leia CONTRIBUTING.md
- Verifique que `ruff check .`, `ruff format --check .` e `pytest tests/` passam
- Atualize CHANGELOG.md na secao [Unreleased]
-->

## Resumo

<!-- 1-3 linhas: o que muda e por que -->

## Tipo de mudanca

- [ ] feat (nova feature)
- [ ] fix (bug fix)
- [ ] docs (so documentacao)
- [ ] refactor (sem mudanca de comportamento)
- [ ] perf (otimizacao)
- [ ] test (so testes)
- [ ] build/ci/chore

## Como testei

<!-- Comandos rodados, hardware testado, fluxos manuais cobertos -->

```bash
pytest tests/ -v
ruff check .
```

## Checklist

- [ ] Codigo segue o estilo do projeto (`ruff check . --fix` e `ruff format .`)
- [ ] Testes novos cobrindo a logica adicionada/alterada
- [ ] Mudancas documentadas em `CHANGELOG.md` ([Unreleased])
- [ ] Commits seguem [Conventional Commits](https://www.conventionalcommits.org/pt-br/)
- [ ] Sem `print()` debug perdido / sem TODOs sem owner

## Breaking changes?

<!-- Se sim, explique: como migrar, qual a versao impactada -->

- [ ] Sim — descrito acima
- [ ] Nao

## Issue relacionado

<!-- Closes #N -->

## Screenshots / GIFs (se UI)

<!-- Anexe demos visuais quando mexer em holograma / overlay -->
