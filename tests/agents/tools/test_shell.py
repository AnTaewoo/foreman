"""P4.1 shell 툴 (red e~g). 허용 프리픽스만, 셸 없이 exec."""

from __future__ import annotations

import pytest

from agents.tools.base import ToolContext, ToolDenied, ToolTimeout
from agents.tools.shell import ALLOWED_PREFIXES, ShellTool
from tests.agents.conftest import Spy


@pytest.fixture
def shell(ctx: ToolContext) -> ShellTool:
    return ShellTool(ctx)


# (e)
def test_allowed_prefixes() -> None:
    assert ALLOWED_PREFIXES == frozenset(
        {"pytest", "ruff", "mypy", "npm test", "npm run test", "make", "uv run pytest"}
    )


async def test_pytest_runs_in_worktree(shell: ShellTool, spy: Spy) -> None:
    result = await shell.run("pytest -q", timeout=120)
    assert result.exit_code == 0 and "2 passed" in result.stdout
    assert spy.types() == ["run.tool_called"] and spy.events[0].payload["tool"] == "shell"


async def test_failing_command_returns_nonzero(shell: ShellTool) -> None:
    result = await shell.run("pytest -q tests/nope.py", timeout=60)
    assert result.exit_code != 0


# (f) 거부 목록
@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /", "curl http://x", "python -c 'print(1)'", "pytest; rm -rf /", "pytest && rm x",
        "pytest | tee out", "pytest $(rm x)", "pytest `rm x`", "pytest > out.txt", "npm install",
        "sudo make", " pytest -q", "pytest -q ", "pytestx", "make; make", "uv run python x.py",
        "npm run build", "ruff check < in",
    ],
)  # fmt: skip
async def test_denied_commands(shell: ShellTool, cmd: str, spy: Spy) -> None:
    with pytest.raises(ToolDenied):
        await shell.run(cmd)
    assert spy.types() == ["run.tool_denied"]


async def test_no_shell_interpretation(shell: ShellTool) -> None:
    # 프리픽스는 통과하지만 인자에 와일드카드가 있어도 셸 확장 없이 그대로 전달된다
    result = await shell.run("pytest -q *.nothing", timeout=60)
    assert result.exit_code != 0 and "*.nothing" in (result.stdout + result.stderr)


# (g) 타임아웃
async def test_timeout(shell: ShellTool, worktree: object) -> None:
    from pathlib import Path

    assert isinstance(worktree, Path)
    (worktree / "tests" / "test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(5)\n"
    )
    with pytest.raises(ToolTimeout):
        await shell.run("pytest -q tests/test_slow.py", timeout=1)
