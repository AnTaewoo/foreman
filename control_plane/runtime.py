"""상주 control plane (P6.1, 리뷰 A1): relay + projection + scheduler + retry를 한 프로세스에.

- 한 consumer group ``control-plane``에서 delivery마다 projection.handle → scheduler.handle 순.
  (그룹을 나누면 scheduler가 ``task.created``를 projection보다 먼저 받아 ``ready``를 못 본다.)
  DryMerger(dry_run일 때, D-36)와 P6.7의 PrOpener도 이 체인 뒤에 붙는다.
- retry 루프: ``events:*:retry`` 키를 SCAN 해 모든 project의 기한 지난 재시도를 적용한다(D-30).
- launcher는 설정(D-15): ``docker`` → DockerCliLauncher(호스트 Redis는 host.docker.internal)
  / ``inprocess`` → InProcessLauncher
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.llm.pricing import Prices
from control_plane.config import Settings
from control_plane.dry_merge import DryMerger
from control_plane.events.bus import RETRY_SUFFIX, STREAM_PREFIX, Delivery, EventBus
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.github_routing import RepoRouter
from control_plane.pr_opener import GitPusher, PrOpener
from control_plane.repo_cache import RepoCache
from control_plane.scheduler.launcher import DockerCliLauncher, InProcessLauncher, WorkerLauncher
from control_plane.scheduler.scheduler import Scheduler
from control_plane.store import session as sess
from github_adapter import ClientRegistry
from github_adapter.protocol import GitHubClient

log = structlog.get_logger(__name__)

Handler = Callable[[Delivery], Awaitable[None]]
GROUP = "control-plane"


def host_url(url: str) -> str:
    """컨테이너에서 호스트 서비스로: localhost/127.0.0.1 → host.docker.internal."""
    return url.replace("://localhost", "://host.docker.internal").replace(
        "://127.0.0.1", "://host.docker.internal"
    )


def worker_llm_env(settings: Settings, profile: str | None = None) -> dict[str, str]:
    """워커의 LLM 설정 (P4.4; D-57 프로파일). provider 키만 — GitHub 토큰은 없다(§12)."""
    from agents.llm import default_profile, profile_env

    cfg = profile_env(settings, profile or default_profile(settings))
    env: dict[str, str] = {"WORKER_LLM_PROVIDER": cfg["provider"], "WORKER_LLM_MODEL": cfg["model"]}
    if cfg["provider"] == "openai_compat":
        env["WORKER_LLM_BASE_URL"] = host_url(cfg["base_url"])  # localhost만 host.docker.internal로
        env["WORKER_LLM_TOKEN_PARAM"] = str(cfg["token_param"])  # OpenAI 프로브 진단 #1
        if cfg["max_tokens"]:
            env["WORKER_LLM_MAX_TOKENS"] = str(cfg["max_tokens"])  # 진단 #3
    env["WORKER_LLM_API_KEY"] = cfg["api_key"]
    env.update(prices_of(settings).to_env())  # D-39
    return env


def prices_of(settings: Settings) -> Prices:
    return Prices(settings.llm_price_in_per_mtok, settings.llm_price_out_per_mtok)


def build_launcher(
    settings: Settings, redis: Redis, *, mounts: list[tuple[str, str]] | None = None
) -> WorkerLauncher:
    if settings.worker_launcher == "docker":
        root_path = Path(settings.repo_root).resolve()
        # 없는 경로를 -v로 넘기면 Docker가 root 소유로 만든다 → 미리 호스트 사용자 소유로 (PC-8)
        root_path.mkdir(parents=True, exist_ok=True)
        root = str(root_path)
        return DockerCliLauncher(
            image=settings.worker_image,
            redis_url=host_url(settings.redis_url),
            worker_env=lambda profile: worker_llm_env(settings, profile),  # D-57
            mounts=mounts if mounts is not None else [(root, root)],  # D-38: repo_root 마운트
        )
    from agents.llm import get_provider
    from agents.llm.base import ModelProvider

    def provider_for(profile: str | None = None) -> ModelProvider:  # D-57
        return get_provider(settings, profile=profile)

    return InProcessLauncher(
        redis,
        provider_factory=provider_for,
        workdir=Path(tempfile.gettempdir()) / "foreman-inprocess",
        model=None,  # 프로파일의 provider가 자기 모델을 쓴다
        prices=prices_of(settings),
    )


class Runtime:
    def __init__(
        self,
        settings: Settings,
        factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        *,
        launcher: WorkerLauncher | None = None,
        retry_interval: float = 1.0,
        reap_interval: float = 5.0,
        clock: Callable[[], datetime] | None = None,
        consumer: str = "cp1",
        block_ms: int = 200,
        github: GitHubClient | None = None,
        token_provider: Any = None,  # Any: RepoRouter 류 (token_nowait(repo))
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.redis = redis
        self.launcher = launcher or build_launcher(settings, redis)
        # D-41: 실 모드면 installation 토큰으로 clone·push (워커에는 안 준다)
        # P9.9: 프로젝트별 installation — repo로 client·토큰을 고른다
        router = (
            RepoRouter(factory, ClientRegistry(settings))
            if token_provider is None or github is None
            else None
        )
        self.token_provider = token_provider
        if self.token_provider is None and router is not None and router.token_getter is not None:
            self.token_provider = router
        token_getter = self.token_provider.token_nowait if self.token_provider else None
        self.repo_cache = RepoCache(
            Path(settings.repo_root), token_getter=token_getter, dry_run=settings.dry_run
        )  # D-38, D-48
        self.bus = EventBus(redis)
        self.relay = OutboxRelay(factory, redis)
        self.projection = Projection(factory, self.bus)
        self.scheduler = Scheduler(
            factory,
            self.bus,
            self.launcher,
            projection=self.projection,
            max_workers=settings.scheduler_max_workers,
            repo_resolver=lambda repo: str(self.repo_cache.ensure(repo)),
        )
        self.handlers: list[Handler] = [self.projection.handle, self.scheduler.handle]
        # D-37: Dry/실 선택은 control plane. 실 모드면 repo로 installation client를 고른다 (P9.9)
        self.github = github if github is not None else cast(RepoRouter, router).github
        self.pr_opener = PrOpener(
            factory,
            self.bus,
            self.github,
            pusher=GitPusher(token_getter) if token_getter is not None else None,
            repo_path_for=self.repo_cache.ensure,
        )
        self.handlers.append(self.pr_opener.handle)
        self.dry_merger: DryMerger | None = None
        if settings.dry_run:  # D-36: Dry에서만 사람 머지를 흉내 낸다
            self.dry_merger = DryMerger(
                factory, self.bus, enabled=True, repo_path_for=self.repo_cache.ensure
            )
            self.handlers.append(self.dry_merger.handle)
        self.tasks: list[asyncio.Task[None]] = []
        self._retry_interval = retry_interval
        self._reap_interval = reap_interval
        self._clock = clock or (lambda: datetime.now(UTC))
        self._consumer = consumer
        self._block_ms = block_ms
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ 수명
    async def start(self) -> None:
        self._stop.clear()
        self.relay.start()
        assert self.relay._task is not None
        await self.scheduler.recover_orphans()  # D-44 (3): 이전 프로세스의 assigned/running 잔재
        await self.scheduler.complete_all()  # P9.7: 끝났는데 active로 남은 Goal·Epic
        self.tasks = [
            self.relay._task,
            asyncio.create_task(self._consume(), name="control-plane-consumer"),
            asyncio.create_task(self._retry_loop(), name="control-plane-retry"),
            asyncio.create_task(self._reap_loop(), name="control-plane-reaper"),
        ]
        log.info("runtime.started", launcher=type(self.launcher).__name__)

    async def stop(self) -> None:
        self._stop.set()
        self.bus.stop()
        await self.relay.stop()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        log.info("runtime.stopped")

    # ------------------------------------------------------------------ 루프
    async def _handle(self, delivery: Delivery) -> None:
        for handler in self.handlers:
            await handler(delivery)

    async def _consume(self) -> None:
        await self.bus.subscribe(
            GROUP, self._handle, consumer=self._consumer, block_ms=self._block_ms
        )

    async def _reap_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.scheduler.reap(now=self._clock())
            except Exception:
                log.exception("runtime.reap_loop_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._reap_interval)
            except TimeoutError:
                pass

    async def _retry_loop(self) -> None:
        while not self._stop.is_set():
            if self.token_provider is not None:  # D-41: 동기 경로용 토큰을 미리 신선하게
                try:
                    await self.token_provider.refresh_all()  # P9.9: 모든 프로젝트의 installation
                except Exception:
                    log.exception("runtime.token_refresh_failed")
            try:
                async for key in self.redis.scan_iter(match=f"{STREAM_PREFIX}*{RETRY_SUFFIX}"):
                    k = key if isinstance(key, str) else key.decode()
                    project_id = k[len(STREAM_PREFIX) : -len(RETRY_SUFFIX)]
                    await self.projection.apply_retries(project_id, now=self._clock())
            except Exception:
                log.exception("runtime.retry_loop_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._retry_interval)
            except TimeoutError:
                pass


def build_runtime(settings: Settings, **kw: Any) -> Runtime:
    """설정만으로 조립 (``python -m control_plane``). kw는 Runtime 옵션."""
    engine = sess.create_engine(settings)
    factory = sess.create_session_factory(engine)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return Runtime(settings, factory, redis, **kw)
