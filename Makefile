.DEFAULT_GOAL := help
UV := uv run

.PHONY: help install up up-observability down logs dev worker migrate revision generate dataset seed eval-extraction golden eval-retrieval seed-dataset eval-sql eval-agent evals evals-record evals-baseline test test-unit lint format typecheck check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies and git hooks
	uv sync
	$(UV) pre-commit install

up: ## Start the full stack (runs migrations first)
	docker compose up -d --build

up-observability: ## Start the stack plus Jaeger (set OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 in .env); UI at :16686
	docker compose --profile observability up -d --build

down: ## Stop the stack
	docker compose --profile observability down

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

eval-retrieval: ## Score retrieval on the golden set (needs make up && make seed-dataset); ARGS="--strategy hybrid --rerank"
	$(UV) python -m evals.retrieval_eval $(GOLDEN) $(ARGS)

eval-sql: ## Text-to-SQL execution accuracy (needs a real LLM and the dataset ingested)
	$(UV) python -m evals.sql_eval evals/datasets/sql_golden.jsonl

eval-agent: ## Agent routing, citation validity and (with ARGS=--judge) faithfulness; needs a real LLM
	$(UV) python -m evals.agent_eval evals/datasets/agent_questions.jsonl $(ARGS)

EVALS_DB ?= postgresql+asyncpg://voc:voc@localhost:5432/voc_evals

evals-db:
	@docker compose exec -T postgres createdb -U voc voc_evals 2>/dev/null || true

evals: evals-db ## The CI eval suite against a dedicated database; fails on regression vs evals/baselines.json
	$(UV) python -m evals.ci --database-url $(EVALS_DB)

evals-record: evals-db ## Re-record LLM responses for the CI evals (needs a real LLM_PROVIDER)
	$(UV) python -m evals.ci --database-url $(EVALS_DB) --record

evals-baseline: evals-db ## Accept current eval numbers as the new baseline (commit evals/baselines.json)
	$(UV) python -m evals.ci --database-url $(EVALS_DB) --update-baselines

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
