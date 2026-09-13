"""FastAPI 앱 팩토리 (설계 §13). 라우터: projects / goals / tasks / events (+ `/health`).

테스트는 `create_app(Settings(_env_file=None), factory=…, redis=…)`로 sqlite + 진짜 Redis 주입.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.api import approvals, events, goals, projects, stream, tasks
from control_plane.api.deps import AppState, build_state
from control_plane.api.idempotency import IdempotencyMiddleware
from control_plane.config import Settings, get_settings
from control_plane.logging import configure_logging
from control_plane.orchestrator.runner import GoalRunner


def create_app(
    settings: Settings | None = None,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
    redis: Redis | None = None,
    runner: GoalRunner | None = None,
) -> FastAPI:
    """설정을 주입받아 앱을 만든다. factory/redis를 안 주면 설정으로 만들고 shutdown에서 닫는다.

    ``runner``(P5.2)를 주면 ``POST /goals``가 Orchestrator를 백그라운드로 돌리고
    ``POST /webhooks/github``의 ``/approve``·``/reject``가 재개한다.
    """
    settings = settings or get_settings()
    configure_logging(settings)
    state = build_state(settings, factory=factory, redis=redis)
    if runner is not None:
        state.runner = runner
        state.on_goal_created = runner.start

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if state.runner is not None:
            await state.runner.startup(settings.database_url)
        yield
        if state.runner is not None:
            await state.runner.shutdown()
        await _close(state)

    application = FastAPI(title="foreman control plane", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.ctx = state
    application.add_middleware(IdempotencyMiddleware)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for r in (projects.router, goals.router, tasks.router, events.router, stream.router):
        application.include_router(r)
    if runner is not None:
        application.include_router(approvals.build_webhook(state, runner))
    return application


async def _close(state: AppState) -> None:
    if state.owns_redis:
        await state.redis.aclose()
    if state.engine is not None:
        await state.engine.dispose()


def build_runner(settings: Settings, state: AppState) -> GoalRunner:
    """설정으로 실 실행기: provider(D-33), GitHub(DRY_RUN이면 Dry). 체크포인터는 startup."""
    from agents.llm import get_provider
    from github_adapter import get_discussions_client, get_github_client

    return GoalRunner(
        factory=state.factory,
        bus=state.bus,
        provider=get_provider(settings),
        github=get_github_client(settings),
        discussions=get_discussions_client(settings),
        model=settings.llm_model if settings.llm_provider != "anthropic" else None,
    )


def app() -> FastAPI:
    """`uvicorn control_plane.api.app:app --factory`용 팩토리 (실행기 포함)."""
    settings = get_settings()
    state = build_state(settings)
    runner = build_runner(settings, state)
    return create_app(settings, factory=state.factory, redis=state.redis, runner=runner)
