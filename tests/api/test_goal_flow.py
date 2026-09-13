"""P5.2 — Goal 실행 + 승인 재개 (red a~e): POST /goals → 백그라운드 Orchestrator → interrupt 대기 →
서명된 issue_comment 웹훅(/approve·/reject) → Command(resume). GitHub·LLM은 Dry/Fake."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.llm.fake import FakeProvider
from control_plane.api.app import create_app
from control_plane.config import Settings
from control_plane.events.bus import EventBus
from control_plane.orchestrator.runner import GoalRunner
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient
from tests.api.test_api import events_of
from tests.orchestrator.test_graph import DECOMPOSE_JSON, PLAN_JSON

SECRET = "s3cret"
SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_repo"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "webhooks"
Pump = Callable[[], Awaitable[list[str]]]


@pytest.fixture
def runner(factory: async_sessionmaker[AsyncSession], redis: Redis) -> GoalRunner:
    return GoalRunner(
        factory=factory,
        bus=EventBus(redis),
        provider=FakeProvider(script=[PLAN_JSON, DECOMPOSE_JSON]),
        github=DryRunGitHubClient(),
        discussions=DryRunDiscussionsClient(),
        checkpointer=MemorySaver(),
        repo_path_for=lambda repo: SAMPLE,  # D-11: 로컬 경로만
    )


@pytest.fixture
def app(factory: async_sessionmaker[AsyncSession], redis: Redis, runner: GoalRunner) -> Any:
    return create_app(
        Settings(_env_file=None, github_webhook_secret=SECRET),
        factory=factory,
        redis=redis,
        runner=runner,
    )


@pytest.fixture
async def client(app: Any) -> Any:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def comment_body(text: str = "/approve", author: str = "alice") -> bytes:
    payload = json.loads((FIXTURES / "issue_comment_approve.json").read_text())
    payload["comment"]["body"] = text
    payload["comment"]["user"]["login"] = author
    payload["sender"]["login"] = author
    return json.dumps(payload).encode()


def signed(body: bytes, delivery: str) -> dict[str, str]:
    return {
        "X-GitHub-Event": "issue_comment",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": "sha256="
        + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        "Content-Type": "application/json",
    }


async def start_goal(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    members: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    body: dict[str, Any] = {"name": "demo", "repo": "org/demo"}
    if members is not None:
        body["members"] = members
    r = await client.post("/projects", json=body, headers={"X-User-Id": "alice"})
    assert r.status_code == 201, r.text
    pid = str(r.json()["id"])
    await pump()
    r = await client.post(
        f"/projects/{pid}/goals", json={"title": "Users API", "description": "CRUD"}
    )
    assert r.status_code == 202, r.text
    gid = str(r.json()["id"])
    await runner.wait_idle()
    await pump()
    return pid, gid


# (a) POST /goals → 백그라운드 실행 → Plan Discussion(dry) → interrupt 대기, awaiting_plan_approval
async def test_goal_runs_until_plan_approval(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid, gid = await start_goal(client, pump, runner)
    assert runner.is_waiting(gid)
    g = (await client.get(f"/projects/{pid}/goals/{gid}")).json()
    assert g["status"] == "awaiting_plan_approval" and g["plan_revision"] == 1
    assert g["plan_discussion_number"] is not None
    proposed = await events_of(factory, "goal.plan_proposed")
    assert len(proposed) == 1 and proposed[0].correlation_id == gid
    assert proposed[0].causation_id is not None  # goal.created 뒤
    assert [e.type for e in await events_of(factory, "task.created")] == []


# (b) /approve (작성자 = owner) → resume → goal.activated → epic/task 생성
async def test_approve_resumes_and_creates_tasks(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid, gid = await start_goal(client, pump, runner)
    body = comment_body("/approve")
    r = await client.post("/webhooks/github", content=body, headers=signed(body, "d-approve"))
    assert r.status_code == 202, r.text
    await runner.wait_idle()
    await pump()
    assert not runner.is_waiting(gid)
    g = (await client.get(f"/projects/{pid}/goals/{gid}")).json()
    assert g["status"] == "active" and g["tasks"] == {"ready": 2}
    assert [e["title"] for e in g["epics"]] == ["Users API"]
    activated = await events_of(factory, "goal.activated")
    assert len(activated) == 1 and activated[0].payload["by"] == "alice"
    created = await events_of(factory, "task.created")
    assert [e.payload["title"] for e in created] == ["Add users route", "Test users route"]
    assert created[1].payload["depends_on"] == [created[0].subject_id]
    tasks = (await client.get(f"/projects/{pid}/tasks")).json()["items"]
    assert all(t["issue_number"] for t in tasks)  # dry-run이어도 번호는 준다


# (c) viewer → 403, resume 안 됨
async def test_viewer_forbidden(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    members = [{"user_id": "alice", "role": "viewer"}, {"user_id": "carol", "role": "owner"}]
    pid, gid = await start_goal(client, pump, runner, members=members)
    body = comment_body("/approve", author="alice")
    r = await client.post("/webhooks/github", content=body, headers=signed(body, "d-viewer"))
    assert r.status_code == 403, r.text
    await runner.wait_idle()
    assert runner.is_waiting(gid)
    assert await events_of(factory, "goal.activated") == []
    # 멤버가 아닌 사람도 403
    body = comment_body("/approve", author="mallory")
    r = await client.post("/webhooks/github", content=body, headers=signed(body, "d-mallory"))
    assert r.status_code == 403
    # approver는 된다
    body = comment_body("/approve", author="carol")
    r = await client.post("/webhooks/github", content=body, headers=signed(body, "d-carol"))
    assert r.status_code == 202
    await runner.wait_idle()
    assert not runner.is_waiting(gid)


# (d) /reject <reason> → goal.cancelled, Task 없음
async def test_reject_cancels_goal(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid, gid = await start_goal(client, pump, runner)
    body = comment_body("/reject too risky")
    r = await client.post("/webhooks/github", content=body, headers=signed(body, "d-reject"))
    assert r.status_code == 202
    await runner.wait_idle()
    await pump()
    g = (await client.get(f"/projects/{pid}/goals/{gid}")).json()
    assert g["status"] == "cancelled" and g["tasks"] == {}
    cancelled = await events_of(factory, "goal.cancelled")
    assert len(cancelled) == 1
    assert cancelled[0].payload == {"by": "alice", "reason": "too risky"}
    assert await events_of(factory, "task.created") == []


# (e) 같은 delivery 두 번 → 두 번째는 200 duplicate(P2.4 계약), resume 1회
async def test_same_delivery_twice_resumes_once(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    _, gid = await start_goal(client, pump, runner)
    body = comment_body("/approve")
    assert (
        await client.post("/webhooks/github", content=body, headers=signed(body, "d-1"))
    ).status_code == 202
    assert (
        await client.post("/webhooks/github", content=body, headers=signed(body, "d-1"))
    ).status_code == 200  # duplicate
    await runner.wait_idle()
    # 이미 재개된 뒤의 새 delivery도 무해 (대기 중인 Goal 없음 → no-op 202)
    assert (
        await client.post("/webhooks/github", content=body, headers=signed(body, "d-2"))
    ).status_code == 202
    await runner.wait_idle()
    await pump()
    assert len(await events_of(factory, "goal.activated")) == 1
    assert len(await events_of(factory, "task.created")) == 2
    assert not runner.is_waiting(gid)


# 모르는 repo → 204 (resolve_project None), 서명 불량 → 401
async def test_unknown_repo_and_bad_signature(client: httpx.AsyncClient) -> None:
    payload = json.loads((FIXTURES / "issue_comment_approve.json").read_text())
    payload["repository"]["full_name"] = "org/unknown"
    body = json.dumps(payload).encode()
    assert (
        await client.post("/webhooks/github", content=body, headers=signed(body, "d-u"))
    ).status_code == 204
    headers = signed(body, "d-bad") | {"X-Hub-Signature-256": "sha256=deadbeef"}
    assert (await client.post("/webhooks/github", content=body, headers=headers)).status_code == 401
