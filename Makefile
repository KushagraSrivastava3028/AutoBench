# Autobench — the commands you actually type.
#
# Two modes:
#   local   no Docker, no Postgres, no Redis, no API key. `make quickstart`.
#   stack   the real thing, via docker compose. `make dev`.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PY      ?= .venv/Scripts/python.exe
ifeq (,$(wildcard $(PY)))
PY      := .venv/bin/python
endif
ifeq (,$(wildcard $(PY)))
PY      := python3
endif

export PYTHONPATH := src
AB      := $(PY) -m autobench.cli.main
COMPOSE := docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	 | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

.PHONY: venv
venv: ## Create the virtualenv and install everything
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e ".[dev,providers,export]"

.PHONY: quickstart
quickstart: seed ## Zero-infrastructure: seed, then run the reference suite
	$(AB) run --suite core-reference --agent oracle --k 3 --concurrency 4

# ---------------------------------------------------------------------------
# quality
# ---------------------------------------------------------------------------

.PHONY: lint
lint: ## ruff check + format check
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

.PHONY: format
format: ## Apply formatting and autofixes
	$(PY) -m ruff check --fix src tests
	$(PY) -m ruff format src tests

.PHONY: typecheck
typecheck: ## mypy --strict
	$(PY) -m mypy src

.PHONY: test
test: ## Unit tests only (fast)
	$(PY) -m pytest tests/unit -q

.PHONY: test-integration
test-integration: ## Pipeline, queue and API tests
	$(PY) -m pytest tests/integration -q

.PHONY: test-e2e
test-e2e: ## The five user stories, end to end
	$(PY) -m pytest tests/e2e -q

.PHONY: test-all
test-all: ## Everything, with coverage
	$(PY) -m pytest tests -q --cov=autobench --cov-report=term-missing

.PHONY: check
check: lint typecheck test ## What CI runs on every push

# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------

.PHONY: migrate
migrate: ## Apply migrations
	$(PY) -m alembic upgrade head

.PHONY: migrate-down
migrate-down: ## Roll back one revision
	$(PY) -m alembic downgrade -1

.PHONY: migration
migration: ## Autogenerate a revision: make migration m="add x"
	$(PY) -m alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Register the corpus, agents, models and suites
	$(AB) seed

.PHONY: reset
reset: ## Drop local state and start over
	rm -rf .autobench
	$(AB) seed

# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

.PHONY: validate
validate: ## Static gates on every task
	$(AB) validate tasks/
	$(AB) validate tests/fixtures/tasks/ || true

.PHONY: validate-deep
validate-deep: ## Every gate, including oracle/null calibration
	$(AB) validate tasks/ --deep

.PHONY: pin-images
pin-images: ## Rewrite base-image digests from the local daemon
	$(PY) scripts/pin_base_images.py tasks tests/fixtures/tasks

# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

.PHONY: serve
serve: ## Control plane on :8000
	$(AB) serve --reload

.PHONY: worker
worker: ## One worker
	$(AB) worker

.PHONY: ui
ui: ## Next dev server on :3000
	cd frontend && npm run dev

.PHONY: bench
bench: ## Measure the parallel speedup (oracle agent, committed to bench/)
	$(AB) bench-harness --suite tasks --agent oracle \
	    --concurrency 1,2,4,8 --repeats 3 --markdown

.PHONY: types
types: ## Regenerate frontend types from the live OpenAPI schema
	cd frontend && npm run gen:types

# ---------------------------------------------------------------------------
# the docker stack
# ---------------------------------------------------------------------------

.PHONY: dev
dev: ## Bring up the full stack
	$(COMPOSE) up -d --build
	@echo "api      http://localhost:8000/docs"
	@echo "ui       http://localhost:3000"
	@echo "grafana  http://localhost:3001"
	@echo "minio    http://localhost:9001"

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete its volumes
	$(COMPOSE) down -v
	rm -rf .autobench frontend/.next

.PHONY: logs
logs: ## Tail api and worker logs
	$(COMPOSE) logs -f api worker

.PHONY: ps
ps: ## Stack status
	$(COMPOSE) ps

.PHONY: prune
prune: ## Reclaim disk from the image cache and stopped containers
	docker system prune -f --filter "label=dev.autobench.managed=true"
