"""워커 기동 (D-15): ``DockerCliLauncher``는 subprocess로 ``docker`` CLI, 테스트는 ``FakeLauncher``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)


class LaunchError(Exception):
    pass


@dataclass(frozen=True)
class LaunchSpec:
    task_id: str
    run_id: str
    project_id: str
    goal_id: str
    branch: str
    repo_url: str
    task_json: dict[str, Any]  # AgentInput dump (WORKER_TASK_JSON)
    timeout_min: int

    def env(self, *, redis_url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {
            "WORKER_REPO_URL": self.repo_url,
            "WORKER_BRANCH": self.branch,
            "WORKER_TASK_JSON": json.dumps(self.task_json, ensure_ascii=False),
            "WORKER_REDIS_URL": redis_url,
            "WORKER_TOKEN": "",
            "WORKER_TIMEOUT_MIN": str(self.timeout_min),
            **(extra or {}),
        }


class WorkerLauncher(Protocol):
    async def launch(self, spec: LaunchSpec) -> str:
        """워커 식별자(컨테이너 id 등) 반환. 실패면 LaunchError."""
        ...


OnLaunch = Callable[[LaunchSpec], Awaitable[None]]


@dataclass
class FakeLauncher:
    """기록만 한다. ``on_launch``를 주면 프로세스 내에서 워커 역할을 대신 수행(PC-4)."""

    fail: bool = False
    on_launch: OnLaunch | None = None
    specs: list[LaunchSpec] = field(default_factory=list)
    order: list[tuple[str, str]] = field(default_factory=list)

    async def launch(self, spec: LaunchSpec) -> str:
        self.order.append(("launch", spec.task_id))
        if self.fail:
            raise LaunchError(f"fake launcher refused {spec.task_id}")
        self.specs.append(spec)
        if self.on_launch is not None:
            await self.on_launch(spec)
        return f"fake-{spec.run_id}"


class DockerCliLauncher:
    """``docker run -d --rm -e WORKER_* <image> <task_id>`` → 컨테이너 id. ``docker inspect``로 기동 확인."""

    def __init__(
        self,
        *,
        image: str = "foreman-worker:dev",
        redis_url: str,
        worker_env: dict[str, str] | None = None,
        docker_bin: str = "docker",
        extra_args: tuple[str, ...] = ("--add-host", "host.docker.internal:host-gateway"),
    ) -> None:
        self._image = image
        self._redis_url = redis_url
        self._worker_env = worker_env or {}
        self._docker = docker_bin
        self._extra = extra_args

    async def _run(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            self._docker, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise LaunchError(f"docker {args[0]}: {err.decode(errors='replace').strip()}")
        return out.decode(errors="replace").strip()

    async def launch(self, spec: LaunchSpec) -> str:
        env_args: list[str] = []
        for k, v in spec.env(redis_url=self._redis_url, extra=self._worker_env).items():
            env_args += ["-e", f"{k}={v}"]
        container = await self._run(
            "run", "-d", "--rm", "--name", f"foreman-worker-{spec.run_id.lower()}",
            *self._extra, *env_args, self._image, spec.task_id,
        )  # fmt: skip
        state = json.loads(await self._run("inspect", "--format", "{{json .State}}", container))
        if not state.get("Running") and state.get("ExitCode", 0) not in (0, None):
            raise LaunchError(f"container {container[:12]} exited immediately: {state}")
        log.info("launcher.started", container=container[:12], task_id=spec.task_id)
        return container
