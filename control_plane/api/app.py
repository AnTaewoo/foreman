"""FastAPI 앱 팩토리 (설계 §13). 라우터: projects / goals / tasks / events (+ `/health`).

테스트는 `create_app(Settings(_env_file=None), factory=…, redis=…)`로 sqlite + 진짜 Redis 주입.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.api import events, goals, projects, tasks
from control_plane.api.deps import AppState, build_state
from control_plane.api.idempotency import IdempotencyMiddleware
from control_plane.config import Settings, get_settings
from control_plane.logging import configure_logging


def create_app(
    settings: Settings | None = None,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
    redis: Redis | None = None,
) -> FastAPI:
    """설정을 주입받아 앱을 만든다. factory/redis를 안 주면 설정으로 만들고 shutdown에서 닫는다."""
    settings = settings or get_settings()
    configure_logging(settings)
    state = build_state(settings, factory=factory, redis=redis)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await _close(state)

    application = FastAPI(title="foreman control plane", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.ctx = state
    application.add_middleware(IdempotencyMiddleware)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for r in (projects.router, goals.router, tasks.router, events.router):
        application.include_router(r)
    return application


async def _close(state: AppState) -> None:
    if state.owns_redis:
        await state.redis.aclose()
    if state.engine is not None:
        await state.engine.dispose()


def app() -> FastAPI:
    """`uvicorn control_plane.api.app:app --factory`용 팩토리."""
    return create_app()
