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
        min_tasks=1,  # X.2: DECOMPOSE_JSON은 Task 2개
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


# P9 (D-46 확장): 프로젝트 생성 직후(projection 전) Goal을 만들어도 runner가 project.created 이벤트로 repo를 읽는다
async def test_goal_started_before_project_projection(
    client: httpx.AsyncClient, pump: Pump, runner: GoalRunner
) -> None:
    r = await client.post("/projects", json={"name": "demo", "repo": "org/demo"})
    pid = str(r.json()["id"])
    r = await client.post(f"/projects/{pid}/goals", json={"title": "Users API"})
    assert r.status_code == 202, r.text
    gid = str(r.json()["id"])
    await runner.wait_idle()
    assert runner.errors.get(gid) is None, runner.errors
    assert runner.is_waiting(gid)


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


# ---------------------------------------------------------------- P6.2 승인 대기 복원 (리뷰 A6)
def make_runner(
    factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    checkpointer: MemorySaver,
    script: list[Any],
) -> GoalRunner:
    return GoalRunner(
        factory=factory,
        bus=EventBus(redis),
        provider=FakeProvider(script=script),
        github=DryRunGitHubClient(),
        discussions=DryRunDiscussionsClient(),
        checkpointer=checkpointer,
        repo_path_for=lambda repo: SAMPLE,
        min_tasks=1,
    )


# (a)(b)(d) 같은 checkpointer의 새 GoalRunner가 startup()에서 대기 목록을 복원하고 /approve로 재개
async def test_restart_restores_waiting_goals(
    factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    pump: Pump,
) -> None:
    saver = MemorySaver()
    runner1 = make_runner(factory, redis, saver, [PLAN_JSON, DECOMPOSE_JSON])
    app1 = create_app(
        Settings(_env_file=None, github_webhook_secret=SECRET),
        factory=factory,
        redis=redis,
        runner=runner1,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app1), base_url="http://t"
    ) as c1:
        pid, gid = await start_goal(c1, pump, runner1)
        g = (await c1.get(f"/projects/{pid}/goals/{gid}")).json()
    assert runner1.is_waiting(gid) and g["status"] == "awaiting_plan_approval"
    # "재시작": 새 runner는 _run을 돌린 적이 없다. 체크포인트(saver)와 projection만 남아 있다
    runner2 = make_runner(factory, redis, saver, [DECOMPOSE_JSON])
    assert not runner2.is_waiting(gid)
    await runner2.startup("sqlite+aiosqlite://")
    assert runner2.is_waiting(gid)
    w = runner2.waiting(pid)[gid]
    assert w.plan_discussion_number == g["plan_discussion_number"] and w.plan_revision == 1
    app2 = create_app(
        Settings(_env_file=None, github_webhook_secret=SECRET),
        factory=factory,
        redis=redis,
        runner=runner2,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app2), base_url="http://t"
    ) as c2:
        body = comment_body("/approve")
        r = await c2.post("/webhooks/github", content=body, headers=signed(body, "d-restart"))
        assert r.status_code == 202, r.text
        await runner2.wait_idle()
        await pump()
        assert not runner2.is_waiting(gid) and runner2.errors == {}
        g = (await c2.get(f"/projects/{pid}/goals/{gid}")).json()
    assert g["status"] == "active" and g["tasks"] == {"ready": 2}
    assert len(await events_of(factory, "task.created")) == 2


# (c) 체크포인트 스레드가 없는 Goal(다른 saver)은 복원하지 않는다
async def test_restore_skips_goals_without_checkpoint(
    factory: async_sessionmaker[AsyncSession], redis: Redis, pump: Pump
) -> None:
    runner1 = make_runner(factory, redis, MemorySaver(), [PLAN_JSON, DECOMPOSE_JSON])
    app1 = create_app(
        Settings(_env_file=None, github_webhook_secret=SECRET),
        factory=factory,
        redis=redis,
        runner=runner1,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app1), base_url="http://t"
    ) as c1:
        _, gid = await start_goal(c1, pump, runner1)
    runner3 = make_runner(factory, redis, MemorySaver(), [DECOMPOSE_JSON])  # 다른(빈) saver
    await runner3.startup("sqlite+aiosqlite://")
    assert not runner3.is_waiting(gid)
    assert runner3.restore_skipped == [gid]


