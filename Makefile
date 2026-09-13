.PHONY: install lint typecheck test test-integration check

UV ?= uv

install:
	$(UV) sync --all-groups

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
