"""워커 entrypoint: ``python -m worker <task_id> [--publish-file P] [--workdir D]`` (설계 §10.3, §15.1).

설정은 **환경변수뿐** (Settings 클래스를 import하지 않는다 — 워커는 secrets를 받지 않는다, §12):

    WORKER_REPO_URL       clone 대상 (bare remote 경로 또는 URL)
    WORKER_BRANCH         작업 브랜치 (ai/<epic>/<n>-<slug>)
    WORKER_TASK_JSON      AgentInput 직렬화 {task, project_context, run_id, agent_id}
    WORKER_REDIS_URL      이벤트 XADD 대상 (--publish-file이 없을 때)
    WORKER_TOKEN          옵션(빈 값). MVP 1은 GitHub 쓰기가 Dry라 필요 없다
    WORKER_TIMEOUT_MIN    기본 45
    WORKER_LLM_PROVIDER   fake | openai_compat | anthropic (D-33) + WORKER_LLM_BASE_URL/MODEL/API_KEY,
                          fake는 WORKER_FAKE_SCRIPT(JSON 스크립트 파일)

종료 코드: 0 done / 1 failed / 2 needs_decision·blocked / 3 timeout / 64 설정 오류.
타임아웃이면 WIP 커밋+push 후 ``task.failed(reason=timeout)``·``run.finished(outcome=timeout)`` (D-28).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import structlog
from redis.asyncio import Redis

from agents.base import AgentInput, Publish
from agents.coding import CodingAgent
from agents.llm.anthropic import AnthropicProvider
from agents.llm.base import ModelProvider
from agents.llm.fake import FakeProvider
from agents.llm.ollama import OllamaCompatProvider
from control_plane.events.schema import Actor, Event, EventType, Subject
from github_adapter.dry_run import DryRunGitHubClient
from worker.publish import FilePublisher, RedisPublisher

log = structlog.get_logger(__name__)

REQUIRED_ENV = ("WORKER_REPO_URL", "WORKER_BRANCH", "WORKER_TASK_JSON")
EXIT_BY_OUTCOME = {"done": 0, "failed": 1, "needs_decision": 2, "blocked": 2, "timeout": 3}
EXIT_USAGE = 64
GIT_ENV = {
    "GIT_AUTHOR_NAME": "foreman-agent",
    "GIT_AUTHOR_EMAIL": "agent@foreman.local",
    "GIT_COMMITTER_NAME": "foreman-agent",
    "GIT_COMMITTER_EMAIL": "agent@foreman.local",
}


def provider_from_env(env: Mapping[str, str]) -> ModelProvider:
    kind = env.get("WORKER_LLM_PROVIDER", "openai_compat")
    if kind == "fake":
        script_path = env.get("WORKER_FAKE_SCRIPT")
        script: list[Any] = (
            json.loads(Path(script_path).read_text(encoding="utf-8")) if script_path else []
        )
        return FakeProvider(script=script)
    if kind == "openai_compat":
        return OllamaCompatProvider(
            base_url=env.get("WORKER_LLM_BASE_URL", "http://localhost:11434/v1"),
            model=env.get("WORKER_LLM_MODEL", "qwen2.5-coder:7b"),
            api_key=env.get("WORKER_LLM_API_KEY", "ollama"),
        )
    if kind == "anthropic":
        return AnthropicProvider(
            env.get("WORKER_LLM_API_KEY", ""), model=env.get("WORKER_LLM_MODEL", "claude-opus-5")
        )
    raise ValueError(f"WORKER_LLM_PROVIDER must be fake|openai_compat|anthropic, got {kind!r}")


def _git(cwd: Path, *args: str, env: Mapping[str, str]) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**GIT_ENV, "PATH": env.get("PATH", "/usr/bin:/bin"), "HOME": env.get("HOME", "/tmp")},
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout.strip()


def prepare_worktree(repo_url: str, branch: str, workdir: Path, env: Mapping[str, str]) -> Path:
    """clone → 작업 브랜치 (있으면 checkout, 없으면 default에서 분기)."""
    repo = workdir / "repo"
    workdir.mkdir(parents=True, exist_ok=True)
    _git(workdir, "clone", "-q", repo_url, str(repo), env=env)
    remote_branches = _git(repo, "branch", "-r", "--list", f"origin/{branch}", env=env)
    if remote_branches.strip():
        _git(repo, "checkout", "-q", "-B", branch, f"origin/{branch}", env=env)
    else:
        _git(repo, "checkout", "-q", "-B", branch, env=env)
    return repo


def _wip_push(repo: Path, branch: str, env: Mapping[str, str]) -> None:
    try:
        _git(repo, "add", "-A", env=env)
        if _git(repo, "status", "--porcelain", env=env):
            _git(repo, "commit", "-q", "-m", "wip: worker timeout (kept for inspection)", env=env)
        _git(repo, "push", "-q", "-u", "origin", f"{branch}:{branch}", env=env)
    except RuntimeError as exc:
        log.warning("worker.wip_push_failed", error=str(exc))


async def _publish_timeout_events(
    publish: Publish, agent: CodingAgent, input: AgentInput, started: float
) -> None:
    def ev(type_: EventType, entity: str, id_: str, payload: dict[str, Any]) -> Event:
        return Event(
            project_id=input.project_context.project_id,
            actor=Actor(type="agent", id=input.agent_id),
            type=type_,
            subject=Subject(entity=entity, id=id_),  # type: ignore[arg-type]  # entity ∈ Literal
            payload=payload,
            correlation_id=input.project_context.goal_id,
            causation_id=agent.last_event_id,
        )

    failed = await publish(
        ev(EventType.TASK_FAILED, "task", input.task.id,
           {"run_id": input.run_id, "reason": "timeout", "attempt": input.task.attempt})
    )  # fmt: skip
    agent.last_event_id = failed.id
    await publish(
        ev(EventType.RUN_FINISHED, "run", input.run_id, {
            "outcome": "timeout", "agent_outcome": "timeout", "tokens_in": 0, "tokens_out": 0,
            "cost_usd": 0.0, "duration_s": round(time.monotonic() - started, 3),
            "error": f"timeout after {input.budget.max_seconds}s",
        })
    )  # fmt: skip


async def _run(
    args: argparse.Namespace, env: Mapping[str, str], provider: ModelProvider | None
) -> int:
    input = AgentInput.model_validate(json.loads(env["WORKER_TASK_JSON"]))
    timeout_s = float(env.get("WORKER_TIMEOUT_MIN", "45")) * 60
    input = input.model_copy(
        update={"budget": input.budget.model_copy(update={"max_seconds": int(timeout_s)})}
    )
    workdir = Path(args.workdir) if args.workdir else Path("/work") / input.run_id
    repo = prepare_worktree(env["WORKER_REPO_URL"], env["WORKER_BRANCH"], workdir, env)

    redis: Redis | None = None
    publish: Publish
    if args.publish_file:
        publish = FilePublisher(Path(args.publish_file))
    else:
        redis = Redis.from_url(env["WORKER_REDIS_URL"], decode_responses=True)
        publish = RedisPublisher(redis)
    prov = provider or provider_from_env(env)
    agent = CodingAgent(
        publish=publish,
        provider=prov,
        github=DryRunGitHubClient(),  # MVP 1: GitHub 쓰기는 항상 Dry (워커는 secrets 없음)
        repo=input.project_context.repo,
        worktree=repo,
        model=env.get("WORKER_LLM_MODEL"),
        test_command=env.get("WORKER_TEST_COMMAND", "pytest -q"),
        shell_timeout=min(timeout_s, 600.0),
    )
    started = time.monotonic()
    try:
        output = await asyncio.wait_for(agent.run(input), timeout=timeout_s)
    except TimeoutError:
        log.error("worker.timeout", run_id=input.run_id, seconds=timeout_s)
        _wip_push(repo, env["WORKER_BRANCH"], env)
        await _publish_timeout_events(publish, agent, input, started)
        if redis is not None:
            await redis.aclose()
        return EXIT_BY_OUTCOME["timeout"]
    if redis is not None:
        await redis.aclose()
    log.info(
        "worker.done", run_id=input.run_id, outcome=output.outcome, summary=output.summary[:200]
    )
    return EXIT_BY_OUTCOME.get(output.outcome, 1)


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    provider: ModelProvider | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m worker")
    parser.add_argument("task_id")
    parser.add_argument(
        "--publish-file", default=None, help="이벤트를 Redis 대신 JSON-lines 파일로"
    )
    parser.add_argument("--workdir", default=None, help="clone 위치 (기본 /work/<run_id>)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    environ = dict(env if env is not None else os.environ)
    missing = [k for k in REQUIRED_ENV if not environ.get(k)]
    if not args.publish_file and not environ.get("WORKER_REDIS_URL"):
        missing.append("WORKER_REDIS_URL")
    if missing:
        print(f"worker: missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    return asyncio.run(_run(args, environ, provider))