# ------------------------------------------------ P6.4 Discussion 번호로 승인 매칭 (리뷰 A5)
def discussion_body(text: str, number: int, author: str = "alice") -> bytes:
    payload = json.loads((FIXTURES / "discussion_comment_approve.json").read_text())
    payload["comment"]["body"] = text
    payload["comment"]["user"]["login"] = author
    payload["sender"]["login"] = author
    payload["discussion"]["number"] = number
    return json.dumps(payload).encode()


def signed_discussion(body: bytes, delivery: str) -> dict[str, str]:
    return signed(body, delivery) | {"X-GitHub-Event": "discussion_comment"}


async def test_discussion_approve_matches_plan_discussion_number(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid, gid = await start_goal(client, pump, runner)
    number = runner.waiting(pid)[gid].plan_discussion_number
    assert number is not None
    # 다른 Discussion 번호 → 대기 Goal 매칭 없음 → no-op 202, 여전히 대기
    body = discussion_body("/approve", number + 100)
    r = await client.post(
        "/webhooks/github", content=body, headers=signed_discussion(body, "d-dc-other")
    )
    assert r.status_code == 202 and runner.is_waiting(gid)
    # Plan Discussion 번호 → resume
    body = discussion_body("/approve", number)
    r = await client.post(
        "/webhooks/github", content=body, headers=signed_discussion(body, "d-dc-match")
    )
    assert r.status_code == 202, r.text
    await runner.wait_idle()
    await pump()
    assert not runner.is_waiting(gid)
    assert (await client.get(f"/projects/{pid}/goals/{gid}")).json()["status"] == "active"
    assert len(await events_of(factory, "task.created")) == 2


async def test_discussion_reject_viewer_forbidden(
    client: httpx.AsyncClient, pump: Pump, runner: GoalRunner
) -> None:
    members = [{"user_id": "alice", "role": "viewer"}, {"user_id": "carol", "role": "owner"}]
    pid, gid = await start_goal(client, pump, runner, members=members)
    number = runner.waiting(pid)[gid].plan_discussion_number or 0
    body = discussion_body("/reject nope", number, author="alice")
    r = await client.post(
        "/webhooks/github", content=body, headers=signed_discussion(body, "d-dc-viewer")
    )
    assert r.status_code == 403 and runner.is_waiting(gid)


# ------------------------------------------------ P6.6 repo 확보 실패 (D-38) → Goal 종료 이벤트
async def test_repo_unavailable_blocks_goal(
    factory: async_sessionmaker[AsyncSession], redis: Redis, pump: Pump
) -> None:
    from control_plane.repo_cache import RepoUnavailable

    def boom(repo: str) -> Path:
        raise RepoUnavailable(repo, "clone failed: no such remote")

    runner = GoalRunner(
        factory=factory,
        bus=EventBus(redis),
        provider=FakeProvider(script=[PLAN_JSON, DECOMPOSE_JSON]),
        github=DryRunGitHubClient(),
        discussions=DryRunDiscussionsClient(),
        checkpointer=MemorySaver(),
        repo_path_for=boom,
        min_tasks=1,
    )
    app1 = create_app(
        Settings(_env_file=None, github_webhook_secret=SECRET),
        factory=factory,
        redis=redis,
        runner=runner,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1), base_url="http://t") as c:
        pid, gid = await start_goal(c, pump, runner)
        g = (await c.get(f"/projects/{pid}/goals/{gid}")).json()
    # §6.1: draft→blocked는 없다 → Goal은 취소로 끝나고 사유를 남긴다 (P6.6 기록)
    assert g["status"] == "cancelled"
    ev = (await events_of(factory, "goal.cancelled"))[-1]
    assert ev.payload["reason"].startswith("repo_unavailable") and ev.payload["by"] == "system"
    assert not runner.is_waiting(gid) and await events_of(factory, "goal.plan_proposed") == []


