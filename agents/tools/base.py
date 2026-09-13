"""Agent 툴 공통 (설계 §5.2 Coding Agent 도구, §10.3, §12). **거부 > 허용.**

- ``ToolContext``: worktree 경계, owned_paths, Run/Task 식별자, 이벤트 발행. 비밀값·파일 내용은
  이벤트에 싣지 않는다 — 인자는 sha256 digest만 (D-31).
- 허용된 호출 → ``run.tool_called``(subject=run, 체인 밖). 거부 → ``run.tool_denied``(감사 체인).
- ``guarded(ctx, tool, args)``: 호출을 감싸 시간을 재고 결과에 따라 두 이벤트 중 하나를 발행한다.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from control_plane.events.schema import Actor, Event, EventType, Subject

Publish = Callable[[Event], Awaitable[Event]]

SECRET_PATTERNS = (".env", ".env.*", "*.pem", "id_rsa*", "*.key", ".git/config")
_SECRET_DIRS = (".git",)


class ToolDenied(Exception):
    """정책 위반. ``reason``은 짧은 코드(outside / secret / owned_paths / branch / default / …)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class ToolTimeout(Exception):
    """명령이 제한 시간 안에 끝나지 않았다."""


class Tool(Protocol):
    name: str


def args_digest(args: dict[str, Any]) -> str:
    """인자 전체의 sha256 — 값 자체는 이벤트에 남기지 않는다."""
    body = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """``**`` = 여러 디렉토리, ``*`` = 한 구성요소 안."""
    out = "^"
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile(out + "$")


def is_secret_path(rel: str) -> bool:
    p = PurePosixPath(rel)
    if p.parts and p.parts[0] in _SECRET_DIRS:
        return True
    name = p.name
    return any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat) for pat in SECRET_PATTERNS)


@dataclass
class ToolContext:
    worktree: Path
    owned_paths: list[str]
    run_id: str
    task_id: str
    project_id: str
    goal_id: str
    publish: Publish
    default_branch: str = "main"
    agent_id: str = "coding-1"
    last_event_id: str | None = None
    _owned: list[re.Pattern[str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.worktree = Path(self.worktree).resolve()
        self._owned = [_glob_to_regex(p.strip("/")) for p in self.owned_paths]

    # ------------------------------------------------------------------ 경계
    def resolve(self, path: str) -> Path:
        """worktree 안의 절대 경로. 밖이면 ToolDenied("outside")."""
        candidate = Path(path)
        target = candidate if candidate.is_absolute() else self.worktree / candidate
        resolved = target.resolve()
        if resolved != self.worktree and self.worktree not in resolved.parents:
            raise ToolDenied("outside", path)
        return resolved

    def relative(self, path: str) -> str:
        return self.resolve(path).relative_to(self.worktree).as_posix()

    def is_owned(self, rel: str) -> bool:
        rel = rel.strip("/")
        return any(r.match(rel) for r in self._owned)

    # ------------------------------------------------------------------ 이벤트
    async def record(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        denied: str | None = None,
        duration_ms: int = 0,
    ) -> Event:
        digest = args_digest(args)
        if denied is None:
            type_ = EventType.RUN_TOOL_CALLED
            payload: dict[str, Any] = {
                "tool": tool,
                "args_digest": digest,
                "duration_ms": duration_ms,
            }
        else:
            type_ = EventType.RUN_TOOL_DENIED
            payload = {"tool": tool, "reason": denied, "args_digest": digest}
        event = Event(
            project_id=self.project_id,
            actor=Actor(type="agent", id=self.agent_id),
            type=type_,
            subject=Subject(entity="run", id=self.run_id),
            payload=payload,
            correlation_id=self.goal_id,
            causation_id=self.last_event_id,
        )
        out = await self.publish(event)
        self.last_event_id = out.id
        return out


@asynccontextmanager
async def guarded(
    ctx: ToolContext, tool: str, args: dict[str, Any]
) -> Any:  # Any: 컨텍스트 값 없음
    """``async with guarded(ctx, "fs.read", {...}):`` — 거부면 tool_denied, 정상이면 tool_called."""
    started = time.monotonic()
    try:
        yield
    except ToolDenied as exc:
        await ctx.record(
            tool, args, denied=f"{exc.reason}: {exc.detail}" if exc.detail else exc.reason
        )
        raise
    await ctx.record(tool, args, duration_ms=int((time.monotonic() - started) * 1000))
