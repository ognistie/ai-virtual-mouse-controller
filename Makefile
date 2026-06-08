# ============================================================================
# Makefile — atalhos de desenvolvimento
# ============================================================================
# Uso: make <target>. Sem args, lista os targets disponiveis.
# ============================================================================

.PHONY: help install dev test test-fast lint format type-check check clean run pre-commit

help:  ## Lista targets disponiveis
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Instala deps de runtime
	pip install -e .

dev:  ## Instala deps de runtime + dev + hologram
	pip install -e ".[dev,hologram]"
	pre-commit install
	pre-commit install --hook-type commit-msg

test:  ## Roda todos os testes
	pytest tests/ -v

test-fast:  ## Roda so testes rapidos
	pytest tests/ -v -m "not slow and not integration"

test-cov:  ## Roda testes com coverage
	pytest tests/ --cov=core --cov=services --cov-report=term-missing --cov-report=html

lint:  ## Roda ruff check
	ruff check .

format:  ## Aplica ruff format
	ruff format .
	ruff check . --fix

type-check:  ## Roda mypy nos modulos puros que passam limpo
	mypy core/hand_anchor.py core/finger_posture.py \
	     core/click_burst.py core/utils.py core/perf_telemetry.py

check: lint type-check test  ## Roda tudo: lint + types + tests

pre-commit:  ## Roda pre-commit em todos os arquivos
	pre-commit run --all-files

clean:  ## Limpa caches e build artifacts
	rm -rf build dist *.egg-info .pytest_cache .coverage htmlcov coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +

run:  ## Roda a aplicacao
	python main.py