# ------------------------------------ PC-7 발견: API의 GoalRunner도 토큰으로 clone 해야 한다 (D-41)
async def test_runner_warms_token_before_repo_clone(
    factory: async_sessionmaker[AsyncSession], redis: Redis, pump: Pump
) -> None:
    calls: list[str] = []

    class Provider:
        async def token(self) -> str:
            calls.append("token")
            return "ghs_x"

        def token_nowait(self) -> str:
            return "ghs_x"

    def repo_path_for(repo: str) -> Path:
        calls.append(f"clone:{repo}")
        return SAMPLE

    runner = GoalRunner(
        factory=factory,
        bus=EventBus(redis),
        provider=FakeProvider(script=[PLAN_JSON, DECOMPOSE_JSON]),
        github=DryRunGitHubClient(),
        discussions=DryRunDiscussionsClient(),
        checkpointer=MemorySaver(),
        repo_path_for=repo_path_for,
        token_provider=Provider(),
        min_tasks=1,
    )
    app1 = create_app(
        Settings(_env_file=None, github_webhook_secret=SECRET),
        factory=factory,
        redis=redis,
        runner=runner,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1), base_url="http://t") as c:
        _, gid = await start_goal(c, pump, runner)
    assert calls[:2] == ["token", "clone:org/demo"] and runner.is_waiting(gid)


def test_build_runner_real_mode_uses_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """dry_run=false면 build_runner가 토큰 제공자를 만들어 RepoCache와 runner에 준다."""
    from control_plane.api import app as app_mod
    from control_plane.api.deps import build_state

    class Provider:
        def token_nowait(self) -> str:
            return "ghs_x"

        async def token(self) -> str:
            return "ghs_x"

    monkeypatch.setattr(app_mod, "make_token_provider", lambda settings: Provider())
    settings = Settings(
        _env_file=None,
        dry_run=False,
        llm_provider="openai_compat",
        github_app_id="1",
        github_app_private_key="pem",
        github_installation_id=1,
    )
    monkeypatch.setattr(app_mod, "get_github_client", lambda s: DryRunGitHubClient())
    monkeypatch.setattr(app_mod, "get_discussions_client", lambda s: DryRunDiscussionsClient())
    runner = app_mod.build_runner(settings, build_state(settings))
    assert runner.token_provider is not None
    assert runner.repo_cache is not None and runner.repo_cache.url_for("org/demo").startswith(
        "https://x-access-token:ghs_x@"
    )


# ------------------------ P8.4 (D-51): API로 Plan 승인/거절 — 웹훅·터널 없이 (리포트 #6)
async def test_api_approve_endpoint(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid, gid = await start_goal(client, pump, runner)
    r = await client.post(f"/projects/{pid}/goals/{gid}/approve", headers={"X-User-Id": "mallory"})
    assert r.status_code == 403 and runner.is_waiting(gid)
    r = await client.post(f"/projects/{pid}/goals/{gid}/approve", headers={"X-User-Id": "alice"})
    assert r.status_code == 202, r.text
    await runner.wait_idle()
    await pump()
    assert not runner.is_waiting(gid)
    assert (await client.get(f"/projects/{pid}/goals/{gid}")).json()["status"] == "active"
    assert (await events_of(factory, "goal.activated"))[-1].payload["by"] == "alice"
    # 대기 중이 아니면 409
    r = await client.post(f"/projects/{pid}/goals/{gid}/approve", headers={"X-User-Id": "alice"})
    assert r.status_code == 409


async def test_api_reject_endpoint(
    client: httpx.AsyncClient,
    pump: Pump,
    runner: GoalRunner,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid, gid = await start_goal(client, pump, runner)
    r = await client.post(
        f"/projects/{pid}/goals/{gid}/reject", json={"reason": "no"}, headers={"X-User-Id": "alice"}
    )
    assert r.status_code == 202
    await runner.wait_idle()
    await pump()
    assert (await client.get(f"/projects/{pid}/goals/{gid}")).json()["status"] == "cancelled"
    assert (await events_of(factory, "goal.cancelled"))[-1].payload == {
        "by": "alice",
        "reason": "no",
    }
