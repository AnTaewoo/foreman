"""P4.4 (e) — 워커 컨테이너: docker build → sample_repo bare remote 볼륨 → 브랜치 push → exit 0. Docker 없으면 skip."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="docker not available (try: echo cmd | newgrp docker)")
def test_worker_container_pushes_branch(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    shutil.copytree(
        FIXTURES / "sample_repo",
        seed,
        ignore=shutil.ignore_patterns("dot_git_stub", ".venv", "node_modules"),
    )
    env = {
        "GIT_AUTHOR_NAME": "s",
        "GIT_AUTHOR_EMAIL": "s@x",
        "GIT_COMMITTER_NAME": "s",
        "GIT_COMMITTER_EMAIL": "s@x",
        "PATH": "/usr/bin:/bin",
    }
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "seed"],
        ["git", "remote", "add", "origin", str(remote)],
        ["git", "push", "-q", "origin", "main"],
    ):
        subprocess.run(cmd, cwd=seed, check=True, capture_output=True, env=env)
    (tmp_path / "remote.git").chmod(0o777)
    subprocess.run(
        ["docker", "build", "-q", "-t", "foreman-worker:test", "-f", "worker/Dockerfile", "."],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    events_dir.chmod(0o777)
    task = {
        "task": {
            "id": "01TASK",
            "title": "Add users module",
            "spec": "Add src/app/users.py + tests",
            "kind": "feature",
            "role_required": "coding",
            "owned_paths": ["src/app/**", "tests/**"],
            "issue_number": 12,
            "epic_slug": "users-api",
            "risk_tier": "T1",
            "attempt": 1,
            "max_attempts": 3,
        },
        "project_context": {
            "project_id": "P1",
            "goal_id": "G1",
            "repo": "org/demo",
            "default_branch": "main",
        },
        "run_id": "01RUN",
        "agent_id": "coding-1",
    }
    res = subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{remote}:/remote.git", "-v", f"{events_dir}:/events",
         "-v", f"{FIXTURES / 'coding_scripts'}:/scripts:ro",
         "-e", "WORKER_REPO_URL=/remote.git", "-e", "WORKER_BRANCH=ai/users-api/12-add-users-module",
         "-e", f"WORKER_TASK_JSON={json.dumps(task)}", "-e", "WORKER_REDIS_URL=redis://none",
         "-e", "WORKER_TOKEN=", "-e", "WORKER_TIMEOUT_MIN=5", "-e", "WORKER_LLM_PROVIDER=fake",
         "-e", "WORKER_FAKE_SCRIPT=/scripts/pass.json",
         "foreman-worker:test", "01TASK", "--publish-file", "/events/events.jsonl"],
        capture_output=True, text=True, cwd=ROOT,
    )  # fmt: skip
    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
    heads = subprocess.run(
        ["git", "branch", "--list"], cwd=remote, capture_output=True, text=True, env=env
    ).stdout
    assert "ai/users-api/12-add-users-module" in heads
    lines = (events_dir / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["type"] == "run.finished"
