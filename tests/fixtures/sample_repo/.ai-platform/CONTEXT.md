# CONTEXT.md

## Architecture
- Flask app factory in `app.main:create_app`; in-memory `UserStore`.

## Conventions
- pytest via `pytest -q`; keep functions small; type hints required.

## Pitfalls
- Do not add a database; this fixture must stay dependency-light.
