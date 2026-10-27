.DEFAULT_GOAL := help
UV := uv run

.PHONY: help install up down logs dev migrate revision test test-unit lint format typecheck check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies and git hooks
	uv sync
	$(UV) pre-commit install

up: ## Start the full stack (runs migrations first)
	docker compose up -d --build

down: ## Stop the stack
	docker compose down

logs: ## Tail API logs
	docker compose logs -f api

dev: ## Run the API locally with hot reload (needs postgres + redis: `docker compose up -d postgres redis`)
	$(UV) uvicorn app.main:create_app --factory --reload

migrate: ## Apply migrations to DATABASE_URL
	$(UV) alembic upgrade head

revision: ## Create a migration: make revision m="add conversations"
	$(UV) alembic revision --autogenerate -m "$(m)"

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
