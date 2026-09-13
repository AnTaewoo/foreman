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
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.llm.pricing import Prices
from control_plane.config import Settings
from control_plane.dry_merge import DryMerger
from control_plane.events.bus import RETRY_SUFFIX, STREAM_PREFIX, Delivery, EventBus
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.scheduler.launcher import DockerCliLauncher, InProcessLauncher, WorkerLauncher
from control_plane.scheduler.scheduler import Scheduler
from control_plane.store import session as sess

log = structlog.get_logger(__name__)

Handler = Callable[[Delivery], Awaitable[None]]
GROUP = "control-plane"


def host_url(url: str) -> str:
    """컨테이너에서 호스트 서비스로: localhost/127.0.0.1 → host.docker.internal."""
    return url.replace("://localhost", "://host.docker.internal").replace(
        "://127.0.0.1", "://host.docker.internal"
    )


def worker_llm_env(settings: Settings) -> dict[str, str]:
    """워커 컨테이너의 LLM 설정 (P4.4 계약). 키는 provider 키만 — GitHub 토큰은 주지 않는다(§12)."""
    env: dict[str, str] = {"WORKER_LLM_PROVIDER": settings.llm_provider}
    if settings.llm_provider == "openai_compat":
        env["WORKER_LLM_BASE_URL"] = host_url(settings.llm_base_url)
        env["WORKER_LLM_MODEL"] = settings.llm_model
        env["WORKER_LLM_API_KEY"] = settings.llm_api_key.get_secret_value()
    elif settings.llm_provider == "anthropic":
        env["WORKER_LLM_MODEL"] = settings.anthropic_model
        env["WORKER_LLM_API_KEY"] = settings.anthropic_api_key.get_secret_value()
    env.update(prices_of(settings).to_env())  # D-39
    return env


def prices_of(settings: Settings) -> Prices:
    return Prices(settings.llm_price_in_per_mtok, settings.llm_price_out_per_mtok)


def build_launcher(
    settings: Settings, redis: Redis, *, mounts: list[tuple[str, str]] | None = None
) -> WorkerLauncher:
    if settings.worker_launcher == "docker":
        return DockerCliLauncher(
            image=settings.worker_image,
            redis_url=host_url(settings.redis_url),
            worker_env=worker_llm_env(settings),
            mounts=mounts or [],
        )
    from agents.llm import get_provider

    return InProcessLauncher(
        redis,
        provider_factory=lambda: get_provider(settings),
        workdir=Path(tempfile.gettempdir()) / "foreman-inprocess",
        model=settings.llm_model if settings.llm_provider == "openai_compat" else None,
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
        clock: Callable[[], datetime] | None = None,
        consumer: str = "cp1",
        block_ms: int = 200,
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.redis = redis
        self.launcher = launcher or build_launcher(settings, redis)
        self.bus = EventBus(redis)
        self.relay = OutboxRelay(factory, redis)
        self.projection = Projection(factory, self.bus)
        self.scheduler = Scheduler(
            factory,
            self.bus,
            self.launcher,
            projection=self.projection,
            max_workers=settings.scheduler_max_workers,
        )
        self.handlers: list[Handler] = [self.projection.handle, self.scheduler.handle]
        self.dry_merger: DryMerger | None = None
        if settings.dry_run:  # D-36: Dry에서만 사람 머지를 흉내 낸다
            self.dry_merger = DryMerger(factory, self.bus, enabled=True)
            self.handlers.append(self.dry_merger.handle)
        self.tasks: list[asyncio.Task[None]] = []
        self._retry_interval = retry_interval
        self._clock = clock or (lambda: datetime.now(UTC))
        self._consumer = consumer
        self._block_ms = block_ms
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ 수명
    async def start(self) -> None:
        self._stop.clear()
        self.relay.start()
        assert self.relay._task is not None
        self.tasks = [
            self.relay._task,
            asyncio.create_task(self._consume(), name="control-plane-consumer"),
            asyncio.create_task(self._retry_loop(), name="control-plane-retry"),
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

    async def _retry_loop(self) -> None:
        while not self._stop.is_set():
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
