"""git 툴: 브랜치 규약 ``ai/<epic>/<issue>-<slug>``(§7.3), 기본 브랜치 push 거부, 트레일러(§6.3).

push 대상은 로컬 bare remote(D-14)든 실 GitHub든 ``origin``. 커밋 작성자는 에이전트 고정.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from agents.tools.base import ToolContext, ToolDenied, guarded

BRANCH_RE = re.compile(r"^ai/[a-z0-9][a-z0-9._-]*/\d+-[a-z0-9][a-z0-9._-]*$")
PROTECTED = frozenset({"main", "master"})
AGENT_NAME = "foreman-agent"
AGENT_EMAIL = "agent@foreman.local"


class GitError(Exception):
    pass


async def _git(cwd: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        f"user.name={AGENT_NAME}",
        "-c",
        f"user.email={AGENT_EMAIL}",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {err.decode(errors='replace').strip()}")
    return out.decode(errors="replace").strip("\n")  # porcelain의 선행 공백 보존


class GitTool:
    name = "git"

    def __init__(self, ctx: ToolContext) -> None:
        self._ctx = ctx
        self._wt = ctx.worktree

    async def current_branch(self) -> str:
        return (await _git(self._wt, "rev-parse", "--abbrev-ref", "HEAD")).strip()

    # (h)
    async def branch(self, name: str) -> str:
        async with guarded(self._ctx, "git.branch", {"name": name}):
            if not BRANCH_RE.match(name):
                raise ToolDenied("branch", f"{name!r} must match ai/<epic>/<n>-<slug>")
            existing = await _git(self._wt, "branch", "--list", name)
            if existing:
                await _git(self._wt, "checkout", "-q", name)
            else:
                await _git(self._wt, "checkout", "-q", "-b", name)
            return name

    # (j)
    async def commit(self, message: str, *, issue_number: int | None = None) -> str | None:
        async with guarded(
            self._ctx, "git.commit", {"message": message, "issue_number": issue_number}
        ):
            await _git(self._wt, "add", "-A")
            if not await _git(self._wt, "status", "--porcelain"):
                return None
            n = issue_number if issue_number is not None else "?"
            trailer = f"Task #{n} / Run {self._ctx.run_id}"
            await _git(self._wt, "commit", "-q", "-m", f"{message}\n\n{trailer}")
            return (await _git(self._wt, "rev-parse", "HEAD")).strip()

    # (i)(k) — 대상 브랜치가 실행 중에 정해지므로 guarded 대신 직접 record
    async def push(self, branch: str | None = None) -> str:
        target = branch or await self.current_branch()
        args = {"branch": target}
        if target in PROTECTED or target == self._ctx.default_branch:
            await self._ctx.record("git.push", args, denied=f"default: {target}")
            raise ToolDenied("default", f"push to {target} is forbidden")
        try:
            await _git(self._wt, "push", "-q", "-u", "origin", f"{target}:{target}")
        except GitError as exc:
            await self._ctx.record("git.push", args, denied=f"error: {exc}")
            raise
        await self._ctx.record("git.push", args)
        return target

    async def changed_files(self) -> list[str]:
        async with guarded(self._ctx, "git.changed_files", {}):
            status = await _git(self._wt, "status", "--porcelain", "--untracked-files=all")
            files: list[str] = []
            for line in status.splitlines():
                if len(line) < 4:
                    continue
                path = line[3:].strip()  # "XY path"
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                files.append(path)
            return sorted(files)

    async def diff(self) -> str:
        async with guarded(self._ctx, "git.diff", {}):
            await _git(self._wt, "add", "-N", "-A")  # untracked도 diff에 포함 (intent-to-add)
            return await _git(self._wt, "diff", "--no-color")
