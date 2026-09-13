"""FastAPI 앱 팩토리. MVP 1 라우터는 P5.1에서 붙는다. 지금은 liveness `GET /health`만."""

from __future__ import annotations

from fastapi import FastAPI

from control_plane.config import Settings, get_settings
from control_plane.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """설정을 주입받아 앱을 만든다. 테스트는 `Settings(_env_file=None)`을 넘긴다."""
    settings = settings or get_settings()
    configure_logging(settings)

    application = FastAPI(title="foreman control plane", version="0.1.0")
    application.state.settings = settings

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


def app() -> FastAPI:
    """`uvicorn control_plane.api.app:app --factory`용 팩토리."""
    return create_app()
