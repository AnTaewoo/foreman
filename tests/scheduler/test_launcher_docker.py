"""P8.2 — DockerCliLauncher argv (F-5a, F-5b, D-43): repo 개별 마운트, 호스트 uid, HOME/workdir. docker 없이."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from control_plane.scheduler.launcher import DockerCliLauncher, LaunchSpec


def spec(repo_url: str) -> LaunchSpec:
    return LaunchSpec(
        task_id="T1", run_id="01RUN", project_id="P1", goal_id="G1", branch="ai/e/1-t",
        repo_url=repo_url, task_json={"task": {"id": "T1"}}, timeout_min=5,
    )  # fmt: skip


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    async def fake_run(self: DockerCliLauncher, *args: str) -> str:
        calls.append(list(args))
        if args[0] == "run":
            return "abc123"
        return json.dumps({"Running": True, "ExitCode": 0})

    monkeypatch.setattr(DockerCliLauncher, "_run", fake_run)
    return calls


def _flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


# (a) REPO_ROOT 밖 로컬 경로 → 그 경로도 -v 로; 안이면 root 하나만
async def test_repo_outside_root_is_mounted(recorded: list[list[str]]) -> None:
    root = "/srv/repos"
    launcher = DockerCliLauncher(redis_url="redis://h:6379/0", mounts=[(root, root)])
    await launcher.launch(spec("/home/me/proj"))
    argv = recorded[0]
    assert _flag_values(argv, "-v") == [f"{root}:{root}", "/home/me/proj:/home/me/proj"]
    recorded.clear()
    await launcher.launch(spec("/srv/repos/org/demo"))
    assert _flag_values(recorded[0], "-v") == [f"{root}:{root}"]
    recorded.clear()
    await launcher.launch(spec("https://github.com/org/demo.git"))  # URL은 마운트 없음
    assert _flag_values(recorded[0], "-v") == [f"{root}:{root}"]


# (b) 기본은 호스트 uid:gid + HOME=/tmp/worker-home + WORKER_WORKDIR=/tmp/work; user=None이면 생략
async def test_runs_as_host_uid_by_default(recorded: list[list[str]]) -> None:
    launcher = DockerCliLauncher(redis_url="redis://h:6379/0")
    await launcher.launch(spec("/tmp/x"))
    argv = recorded[0]
    assert _flag_values(argv, "--user") == [f"{os.getuid()}:{os.getgid()}"]
    envs = _flag_values(argv, "-e")
    assert "HOME=/tmp/worker-home" in envs and "WORKER_WORKDIR=/tmp/work" in envs
    recorded.clear()
    await DockerCliLauncher(redis_url="redis://h:6379/0", user=None).launch(spec("/tmp/x"))
    argv = recorded[0]
    assert "--user" not in argv and "HOME=/tmp/worker-home" not in _flag_values(argv, "-e")


def test_launcher_settings_default_user() -> None:
    from control_plane.runtime import build_launcher
    from control_plane.config import Settings

    class R:  # redis 자리
        pass

    launcher: Any = build_launcher(Settings(_env_file=None, worker_launcher="docker"), R())
    assert launcher.user == f"{os.getuid()}:{os.getgid()}"
