.PHONY: install lint typecheck test test-integration check run-api run-control-plane run-worker run-webhook-tunnel migrate docker-up docker-down

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

run-control-plane:
	$(UV) run python -m control_plane

run-webhook-tunnel:
	@test -n "$$SMEE_URL" || (echo "SMEE_URL=https://smee.io/<channel> 필요" && exit 64)
	npx --yes smee-client --url $$SMEE_URL --target http://localhost:$${API_PORT:-8000}/webhooks/github

run-worker:
	$(UV) run python -m worker

migrate:
	$(UV) run alembic upgrade head

docker-up:
	$(COMPOSE) up -d --wait

docker-down:
	$(COMPOSE) down
