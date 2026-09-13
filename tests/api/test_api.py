"""P5.1 — API (red a~i): 라우터, 커서, Idempotency-Key, 취소 cascade. 발행만, DB는 projection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
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
    assert (await client.get(f"/projects/{pid}")).status_code == 404  # projection 전
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
    other = await make_project(client, pump)
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
    r4 = await client.post("/projects", json=body)  # 키 없으면 새 자원
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
