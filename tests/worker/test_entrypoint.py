"""P4.4 — worker entrypoint (red a~d): 환경변수만, exit code, 타임아웃, publish-file 모드."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from agents.llm.base import Completion, Message
from agents.llm.fake import FakeProvider
from control_plane.events.schema import Event
from tests.agents.conftest import git
from worker import entrypoint
from worker.publish import FilePublisher, stream_fields_for

SCRIPTS = Path(__file__).resolve().parent.parent / "fixtures" / "coding_scripts"


def task_json() -> str:
    return json.dumps(
        {
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
    )


def env_for(
    remote: Path, events_file: Path, script: str = "pass", timeout_min: str = "45"
) -> dict[str, str]:
    return {
        "WORKER_REPO_URL": str(remote),
        "WORKER_BRANCH": "ai/users-api/12-add-users-module",
        "WORKER_TASK_JSON": task_json(),
        "WORKER_REDIS_URL": "redis://localhost:6379/15",
        "WORKER_TOKEN": "",
        "WORKER_TIMEOUT_MIN": timeout_min,
        "WORKER_LLM_PROVIDER": "fake",
        "WORKER_FAKE_SCRIPT": str(SCRIPTS / f"{script}.json"),
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
    }


def read_events(path: Path) -> list[dict[str, Any]]:
    """JSON-lines → 이벤트 dict(canonical 복원 + signature)."""
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        event = json.loads(rec["canonical"])
        event["signature"] = rec["signature"] or None
        out.append(event)
    return out


# (a) 설정은 환경변수뿐 — 필수 누락 시 exit 64
def test_missing_env_is_usage_error(tmp_path: Path) -> None:
    code = entrypoint.main(["01TASK"], env={"PATH": os.environ["PATH"]})
    assert code == 64


def test_settings_from_env_only() -> None:
    src = (Path("worker") / "entrypoint.py").read_text(encoding="utf-8")
    assert "control_plane.config" not in src and "Settings(" not in src


# (b) clone → 브랜치 → CodingAgent.run → run.finished → exit 0
def test_done_exit_0(tmp_path: Path, worktree: Path, remote: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    code = entrypoint.main(
        ["01TASK", "--publish-file", str(events_file), "--workdir", str(tmp_path / "work")],
        env=env_for(remote, events_file),
    )
    assert code == 0
    types = [e["type"] for e in read_events(events_file)]
    assert types[:2] == ["task.started", "run.started"] and types[-1] == "run.finished"
    assert "pr.opened" in types and "task.completed" in types
    assert "ai/users-api/12-add-users-module" in git(remote, "branch", "--list")
    fin = read_events(events_file)[-1]
    assert fin["payload"]["outcome"] == "success" and fin["signature"] is None  # 서명은 ingest가


@pytest.mark.parametrize(("script", "expected"), [("fail3", 1), ("dependency", 2)])
def test_exit_codes(
    tmp_path: Path, worktree: Path, remote: Path, script: str, expected: int
) -> None:
    events_file = tmp_path / "events.jsonl"
    env = env_for(remote, events_file, script=script)
    if script == "dependency":
        t = json.loads(env["WORKER_TASK_JSON"])
        t["task"]["owned_paths"].append("pyproject.toml")
        env["WORKER_TASK_JSON"] = json.dumps(t)
    code = entrypoint.main(
        ["01TASK", "--publish-file", str(events_file), "--workdir", str(tmp_path / "work")], env=env
    )
    assert code == expected


# (c) 타임아웃 → WIP push, task.failed(timeout), run.finished(timeout), exit 3
class SlowProvider:
    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> Completion:
        await asyncio.sleep(5)
        return Completion(text="", parsed=None, tokens_in=0, tokens_out=0, model="slow")


def test_timeout_exit_3(tmp_path: Path, worktree: Path, remote: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    env = env_for(remote, events_file)
    env["WORKER_TIMEOUT_MIN"] = "0.02"  # 1.2초
    code = entrypoint.main(
        ["01TASK", "--publish-file", str(events_file), "--workdir", str(tmp_path / "work")],
        env=env,
        provider=SlowProvider(),
    )
    assert code == 3
    events = read_events(events_file)
    types = [e["type"] for e in events]
    assert types[0] == "task.started" and "task.failed" in types and types[-1] == "run.finished"
    failed = next(e for e in events if e["type"] == "task.failed")
    assert failed["payload"]["reason"] == "timeout" and failed["payload"]["attempt"] == 1
    fin = events[-1]["payload"]
    assert fin["outcome"] == "timeout" and fin["agent_outcome"] == "timeout"
    assert "ai/users-api/12-add-users-module" in git(remote, "branch", "--list")  # WIP push


# (d) publish: 파일 모드는 stream_fields 형식과 같은 필드를 JSON 줄로
async def test_file_publisher_matches_stream_fields(tmp_path: Path) -> None:
    from control_plane.events.schema import Actor, EventType, Subject

    e = Event(project_id="P1", actor=Actor(type="agent", id="w"), type=EventType.TASK_STARTED,
              subject=Subject(entity="task", id="T"), payload={"run_id": "R"}, correlation_id="G", causation_id=None)  # fmt: skip
    pub = FilePublisher(tmp_path / "ev.jsonl")
    out = await pub(e)
    assert out.id == e.id
    line = json.loads((tmp_path / "ev.jsonl").read_text().strip())
    fields = stream_fields_for(e)
    assert set(fields) == {"id", "type", "seq", "canonical", "signature"}
    assert line["id"] == e.id and line["type"] == "task.started" and line["stream"] == "events:P1"
    restored = Event.model_validate_json(fields["canonical"])
    assert restored.model_copy(update={"signature": None}) == e


class _Plan(BaseModel):
    x: int


def test_fake_provider_loads_script() -> None:
    p = entrypoint.provider_from_env(
        {"WORKER_LLM_PROVIDER": "fake", "WORKER_FAKE_SCRIPT": str(SCRIPTS / "pass.json")}
    )
    assert isinstance(p, FakeProvider) and len(p.script) == 3
    with pytest.raises(ValueError, match="WORKER_LLM_PROVIDER"):
        entrypoint.provider_from_env({"WORKER_LLM_PROVIDER": "bogus"})
