"""P4.1 shell 툴 (red e~g). 허용 프리픽스만, 셸 없이 exec."""

from __future__ import annotations

from pathlib import Path

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
        {
            "pytest",
            "python -m pytest",  # P9 버그 #3: 빈 repo(pyproject 없음)에서 루트 모듈 import
            "ruff",
            "mypy",
            "npm test",
            "npm run test",
            "make",
            "uv run pytest",
        }
    )


# P9 버그 #3 (foreman_demo): pyproject/conftest가 없는 repo에서 `pytest -q`가 루트 모듈을 import 못 해
# (ModuleNotFoundError) 3회 실패 → 워커는 pyproject를 못 만든다(needs_decision). 툴이 PYTHONPATH=worktree를 준다
async def test_pytest_imports_repo_root_modules_without_config(tmp_path: Path, spy: Spy) -> None:
    from agents.tools.base import ToolContext

    wt = tmp_path / "empty-repo"
    (wt / "tests").mkdir(parents=True)
    (wt / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    (wt / "tests" / "test_calc.py").write_text("from calculator import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    ctx = ToolContext(
        worktree=wt, owned_paths=["**"], run_id="01RUN", task_id="01TASK", project_id="P1", goal_id="G1",
        publish=spy.publish, default_branch="main", agent_id="coding-1", last_event_id="EV0",
    )  # fmt: skip
    result = await ShellTool(ctx).run("pytest -q", timeout=120)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


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


# PC-4 (기록): pytest 실행 후 __pycache__가 생기면 `git add -A`가 .pyc를 커밋한다
async def test_no_bytecode_written(shell: ShellTool, worktree: Path) -> None:
    await shell.run("pytest -q", timeout=120)
    assert not list(worktree.rglob("__pycache__"))
