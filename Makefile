.DEFAULT_GOAL := help
UV := uv run

.PHONY: help install up down logs dev worker migrate revision generate dataset seed eval-extraction golden eval-retrieval seed-dataset test test-unit lint format typecheck check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies and git hooks
	uv sync
	$(UV) pre-commit install

up: ## Start the full stack (runs migrations first)
	docker compose up -d --build

down: ## Stop the stack
	docker compose down

logs: ## Tail API and worker logs
	docker compose logs -f api worker

dev: ## Run the API locally with hot reload (needs postgres + redis: `docker compose up -d postgres redis`)
	$(UV) uvicorn app.main:create_app --factory --reload

worker: ## Run the ingestion worker locally
	$(UV) arq app.workers.settings.WorkerSettings

migrate: ## Apply migrations to DATABASE_URL
	$(UV) alembic upgrade head

revision: ## Create a migration: make revision m="add conversations"
	$(UV) alembic revision --autogenerate -m "$(m)"

INDUSTRY ?= telecom
N ?= 500
SEED ?= 42
RENDERER ?= template
DATASET_DIR := synthetic/datasets/telecom-seed42-n500

generate: ## Generate a synthetic dataset: make generate INDUSTRY=banking N=1000 SEED=7 [RENDERER=llm]
	$(UV) python -m synthetic.generator --industry $(INDUSTRY) --n $(N) --seed $(SEED) \
		--renderer $(RENDERER) --out data/$(INDUSTRY)-seed$(SEED)-n$(N)

dataset: ## Regenerate the committed dataset (run after changing scenarios.yaml or the generator)
	$(UV) python -m synthetic.generator --industry telecom --n 500 --seed 42 --out $(DATASET_DIR)

seed: ## Generate a dataset and ingest it into the running API: make seed INDUSTRY=telecom N=500
	$(MAKE) generate INDUSTRY=$(INDUSTRY) N=$(N) SEED=$(SEED) RENDERER=$(RENDERER)
	$(UV) python -m scripts.ingest data/$(INDUSTRY)-seed$(SEED)-n$(N)

LIMIT ?= 200
eval-extraction: ## Score extraction against ground truth with the configured LLM: make eval-extraction LIMIT=200
	$(UV) python -m evals.extraction_eval $(DATASET_DIR) --limit $(LIMIT)

GOLDEN := evals/datasets/golden-telecom-seed42-n500.jsonl

golden: ## Rebuild the retrieval golden set from the committed dataset's ground truth
	$(UV) python -m evals.build_golden $(DATASET_DIR) --out $(GOLDEN)

eval-retrieval: ## Score retrieval on the golden set (dataset must be ingested: make up && make seed-dataset)
	$(UV) python -m evals.retrieval_eval $(GOLDEN)

seed-dataset: ## Ingest the committed dataset into the running API
	$(UV) python -m scripts.ingest $(DATASET_DIR)

test: ## Run all tests (integration tests use Testcontainers)
	$(UV) pytest

test-unit: ## Run unit tests only (no Docker needed)
	$(UV) pytest tests/unit

lint: ## Lint and check formatting
	$(UV) ruff check .
	$(UV) ruff format --check .

format: ## Auto-fix lint and formatting
	$(UV) ruff check --fix .
	$(UV) ruff format .

typecheck: ## Static type checking
	$(UV) mypy

check: lint typecheck test ## Everything CI runs
