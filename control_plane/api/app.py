"""FastAPI 앱 팩토리 (설계 §13). 라우터: projects / goals / tasks / events (+ `/health`).

테스트는 `create_app(Settings(_env_file=None), factory=…, redis=…)`로 sqlite + 진짜 Redis 주입.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.api import approvals, events, goals, projects, stream, tasks
from control_plane.api.demo_guard import RateLimitMiddleware
from control_plane.api.deps import AppState, LlmProbe, build_state
from control_plane.api.idempotency import IdempotencyMiddleware
from control_plane.config import Settings, get_settings
from control_plane.logging import configure_logging
from control_plane.orchestrator.runner import GoalRunner
from github_adapter import ClientRegistry

log = structlog.get_logger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


async def fetch_install_url(settings: Any) -> str | None:  # Any: Settings 또는 테스트 대역
    from github_adapter.app_check import app_install_url
    from github_adapter.client import GITHUB_API_BASE_URL

    async with httpx.AsyncClient(base_url=GITHUB_API_BASE_URL, timeout=10) as http:
        return await app_install_url(settings, http)


class InstallUrl:
    """P9.11: 공개 App 설치 링크를 한 번만 받아 둔다. Dry면 호출 없이 None, 실패는 재시도."""

    def __init__(self, *, dry_run: bool) -> None:
        self._dry_run = dry_run
        self._value: str | None = None

    async def get(self, settings: Any) -> str | None:  # Any: Settings 또는 테스트 대역
        if self._dry_run:
            return None
        if self._value is None:
            try:
                self._value = await fetch_install_url(settings)
            except httpx.HTTPError:
                log.warning("demo.install_url_failed")
        return self._value


def create_app(
    settings: Settings | None = None,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
    redis: Redis | None = None,
    runner: GoalRunner | None = None,
    llm_probe: LlmProbe | None = None,
) -> FastAPI:
    """설정을 주입받아 앱을 만든다. factory/redis를 안 주면 설정으로 만들고 shutdown에서 닫는다.

    ``runner``(P5.2)를 주면 ``POST /goals``가 Orchestrator를 백그라운드로 돌리고
    ``POST /webhooks/github``의 ``/approve``·``/reject``가 재개한다.
    """
    settings = settings or get_settings()
    configure_logging(settings)
    state = build_state(settings, factory=factory, redis=redis)
    if llm_probe is None:  # 기본: 실제 1콜 프로브 (테스트는 가짜를 주입)
        from agents.llm import probe_profile

        async def llm_probe(profile: str) -> None:
            await probe_profile(settings, profile)

    state.llm_probe = llm_probe
    if runner is not None:
        state.runner = runner
        state.on_goal_created = runner.start
        if not settings.github_webhook_secret.get_secret_value():
            log.warning(
                "webhook.secret_missing", hint="POST /webhooks/github will answer 503 (D-50)"
            )

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
    if settings.demo_mode:  # P9.3 (D-52)
        application.add_middleware(
            RateLimitMiddleware, per_minute=settings.demo_post_per_ip_per_min
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/llm")
    async def llm_info() -> dict[str, object]:
        """D-57: 콘솔의 LLM 선택 목록 (api_key는 포함하지 않는다)."""
        from agents.llm import available_profiles, default_profile

        return {"default": default_profile(settings), "profiles": available_profiles(settings)}

    install_url = InstallUrl(dry_run=settings.dry_run)

    @application.get("/demo")
    async def demo_info() -> dict[str, object]:
        """콘솔이 읽는 데모 설정 (토큰은 절대 포함하지 않는다)."""
        return {
            "demo_mode": settings.demo_mode,
            "user_id": settings.demo_user_id,
            "max_running_goals": settings.demo_max_running_goals,
            "goals_per_hour": settings.demo_goals_per_hour,
            "install_url": await install_url.get(settings),  # P9.11 공개 App 설치 링크
        }

    for r in (projects.router, goals.router, tasks.router, events.router, stream.router):
        application.include_router(r)

    # P9.2 (D-52): 데모 콘솔 — MVP 1 임시 정적 1페이지 (설계 §11.0). Mission Control(MVP 5) 아님
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", include_in_schema=False)
    async def console() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @application.middleware("http")
    async def _no_cache_static(request: Request, call_next: Any) -> Any:
        # 콘솔 JS/CSS는 배포마다 바뀐다 — 옛 파일을 쓰지 않게 매번 재검증(ETag) (P9 사용자 보고)
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

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
    from agents.llm.router import ProfileRouter
    from control_plane.github_routing import RepoRouter
    from control_plane.repo_cache import RepoCache

    # P9.9: 프로젝트별 installation — repo로 client·토큰을 고른다 (DRY_RUN이면 Dry 하나)
    router = RepoRouter(state.factory, ClientRegistry(settings))
    repo_cache = RepoCache(
        Path(settings.repo_root),
        token_getter=router.token_getter,  # D-41
        dry_run=settings.dry_run,  # D-48
    )
    return GoalRunner(
        factory=state.factory,
        bus=state.bus,
        provider=ProfileRouter(settings),  # D-57: Goal의 프로파일로 위임
        github=router.github,
        discussions=router.discussions,
        model=None,  # 프로파일의 provider가 모델을 정한다 (D-57)
        repo_path_for=repo_cache.ensure,  # D-38
        token_provider=router if router.token_getter is not None else None,
        repo_cache=repo_cache,
    )


def app() -> FastAPI:
    """`uvicorn control_plane.api.app:app --factory`용 팩토리 (실행기 포함)."""
    settings = get_settings()
    state = build_state(settings)
    runner = build_runner(settings, state)
    return create_app(settings, factory=state.factory, redis=state.redis, runner=runner)
