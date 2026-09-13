"""shell 툴: 허용된 명령 프리픽스만, 셸 없이 ``create_subprocess_exec`` (CLAUDE.md, §5.2 sandbox)."""

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass

from agents.tools.base import ToolContext, ToolDenied, ToolTimeout, guarded

ALLOWED_PREFIXES: frozenset[str] = frozenset(
    {"pytest", "ruff", "mypy", "npm test", "npm run test", "make", "uv run pytest"}
)
FORBIDDEN_CHARS = (";", "&", "|", "$(", "`", ">", "<", "\n")
DEFAULT_TIMEOUT = 600.0
SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "VIRTUAL_ENV", "PYTHONPATH")


@dataclass(frozen=True)
class ShellResult:
    exit_code: int
    stdout: str
    stderr: str


def _check(cmd: str) -> list[str]:
    if cmd != cmd.strip() or not cmd:
        raise ToolDenied("whitespace", "command must not have leading/trailing whitespace")
    if any(ch in cmd for ch in FORBIDDEN_CHARS):
        raise ToolDenied("shell_syntax", "no shell operators allowed")
    if not any(cmd == p or cmd.startswith(p + " ") for p in ALLOWED_PREFIXES):
        raise ToolDenied("not_allowed", cmd.split(" ", 1)[0])
    try:
        return shlex.split(cmd)
    except ValueError as exc:
        raise ToolDenied("parse", str(exc)) from exc


class ShellTool:
    name = "shell"

    def __init__(self, ctx: ToolContext) -> None:
        self._ctx = ctx

    async def run(self, cmd: str, timeout: float = DEFAULT_TIMEOUT) -> ShellResult:
        async with guarded(self._ctx, "shell", {"cmd": cmd, "timeout": timeout}):
            argv = _check(cmd)
            env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self._ctx.worktree,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise ToolTimeout(f"{cmd!r} exceeded {timeout}s") from exc
            return ShellResult(
                exit_code=proc.returncode or 0,
                stdout=out.decode("utf-8", errors="replace"),
                stderr=err.decode("utf-8", errors="replace"),
            )
