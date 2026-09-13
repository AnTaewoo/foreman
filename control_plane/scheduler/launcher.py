"""워커 기동 (D-15): ``DockerCliLauncher``는 subprocess로 docker CLI, 테스트는 ``FakeLauncher``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
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
    """``docker run -d --rm -e WORKER_* [-v src:dst …] <image> <task_id>`` → 컨테이너 id.

    inspect로 기동 확인. ``mounts`` (D-38): 로컬 repo 루트를 같은 경로로 마운트 — 워커가 clone·push.
    """

    def __init__(
        self,
        *,
        image: str = "foreman-worker:dev",
        redis_url: str,
        worker_env: dict[str, str] | None = None,
        docker_bin: str = "docker",
        extra_args: tuple[str, ...] = ("--add-host", "host.docker.internal:host-gateway"),
        mounts: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.image = image
        self.redis_url = redis_url
        self._worker_env = worker_env or {}
        self._docker = docker_bin
        self._extra = extra_args
        self.mounts = tuple(mounts)

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
        for k, v in spec.env(redis_url=self.redis_url, extra=self._worker_env).items():
            env_args += ["-e", f"{k}={v}"]
        mount_args: list[str] = []
        for src, dst in self.mounts:
            mount_args += ["-v", f"{src}:{dst}"]
        container = await self._run(
            "run",
            "-d",
            "--rm",
            "--name",
            f"foreman-worker-{spec.run_id.lower()}",
            *self._extra,
            *mount_args,
            *env_args,
            self.image,
            spec.task_id,
        )
        state = json.loads(await self._run("inspect", "--format", "{{json .State}}", container))
        if not state.get("Running") and state.get("ExitCode", 0) not in (0, None):
            raise LaunchError(f"container {container[:12]} exited immediately: {state}")
        log.info("launcher.started", container=container[:12], task_id=spec.task_id)
        return container


class InProcessLauncher:
    """control plane 프로세스 안에서 CodingAgent를 asyncio Task로 실행 (P6.1, Docker 없는 개발용).

    워커 계약과 같다: clone → ``CodingAgent.run`` → 이벤트는 Redis XADD(미서명, ingest가 서명).
    GitHub는 Dry(워커와 동일, D-37 이후 PR은 control plane이 연다). provider는 호출자가 준다.
    """

    def __init__(
        self,
        redis: Any,  # Any: redis.asyncio.Redis — launcher 모듈은 redis 타입에 의존하지 않는다
        *,
        provider_factory: Callable[[], Any],  # Any: agents.llm.base.ModelProvider
        workdir: Path,
        model: str | None = None,
        test_command: str = "pytest -q",
        shell_timeout: float = 300.0,
    ) -> None:
        self._redis = redis
        self._provider_factory = provider_factory
        self._workdir = Path(workdir)
        self._model = model
        self._test_command = test_command
        self._shell_timeout = shell_timeout
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.results: dict[str, str] = {}  # run_id → outcome / "crashed: …"

    async def launch(self, spec: LaunchSpec) -> str:
        task = asyncio.create_task(self._run(spec), name=f"inprocess-{spec.run_id}")
        self._tasks[spec.run_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(spec.run_id, None))
        return f"inprocess-{spec.run_id}"

    async def _run(self, spec: LaunchSpec) -> None:
        from agents.base import AgentInput
        from agents.coding import CodingAgent
        from github_adapter.dry_run import DryRunGitHubClient
        from worker.entrypoint import prepare_worktree
        from worker.publish import RedisPublisher

        try:
            wd = self._workdir / spec.run_id
            repo = prepare_worktree(
                spec.repo_url,
                spec.branch,
                wd,
                {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(wd)},
            )
            agent = CodingAgent(
                publish=RedisPublisher(self._redis),
                provider=self._provider_factory(),
                github=DryRunGitHubClient(),
                repo=str(spec.task_json["project_context"]["repo"]),
                worktree=repo,
                model=self._model,
                test_command=self._test_command,
                shell_timeout=self._shell_timeout,
            )
            output = await asyncio.wait_for(
                agent.run(AgentInput.model_validate(spec.task_json)), timeout=spec.timeout_min * 60
            )
            self.results[spec.run_id] = output.outcome
        except Exception as exc:
            log.error("inprocess.crashed", run_id=spec.run_id, error=repr(exc))
            self.results[spec.run_id] = f"crashed: {exc!r}"

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
