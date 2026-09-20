"""P5.1 — API (red a~i): 라우터, 커서, Idempotency-Key, 취소 cascade. 발행만, DB는 projection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.store import models as m

E = EventType
Pump = Callable[[], Awaitable[list[str]]]
Publish = Callable[..., Awaitable[list[Event]]]
REPO = "tests/fixtures/sample_repo"


async def events_of(factory: async_sessionmaker[AsyncSession], type_: str) -> list[m.Event]:
    async with factory() as s:
        rows = (
            await s.execute(select(m.Event).where(m.Event.type == type_).order_by(m.Event.seq))
        ).scalars()
        return list(rows.all())


async def make_project(client: httpx.AsyncClient, pump: Pump) -> str:
    r = await client.post("/projects", json={"name": "demo", "repo": REPO})
    assert r.status_code == 201, r.text
    await pump()
    return str(r.json()["id"])


async def make_goal(client: httpx.AsyncClient, pump: Pump, pid: str) -> str:
    r = await client.post(f"/projects/{pid}/goals", json={"title": "g", "description": "d"})
    assert r.status_code == 202, r.text
    await pump()
    return str(r.json()["id"])


def seed(pid: str, gid: str) -> list[Event]:
    """emit(P3.5) 대역: epic 1 + task 2 (ready)."""
    sys = Actor(type="system", id="orchestrator")

    def ev(type_: EventType, entity: Any, id_: str, payload: dict[str, Any]) -> Event:
        return Event(
            project_id=pid,
            actor=sys,
            type=type_,
            subject=Subject(entity=entity, id=id_),
            payload=payload,
            correlation_id=gid,
            causation_id=None,
        )

    task = {
        "epic_id": "E1",
        "epic_title": "Epic 1",
        "kind": "feature",
        "role_required": "coding",
        "depends_on": [],
        "risk_tier": "T1",
        "issue_url": None,
    }
    return [
        ev(E.GOAL_PLAN_PROPOSED, "goal", gid, {"plan_discussion_number": 1, "revision": 1}),
        ev(E.GOAL_ACTIVATED, "goal", gid, {}),
        ev(
            E.EPIC_CREATED,
            "epic",
            "E1",
            {"goal_id": gid, "title": "Epic 1", "order": 1, "milestone_number": 1},
        ),
        ev(
            E.TASK_CREATED,
            "task",
            "T1",
            {**task, "title": "t1", "spec": "s1", "owned_paths": ["a/**"], "issue_number": 1},
        ),
        ev(
            E.TASK_CREATED,
            "task",
            "T2",
            {**task, "title": "t2", "spec": "s2", "owned_paths": ["b/**"], "issue_number": 2},
        ),
    ]


# (h)
async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


# (a)(b) project.created는 루트 이벤트: correlation=project_id, causation None (D-25), actor human
async def test_create_and_get_project(
    client: httpx.AsyncClient, pump: Pump, factory: async_sessionmaker[AsyncSession]
) -> None:
    r = await client.post(
        "/projects", json={"name": "demo", "repo": REPO}, headers={"X-User-Id": "alice"}
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    assert len(pid) == 26 and r.json()["name"] == "demo"
    created = await events_of(factory, "project.created")
    assert len(created) == 1
    e = created[0]
    assert e.subject_id == pid and e.correlation_id == pid and e.causation_id is None
    assert e.actor_type == "human" and e.actor_id == "alice"
    assert e.payload["repo"] == REPO and e.payload["default_branch"] == "main"
    # D-46 (P8.4): projection 전에도 events 폴백으로 200 — 이전엔 404
    assert (await client.get(f"/projects/{pid}")).status_code == 200
    await pump()
    got = await client.get(f"/projects/{pid}")
    assert got.status_code == 200
    body = got.json()
    assert body["id"] == pid and body["repo"] == REPO and body["default_branch"] == "main"
    assert (await client.get("/projects/01UNKNOWN00000000000000000")).status_code == 404


# (c)(d) goal.created 루트(correlation=goal_id), 202; 진행률 = Task 상태 카운트 + Epic
async def test_create_goal_and_progress(
    client: httpx.AsyncClient,
    pump: Pump,
    publish: Publish,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid = await make_project(client, pump)
    r = await client.post(f"/projects/{pid}/goals", json={"title": "Add users", "description": "d"})
    assert r.status_code == 202
    gid = r.json()["id"]
    created = await events_of(factory, "goal.created")
    assert len(created) == 1 and created[0].correlation_id == gid
    assert created[0].causation_id is None and created[0].project_id == pid
    assert (
        await client.post("/projects/01UNKNOWN00000000000000000/goals", json={"title": "x"})
    ).status_code == 404
    await pump()
    g = (await client.get(f"/projects/{pid}/goals/{gid}")).json()
    assert g["status"] == "draft" and g["tasks"] == {} and g["epics"] == []
    await publish(*seed(pid, gid))
    await pump()
    g = (await client.get(f"/projects/{pid}/goals/{gid}")).json()
    assert g["status"] == "active" and g["tasks"] == {"ready": 2}
    assert g["total"] == 2 and g["done"] == 0
    assert [e["title"] for e in g["epics"]] == ["Epic 1"] and g["epics"][0]["status"] == "pending"
    assert (await client.get(f"/projects/{pid}/goals/01NOPE0000000000000000000")).status_code == 404


# (e)
async def test_list_tasks_filters(client: httpx.AsyncClient, pump: Pump, publish: Publish) -> None:
    pid = await make_project(client, pump)
    gid = await make_goal(client, pump, pid)
    await publish(*seed(pid, gid))
    await pump()
    all_ = (await client.get(f"/projects/{pid}/tasks")).json()
    assert [t["id"] for t in all_["items"]] == ["T1", "T2"]
    assert all_["items"][0]["status"] == "ready" and all_["items"][0]["epic_id"] == "E1"
    assert (
        len(
            (await client.get(f"/projects/{pid}/tasks", params={"status": "ready"})).json()["items"]
        )
        == 2
    )
    assert (await client.get(f"/projects/{pid}/tasks", params={"status": "done"})).json()[
        "items"
    ] == []
    assert (
        len((await client.get(f"/projects/{pid}/tasks", params={"epic": "E1"})).json()["items"])
        == 2
    )
    assert (await client.get(f"/projects/{pid}/tasks", params={"epic": "E9"})).json()["items"] == []
    assert (
        await client.get(f"/projects/{pid}/tasks", params={"status": "bogus"})
    ).status_code == 422


# (f) 커서는 seq (D-29). since 이후만, type 필터, next_since
async def test_events_cursor(client: httpx.AsyncClient, pump: Pump, publish: Publish) -> None:
    pid = await make_project(client, pump)
    gid = await make_goal(client, pump, pid)
    await publish(*seed(pid, gid))
    await pump()
    page = (await client.get(f"/projects/{pid}/events", params={"since": 0})).json()
    types = [e["type"] for e in page["items"]]
    assert types[:2] == ["project.created", "goal.created"] and types[-1] == "task.created"
    seqs = [e["seq"] for e in page["items"]]
    assert seqs == sorted(seqs) and page["next_since"] == seqs[-1]
    assert set(page["items"][0]) >= {
        "seq",
        "id",
        "ts",
        "type",
        "actor",
        "subject",
        "payload",
        "correlation_id",
        "causation_id",
    }
    rest = (await client.get(f"/projects/{pid}/events", params={"since": seqs[1]})).json()
    assert [e["seq"] for e in rest["items"]] == seqs[2:]
    tc = (await client.get(f"/projects/{pid}/events", params={"type": "task.created"})).json()
    assert [e["subject"]["id"] for e in tc["items"]] == ["T1", "T2"]
    two = (await client.get(f"/projects/{pid}/events", params={"limit": 2})).json()
    assert len(two["items"]) == 2 and two["next_since"] == seqs[1]
    # 다른 project의 이벤트는 안 보인다
    r = await client.post(
        "/projects", json={"name": "other", "repo": "org/other"}
    )  # D-45: repo는 유일
    assert r.status_code == 201
    other = str(r.json()["id"])
    await pump()
    assert all(
        e["subject"]["id"] != other
        for e in (await client.get(f"/projects/{pid}/events")).json()["items"]
    )
    assert (await client.get(f"/projects/{pid}/events", params={"since": "abc"})).status_code == 422


# (g) Idempotency-Key: 같은 키+같은 본문 → 같은 응답, publish 1회; 같은 키+다른 본문 → 422
async def test_idempotency_key(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    body = {"name": "idem", "repo": REPO}
    h = {"Idempotency-Key": "k-1"}
    r1 = await client.post("/projects", json=body, headers=h)
    r2 = await client.post("/projects", json=body, headers=h)
    assert r1.status_code == r2.status_code == 201 and r1.json() == r2.json()
    assert len(await events_of(factory, "project.created")) == 1
    r3 = await client.post("/projects", json={"name": "other", "repo": REPO}, headers=h)
    assert r3.status_code == 422
    r4 = await client.post(
        "/projects", json={**body, "repo": "org/second"}
    )  # 키 없으면 새 자원 (D-45: repo 유일)
    assert r4.status_code == 201 and r4.json()["id"] != r1.json()["id"]
    assert len(await events_of(factory, "project.created")) == 2


# (i) 취소: PATCH task → task.cancelled; POST goal cancel → goal.cancelled + 미완 Task마다 cascade
async def test_cancel_task_and_goal(
    client: httpx.AsyncClient,
    pump: Pump,
    publish: Publish,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid = await make_project(client, pump)
    gid = await make_goal(client, pump, pid)
    await publish(*seed(pid, gid))
    await pump()
    r = await client.patch(
        f"/projects/{pid}/tasks/T1",
        json={"action": "cancel", "reason": "dup"},
        headers={"X-User-Id": "bob"},
    )
    assert r.status_code == 202, r.text
    tc = await events_of(factory, "task.cancelled")
    assert len(tc) == 1 and tc[0].subject_id == "T1" and tc[0].correlation_id == gid
    assert tc[0].payload == {"reason": "dup", "by": "bob", "cascade_from": None}
    assert (
        await client.patch(f"/projects/{pid}/tasks/T1", json={"action": "bogus"})
    ).status_code == 422
    assert (
        await client.patch(f"/projects/{pid}/tasks/T9", json={"action": "cancel"})
    ).status_code == 404
    await pump()
    assert (await client.get(f"/projects/{pid}/goals/{gid}")).json()["tasks"] == {
        "ready": 1,
        "cancelled": 1,
    }
    r = await client.post(
        f"/projects/{pid}/goals/{gid}/cancel",
        json={"reason": "scope"},
        headers={"X-User-Id": "bob"},
    )
    assert r.status_code == 202, r.text
    gc = await events_of(factory, "goal.cancelled")
    assert len(gc) == 1 and gc[0].payload == {"reason": "scope", "by": "bob"}
    tc = await events_of(factory, "task.cancelled")
    assert [t.subject_id for t in tc] == ["T1", "T2"]  # 이미 취소된 T1은 다시 안 보냄
    assert tc[1].payload["cascade_from"] == gid and tc[1].causation_id == gc[0].id
    await pump()
    g = (await client.get(f"/projects/{pid}/goals/{gid}")).json()
    assert g["status"] == "cancelled" and g["tasks"] == {"cancelled": 2}
    assert (
        await client.post(f"/projects/{pid}/goals/01NOPE0000000000000000000/cancel", json={})
    ).status_code == 404


# ---------------------------------------------------------------- P8.4 (D-45, D-46, F-6, F-7, F-8)
# (a) 같은 repo 두 번 → 409 (projection 전이어도 events로 판단)
async def test_duplicate_repo_is_409(client: httpx.AsyncClient, pump: Pump) -> None:
    r1 = await client.post("/projects", json={"name": "a", "repo": REPO})
    assert r1.status_code == 201
    r2 = await client.post("/projects", json={"name": "b", "repo": REPO})  # pump 없이
    assert r2.status_code == 409 and "already" in r2.json()["detail"]
    await pump()
    assert (await client.post("/projects", json={"name": "c", "repo": REPO})).status_code == 409


# (b) 201 직후 pump 없이 POST /goals 202, GET /projects/{id} 200 (events 폴백)
async def test_read_after_write_without_projection(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/projects", json={"name": "demo", "repo": REPO}, headers={"X-User-Id": "alice"}
    )
    pid = r.json()["id"]
    got = await client.get(f"/projects/{pid}")
    assert got.status_code == 200 and got.json()["repo"] == REPO and got.json()["name"] == "demo"
    g = await client.post(f"/projects/{pid}/goals", json={"title": "g", "description": "d"})
    assert g.status_code == 202
    assert (await client.get("/projects/01UNKNOWN00000000000000000")).status_code == 404


# (c) 존재하지 않는 로컬 경로 → 400; owner/name·URL은 허용
async def test_repo_validation(client: httpx.AsyncClient, tmp_path: Path) -> None:
    bad = await client.post("/projects", json={"name": "x", "repo": "/path/to/repo"})
    assert bad.status_code == 400 and "does not exist" in bad.json()["detail"]
    assert (
        await client.post("/projects", json={"name": "y", "repo": "org/demo"})
    ).status_code == 201
    assert (
        await client.post("/projects", json={"name": "z", "repo": "https://github.com/o/r.git"})
    ).status_code == 201
    local = tmp_path / "repo"
    local.mkdir()
    assert (
        await client.post("/projects", json={"name": "w", "repo": str(local)})
    ).status_code == 201
    assert (
        await client.post("/projects", json={"name": "v", "repo": "not a repo"})
    ).status_code == 400


# (d) GET /projects 목록 (projection 기준)
async def test_list_projects(client: httpx.AsyncClient, pump: Pump) -> None:
    await client.post("/projects", json={"name": "one", "repo": REPO})
    await pump()
    items = (await client.get("/projects")).json()["items"]
    assert [p["name"] for p in items] == ["one"] and items[0]["repo"] == REPO


# P9.1 (D-52/D-53): Goal 목록, plan_markdown + Discussion 링크, Task spec + Issue/PR 링크, repo_url
async def test_goal_list_plan_markdown_and_links(
    client: httpx.AsyncClient, pump: Pump, publish: Publish
) -> None:
    r = await client.post("/projects", json={"name": "demo", "repo": "acme/demo"})
    assert r.status_code == 201
    pid = r.json()["id"]
    await pump()
    assert (await client.get(f"/projects/{pid}")).json()[
        "repo_url"
    ] == "https://github.com/acme/demo"
    assert (await client.get("/projects")).json()["items"][0]["repo_url"] == (
        "https://github.com/acme/demo"
    )
    gid = await make_goal(client, pump, pid)
    events = seed(pid, gid)
    events[0] = events[0].model_copy(
        update={"payload": {**events[0].payload, "plan_markdown": "# Plan\n\n- step"}}
    )
    await publish(*events)
    await pump()
    g = (await client.get(f"/projects/{pid}/goals/{gid}")).json()
    assert g["plan_markdown"] == "# Plan\n\n- step"
    assert g["plan_discussion_url"] == "https://github.com/acme/demo/discussions/1"

    gid2 = await make_goal(client, pump, pid)  # 나중 것이 앞 (created_at desc)
    lst = (await client.get(f"/projects/{pid}/goals")).json()["items"]
    assert [x["id"] for x in lst] == [gid2, gid]
    first = lst[1]
    assert first["title"] == "g" and first["status"] == "active"
    assert first["plan_discussion_number"] == 1 and first["plan_revision"] == 1
    assert first["total"] == 2 and first["done"] == 0 and "created_at" in first
    assert lst[0]["status"] == "draft" and lst[0]["total"] == 0

    tasks = {t["id"]: t for t in (await client.get(f"/projects/{pid}/tasks")).json()["items"]}
    assert tasks["T1"]["spec"] == "s1"
    assert tasks["T1"]["issue_url"] == "https://github.com/acme/demo/issues/1"
    assert tasks["T1"]["pr_url"] is None
    await publish(
        Event(
            project_id=pid,
            actor=Actor(type="system", id="pr-opener"),
            type=E.PR_OPENED,
            subject=Subject(entity="pr", id="7"),
            payload={
                "task_id": "T1",
                "run_id": "R1",
                "pr_number": 7,
                "head": "ai/x",
                "base": "main",
            },
            correlation_id=gid,
            causation_id=None,
        )
    )
    await pump()
    t1 = next(
        t for t in (await client.get(f"/projects/{pid}/tasks")).json()["items"] if t["id"] == "T1"
    )
    assert t1["pr_url"] == "https://github.com/acme/demo/pull/7"

    # 로컬 경로 repo면 링크 None
    r = await client.post("/projects", json={"name": "local", "repo": REPO})
    assert r.status_code == 201
    assert r.json()["repo_url"] is None
    assert (
        await client.get(f"/projects/{pid}/goals/00000000000000000000000000")
    ).status_code == 404


# P9 (콘솔 repo 연결): GET /projects/check?repo=owner/name — 읽기 전용 App 점검을 콘솔에서 미리 본다
async def test_repo_check_dry_and_bad_format(client: httpx.AsyncClient) -> None:
    r = await client.get("/projects/check", params={"repo": "acme/demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["repo"] == "acme/demo" and body["dry_run"] is True and body["ok"] is False
    assert body["items"][0]["name"] == "dry_run" and "HITL_DRY_RUN" in body["items"][0]["detail"]
    assert (await client.get("/projects/check", params={"repo": "not a repo"})).status_code == 400
    assert (await client.get("/projects/check", params={"repo": REPO})).status_code == 400


async def test_repo_check_real_mode_runs_app_check(
    factory: async_sessionmaker[AsyncSession], redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    from control_plane.api import projects as projects_mod
    from control_plane.api.app import create_app
    from control_plane.config import Settings
    from github_adapter.app_check import CheckReport

    seen: list[str] = []

    async def fake_run_check(settings: Any, http: Any, *, repo: str) -> CheckReport:
        seen.append(repo)
        rep = CheckReport()
        rep.add("auth", True, "app ok")
        rep.add("discussions", False, "category 'Plans' not found")
        rep.canonical = "Acme/Demo"  # 인계서 #4: GitHub의 정식 이름
        return rep

    class FakeHttp:
        async def __aenter__(self) -> FakeHttp:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

    monkeypatch.setattr(projects_mod, "run_check", fake_run_check)
    monkeypatch.setattr(projects_mod, "github_http", lambda: FakeHttp())
    app1 = create_app(Settings(_env_file=None, dry_run=False), factory=factory, redis=redis)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1), base_url="http://t") as c:
        r = await c.get("/projects/check", params={"repo": "acme/demo"})
    assert r.status_code == 200 and seen == ["acme/demo"]
    body = r.json()
    assert body["dry_run"] is False and body["ok"] is False
    assert [(i["name"], i["ok"]) for i in body["items"]] == [("auth", True), ("discussions", False)]
    assert body["canonical"] == "Acme/Demo"  # 콘솔은 이 이름으로 연결한다


# 인계서 2026-09-18 #4: 같은 repo를 대소문자만 바꿔 두 번 연결할 수 없다 (D-45 비교도 같은 기준)
async def test_repo_taken_ignores_case_for_owner_name(
    client: httpx.AsyncClient, pump: Pump
) -> None:
    assert (
        await client.post("/projects", json={"name": "a", "repo": "Org/Demo"})
    ).status_code == 201
    r = await client.post("/projects", json={"name": "b", "repo": "org/demo"})  # projection 전
    assert r.status_code == 409
    await pump()
    r = await client.post("/projects", json={"name": "b", "repo": "ORG/DEMO"})  # projection 후
    assert r.status_code == 409


# P9 (D-54): DELETE /projects/{id} = 보관. Goal·Task 취소 → project.updated{archived} → 제외,
# 새 Goal 409, 같은 repo로 다시 연결 가능. 이벤트·GitHub 산출물은 그대로
async def test_delete_project_archives_and_cancels(
    client: httpx.AsyncClient,
    pump: Pump,
    publish: Publish,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pid = await make_project(client, pump)
    gid = await make_goal(client, pump, pid)
    await publish(*seed(pid, gid))
    await pump()
    r = await client.delete(f"/projects/{pid}", headers={"X-User-Id": "alice"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["id"] == pid and body["cancelled_goals"] == [gid]
    cancelled = await events_of(factory, "goal.cancelled")
    assert [e.subject_id for e in cancelled] == [gid]
    assert cancelled[0].payload["by"] == "alice" and "deleted" in cancelled[0].payload["reason"]
    assert sorted(e.subject_id for e in await events_of(factory, "task.cancelled")) == ["T1", "T2"]
    upd = await events_of(factory, "project.updated")
    assert len(upd) == 1 and upd[0].payload == {"archived": True, "by": "alice"}
    assert upd[0].subject_id == pid and upd[0].actor_id == "alice"
    await pump()
    assert [p["id"] for p in (await client.get("/projects")).json()["items"]] == []
    listed = (await client.get("/projects", params={"include_archived": "true"})).json()["items"]
    assert [p["id"] for p in listed] == [pid] and listed[0]["archived_at"] is not None
    got = await client.get(f"/projects/{pid}")
    assert got.status_code == 200 and got.json()["archived_at"] is not None
    r = await client.post(f"/projects/{pid}/goals", json={"title": "again"})
    # P9.19: 사람이 읽는 메시지는 deleted (이벤트 필드 archived는 그대로)
    assert r.status_code == 409 and "deleted" in r.json()["detail"]
    # D-45의 repo 유일성은 삭제된 프로젝트를 세지 않는다
    r = await client.post("/projects", json={"name": "demo2", "repo": REPO})
    assert r.status_code == 201, r.text
    # 두 번째 DELETE는 409 (이미 삭제됨)
    again = await client.delete(f"/projects/{pid}")
    assert again.status_code == 409 and "already deleted" in again.json()["detail"]
    assert (await client.delete("/projects/01UNKNOWN00000000000000000")).status_code == 404


# P9 LLM 프로파일 (D-57): GET /llm, POST goals {llm} → 이벤트·목록·상세; 없는/키 없는 프로파일 400
async def test_llm_profiles_endpoint_and_goal_llm(
    factory: async_sessionmaker[AsyncSession], redis: Redis, pump: Pump
) -> None:
    from control_plane.api.app import create_app
    from control_plane.config import Settings

    async def ok_probe(profile: str) -> None:  # 프로브는 아래 테스트에서 따로 검증
        return None

    app1 = create_app(
        Settings(_env_file=None, llm_provider="openai_compat", openai_api_key="sk-x"),
        factory=factory,
        redis=redis,
        llm_probe=ok_probe,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1), base_url="http://t") as c:
        info = (await c.get("/llm")).json()
        assert info["default"] == "openai"  # P9.15: 키가 있으면 openai가 기본
        by = {p["name"]: p for p in info["profiles"]}
        assert by["openai"]["available"] is True and by["anthropic"]["available"] is False
        assert "model" in by["ollama"] and "api_key" not in str(info)
        pid = (await c.post("/projects", json={"name": "d", "repo": REPO})).json()["id"]
        await pump()
        r = await c.post(f"/projects/{pid}/goals", json={"title": "g", "llm": "openai"})
        assert r.status_code == 202, r.text
        gid = r.json()["id"]
        created = await events_of(factory, "goal.created")
        assert created[-1].payload["llm"] == "openai"
        await pump()
        assert (await c.get(f"/projects/{pid}/goals/{gid}")).json()["llm"] == "openai"
        assert (await c.get(f"/projects/{pid}/goals")).json()["items"][0]["llm"] == "openai"
        assert (
            await c.post(f"/projects/{pid}/goals", json={"title": "g", "llm": "nope"})
        ).status_code == 400
        r = await c.post(f"/projects/{pid}/goals", json={"title": "g", "llm": "anthropic"})
        assert r.status_code == 400 and "not available" in r.json()["detail"]
        r = await c.post(f"/projects/{pid}/goals", json={"title": "g"})  # 생략 → 기본 프로파일
        assert r.status_code == 202
        assert (await events_of(factory, "goal.created"))[-1].payload.get("llm") == "openai"
    # HITL_LLM_DEFAULT_PROFILE로 기본값을 되돌릴 수 있다 (P9.15)
    app2 = create_app(
        Settings(
            _env_file=None,
            llm_provider="openai_compat",
            openai_api_key="sk-x",
            llm_default_profile="ollama",
        ),
        factory=factory,
        redis=redis,
        llm_probe=ok_probe,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://t") as c:
        assert (await c.get("/llm")).json()["default"] == "ollama"


# OpenAI 프로브 진단 #4: 프로파일 호환성 오류(예: max_tokens 400)는 Goal 생성 즉시 400으로 알리고
# Goal을 만들지 않는다. ollama는 프로브하지 않는다
async def test_goal_llm_probe_rejects_incompatible_profile(
    factory: async_sessionmaker[AsyncSession], redis: Redis, pump: Pump
) -> None:
    from agents.llm.base import ProviderError
    from control_plane.api.app import create_app
    from control_plane.config import Settings

    probed: list[str] = []

    async def probe(profile: str) -> None:
        probed.append(profile)
        if profile == "openai":
            raise ProviderError("openai_compat HTTP 400: max_tokens unsupported")

    app1 = create_app(
        Settings(_env_file=None, llm_provider="openai_compat", openai_api_key="sk-x"),
        factory=factory,
        redis=redis,
        llm_probe=probe,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1), base_url="http://t") as c:
        pid = (await c.post("/projects", json={"name": "d", "repo": REPO})).json()["id"]
        await pump()
        before = len(await events_of(factory, "goal.created"))
        r = await c.post(f"/projects/{pid}/goals", json={"title": "g", "llm": "openai"})
        assert r.status_code == 400 and "max_tokens unsupported" in r.json()["detail"]
        assert len(await events_of(factory, "goal.created")) == before
        r = await c.post(f"/projects/{pid}/goals", json={"title": "g", "llm": "ollama"})
        assert r.status_code == 202
        assert probed == ["openai"]
