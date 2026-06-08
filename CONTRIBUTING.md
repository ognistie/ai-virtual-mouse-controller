# Contribuindo com o AI Virtual Mouse Controller

Obrigado pelo interesse em contribuir! Este guia cobre o necessario para
abrir um PR que va ser aceito sem retrabalho.

## Indice

- [Setup do ambiente](#setup-do-ambiente)
- [Rodando os testes](#rodando-os-testes)
- [Padroes de codigo](#padroes-de-codigo)
- [Convencao de commits](#convencao-de-commits)
- [Workflow de PR](#workflow-de-pr)
- [Reportando bugs](#reportando-bugs)
- [Sugerindo features](#sugerindo-features)

## Setup do ambiente

Requer **Python 3.11 ou 3.12** (mediapipe ainda nao publica wheel pra 3.13+
estavel).

```bash
git clone https://github.com/ognistie/ai-virtual-mouse-controller.git
cd ai-virtual-mouse-controller

# venv
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# ou
.venv\Scripts\activate  # Windows

# Install em modo editavel com tooling de dev
pip install -e ".[dev,hologram]"

# Pre-commit hooks (lint + format + type-check em cada commit)
pre-commit install
pre-commit install --hook-type commit-msg
```

## Rodando os testes

```bash
# Suite completa
pytest tests/

# Com coverage
pytest tests/ --cov=core --cov=services --cov-report=term-missing

# So testes rapidos (skip integration)
pytest tests/ -m "not integration"
```

Os testes que dependem de `mediapipe` usam `pytest.importorskip`. Se voce
nao tiver mediapipe instalado, eles sao skippados — nao falham.

## Padroes de codigo

### Format + lint
Tudo via **ruff** (substitui black + isort + flake8):

```bash
ruff check .            # lint
ruff format .           # format
ruff check . --fix      # auto-fix issues
```

Pre-commit roda automaticamente. Se quiser verificar antes de commitar:
```bash
pre-commit run --all-files
```

### Type hints
Type hints sao **gradualmente obrigatorios**:
- **Modulos novos**: 100% tipados, validados por mypy strict.
- **Modulos legacy** (god classes em refactor): tipagem incremental ok.

```bash
mypy core/hand_anchor.py core/finger_posture.py  # modulos puros sao strict
```

### Docstrings
Estilo das docstrings ja estabelecido no projeto: PT-BR, explicando o
**porque** (decisao de design), nao so o **o que**. Ver `core/hand_anchor.py`
como referencia.

### Arquitetura
- **Modulos puros** (`core/hand_anchor`, `core/finger_posture`, `core/smoothing`,
  `core/click_burst`): sem dependencia de Qt / cv2 / mediapipe; 100% testaveis
  isoladamente.
- **Modulos com dependencias externas**: soft-fail com fallback gracioso.
- **Services**: orquestracao de modulos puros; sem logica de dominio.

### Single Responsibility
Antes de adicionar codigo novo em arquivo > 500 linhas, considere criar
modulo novo. Os god-files atuais (`gesture_detector.py`, `hologram_*.py`,
`hand_mesh.py`) sao divida tecnica conhecida — nao expanda.

## Convencao de commits

Usamos **[Conventional Commits](https://www.conventionalcommits.org/pt-br/)**.
Formato:

```
<tipo>(<escopo opcional>): <descricao curta>

<corpo opcional explicando o porque>

<rodape opcional: BREAKING CHANGE, refs, issues>
```

Tipos aceitos:

| Tipo | Quando usar |
|---|---|
| `feat` | Nova feature visivel para o usuario |
| `fix` | Bug fix |
| `docs` | So documentacao |
| `style` | Format / whitespace / sem mudanca de comportamento |
| `refactor` | Reorganizar codigo sem mudar comportamento |
| `perf` | Otimizacao de performance |
| `test` | Adicionar / corrigir testes |
| `build` | Build system, deps |
| `ci` | GitHub Actions, scripts de CI |
| `chore` | Manutencao geral (e.g. atualizar lockfile) |
| `revert` | Reverter commit anterior |

Exemplos:

```
feat(anchor): adiciona extrapolacao por velocidade nas bordas
fix(cursor): trava cursor no pixel durante click pra anular drift do pinch
perf(hologram): MSAA 4x -> 2x corta custo de fragment em ~50%
docs(readme): atualiza tabela de troubleshooting
```

Pre-commit verifica formato via `conventional-pre-commit` — commit invalido e bloqueado.

## Workflow de PR

1. **Branch a partir de `main`**:
   ```bash
   git checkout -b feat/nome-da-feature
   ```

2. **Commit em incrementos pequenos e revisaveis.** PR gigante e' rejeitado.

3. **Suba testes** para qualquer logica nova. Coverage minimo configurado em
   `pyproject.toml` (`fail_under = 60` por enquanto).

4. **Rode o CI localmente** antes de abrir o PR:
   ```bash
   ruff check . && ruff format --check . && pytest tests/
   ```

5. **Atualize o `CHANGELOG.md`** na secao `[Unreleased]` com sua mudanca.

6. **Abra o PR** preenchendo o template. Marque review explicita se a
   mudanca toca arquitetura ou breaking change.

7. **Squash + merge** e padrao. Mensagem final segue Conventional Commits.

## Reportando bugs

Use o [template de bug report](.github/ISSUE_TEMPLATE/bug_report.yml).
Sempre inclua:

- Versao do projeto (`git rev-parse HEAD` ou tag)
- Versao do Python (`python --version`)
- Sistema operacional + versao
- Modelo da webcam, se relevante
- Log com `LOG_LEVEL=DEBUG`
- Passos minimos para reproduzir

## Sugerindo features

Use o [template de feature request](.github/ISSUE_TEMPLATE/feature_request.yml).
Antes, busque em [issues abertas](https://github.com/ognistie/ai-virtual-mouse-controller/issues)
para evitar duplicata.

Para mudancas grandes (novo backend, novo paradigma de gesto, etc), prefira
abrir uma [Discussion](https://github.com/ognistie/ai-virtual-mouse-controller/discussions)
antes do PR para alinhar design.

## Codigo de conduta

Todos os contribuidores seguem o [Codigo de Conduta](CODE_OF_CONDUCT.md).

## Reportando vulnerabilidades

**Nao abra issue publico**. Veja [SECURITY.md](SECURITY.md).
