"""P6.1 (f) — DockerCliLauncher가 실제 컨테이너를 띄운다 (D-15, D-38 마운트). Docker 없으면 skip.

컨테이너는 호스트 Redis(host-gateway)에 XADD 하고, 마운트된 로컬 bare remote를 clone·push 한다.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from redis.asyncio import Redis

from control_plane.scheduler.launcher import DockerCliLauncher, LaunchSpec

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
TEST_REDIS_URL = os.environ.get("FOREMAN_TEST_REDIS_URL", "redis://localhost:6379/15")


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="docker not available (try: echo cmd | newgrp docker)")
async def test_docker_launcher_starts_worker(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    shutil.copytree(
        FIXTURES / "sample_repo",
        seed,
        ignore=shutil.ignore_patterns("dot_git_stub", ".venv", "node_modules"),
    )
    genv = {
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
        subprocess.run(cmd, cwd=seed, check=True, capture_output=True, env=genv)
    for path in [remote, *remote.rglob("*")]:  # 컨테이너 uid(10001)가 objects/에 쓸 수 있어야 한다
        path.chmod(0o777 if path.is_dir() else 0o666)
    subprocess.run(
        ["docker", "build", "-q", "-t", "foreman-worker:test", "-f", "worker/Dockerfile", "."],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await redis.flushdb()
    task_json = {
        "task": {
            "id": "T1",
            "title": "Add users module",
            "spec": "s",
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
            "project_id": "PDOCK",
            "goal_id": "G1",
            "repo": str(remote),
            "default_branch": "main",
        },
        "run_id": "01RUNDOCKER",
        "agent_id": "coding-1",
    }
    spec = LaunchSpec(
        task_id="T1",
        run_id="01RUNDOCKER",
        project_id="PDOCK",
        goal_id="G1",
        branch="ai/users-api/12-add-users-module",
        repo_url=str(remote),
        task_json=task_json,
        timeout_min=5,
    )
    # 호스트 Redis DB 15 → 컨테이너에서는 host.docker.internal
    port_db = TEST_REDIS_URL.rsplit(":", 1)[1]
    launcher = DockerCliLauncher(
        image="foreman-worker:test",
        redis_url=f"redis://host.docker.internal:{port_db}",
        worker_env={"WORKER_LLM_PROVIDER": "fake", "WORKER_FAKE_SCRIPT": "/scripts/pass.json"},
        # remote 디렉토리 자체를 같은 경로에 마운트 — pytest tmp의 부모(0o700)는 컨테이너 uid가 못 지나간다
        mounts=[(str(remote), str(remote)), (str(FIXTURES / "coding_scripts"), "/scripts")],
    )
    container = await launcher.launch(spec)
    try:
        for _ in range(120):
            entries = await redis.xrange("events:PDOCK")
            types = [f["type"] for _, f in entries]
            if "run.finished" in types:
                break
            await asyncio.sleep(1)
        assert types and types[0] == "task.started" and types[-1] == "run.finished", types
        heads = subprocess.run(
            ["git", "branch", "--list"], cwd=remote, capture_output=True, text=True, env=genv
        ).stdout
        assert "ai/users-api/12-add-users-module" in heads
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        await redis.flushdb()
        await redis.aclose()
