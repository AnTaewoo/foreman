.PHONY: install lint typecheck test test-integration check run-api run-worker migrate docker-up docker-down

UV ?= uv
COMPOSE ?= docker compose

install:
	$(UV) sync --all-groups
	$(UV) run pre-commit install

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck:
	$(UV) run mypy control_plane agents github_adapter worker

test:
	$(UV) run pytest -q

test-integration:
	$(UV) run pytest -q tests/integration -m integration -o addopts=""

check: lint typecheck test

run-api:
	$(UV) run uvicorn control_plane.api.app:app --factory --host 0.0.0.0 --port $${API_PORT:-8000} --reload

run-worker:
	$(UV) run python -m worker

migrate:
	$(UV) run alembic upgrade head

docker-up:
	$(COMPOSE) up -d --wait

docker-down:
	$(COMPOSE) down
