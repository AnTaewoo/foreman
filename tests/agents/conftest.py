"""P4 공용 픽스처: bare remote(D-14), sample_repo 사본 worktree(미끼 secret), spy, ToolContext."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agents.tools.base import ToolContext
from control_plane.events.schema import Event

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE = FIXTURES / "sample_repo"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com",
}  # fmt: skip


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**GIT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin"},
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    target = tmp_path / "remote.git"
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(target)], check=True, capture_output=True)
    return target


@pytest.fixture
def worktree(tmp_path: Path, remote: Path) -> Path:
    wt = tmp_path / "worktree"
    shutil.copytree(
        SAMPLE,
        wt,
        ignore=shutil.ignore_patterns(
            "dot_git_stub", ".venv", "node_modules", "__pycache__", ".pytest_cache"
        ),
    )
    # 미끼 secrets
    (wt / ".env").write_text("SECRET=hunter2\n")
    (wt / ".env.local").write_text("TOKEN=abc\n")
    (wt / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n")
    (wt / "deploy.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n")
    (wt / ".gitignore").write_text(".env\n.env.*\nid_rsa*\n*.pem\n")
    git(wt, "init", "-q", "-b", "main")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "fixture: sample_repo")
    git(wt, "remote", "add", "origin", str(remote))
    git(wt, "push", "-q", "origin", "main")
    return wt


class Spy:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> Event:
        self.events.append(event)
        return event

    def types(self) -> list[str]:
        return [e.type.value for e in self.events]


@pytest.fixture
def spy() -> Spy:
    return Spy()


@pytest.fixture
def ctx(worktree: Path, spy: Spy) -> ToolContext:
    return ToolContext(
        worktree=worktree,
        owned_paths=["src/app/**", "tests/**"],
        run_id="01RUN",
        task_id="01TASK",
        project_id="P1",
        goal_id="G1",
        publish=spy.publish,
        default_branch="main",
        agent_id="coding-1",
        last_event_id="EV0",
    )
