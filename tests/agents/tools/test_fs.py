"""P4.1 fs 툴 (red a~d, m). 거부 > 허용."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.base import ToolContext, ToolDenied
from agents.tools.fs import FsTool
from tests.agents.conftest import Spy


@pytest.fixture
def fs(ctx: ToolContext) -> FsTool:
    return FsTool(ctx)


# (a) 탈출
@pytest.mark.parametrize(
    "path", ["../outside.txt", "/etc/passwd", "src/../../x", "src/app/../../../y"]
)
async def test_outside_worktree_denied(fs: FsTool, path: str) -> None:
    with pytest.raises(ToolDenied, match="outside"):
        await fs.read(path)
    with pytest.raises(ToolDenied, match="outside"):
        await fs.write(path, "x")


# (b) secrets read 거부
@pytest.mark.parametrize("path", [".env", ".env.local", "deploy.pem", "id_rsa", ".git/config"])
async def test_secret_read_denied(fs: FsTool, path: str) -> None:
    with pytest.raises(ToolDenied, match="secret"):
        await fs.read(path)


# (c) owned_paths 밖 write 거부, secret은 owned여도 거부
async def test_write_outside_owned_paths_denied(fs: FsTool) -> None:
    with pytest.raises(ToolDenied, match="owned_paths"):
        await fs.write("README.md", "hack")
    with pytest.raises(ToolDenied, match="owned_paths"):
        await fs.write("docs/x.md", "hack")


async def test_secret_write_denied_even_if_owned(worktree: Path, spy: Spy) -> None:
    ctx = ToolContext(worktree=worktree, owned_paths=["**"], run_id="R", task_id="T", project_id="P",
                      goal_id="G", publish=spy.publish, last_event_id=None)  # fmt: skip
    fs = FsTool(ctx)
    for p in (".env", "src/app/secret.pem", "src/app/id_rsa"):
        with pytest.raises(ToolDenied, match="secret"):
            await fs.write(p, "x")


# (d) 허용 read/write/list
async def test_read_write_list(fs: FsTool, worktree: Path) -> None:
    assert "def greet" in await fs.read("src/app/main.py")
    await fs.write("src/app/new/dir/users.py", "X = 1\n")  # 중간 디렉토리 생성
    assert (worktree / "src/app/new/dir/users.py").read_text() == "X = 1\n"
    await fs.write("tests/test_users.py", "def test_x():\n    assert True\n")
    listing = await fs.list(".")
    assert "src/app/main.py" in listing and "tests/test_users.py" in listing
    assert not any(p.startswith(".git/") or p == ".git" for p in listing)
    sub = await fs.list("src/app")
    assert "src/app/models.py" in sub and "src/app/new/dir/users.py" in sub
    with pytest.raises(FileNotFoundError):
        await fs.read("src/app/nope.py")


# (m) 이벤트: 허용 → run.tool_called(subject=run, args_digest), 거부 → run.tool_denied; 비밀값·내용 없음
async def test_events_for_allowed_and_denied(fs: FsTool, spy: Spy) -> None:
    await fs.read("src/app/main.py")
    with pytest.raises(ToolDenied):
        await fs.read(".env")
    assert spy.types() == ["run.tool_called", "run.tool_denied"]
    ok, denied = spy.events
    assert ok.subject.entity == "run" and ok.subject.id == "01RUN"
    assert ok.payload["tool"] == "fs.read" and len(ok.payload["args_digest"]) == 64
    assert ok.correlation_id == "G1" and ok.causation_id == "EV0"
    assert denied.causation_id == ok.id  # causation 체인
    assert denied.payload["tool"] == "fs.read" and "secret" in denied.payload["reason"]
    assert ".env" not in denied.payload["args_digest"]
    for e in spy.events:
        dumped = e.model_dump_json()
        assert "hunter2" not in dumped and "def greet" not in dumped
