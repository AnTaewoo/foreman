"""P9.3 (D-52) — 데모 모드 가드: 관리 토큰(401), Goal 한도(429), IP별 쓰기 요청 한도(429).

`demo_mode=False`면 전부 기존 동작. 레이트리밋 IP는 scope["client"](uvicorn --proxy-headers가 채움).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.api.app import create_app
from control_plane.config import Settings
from control_plane.events.schema import Actor, Event, EventType, Subject
from tests.api.conftest import Pump

REPO = "acme/demo"
ADMIN = {"X-Admin-Token": "t"}


def make_app(factory: async_sessionmaker[AsyncSession], redis: Redis, **over: Any) -> Any:
    base: dict[str, Any] = {
        "demo_mode": True,
        "admin_token": "t",
        "demo_max_running_goals": 1,
        "demo_goals_per_hour": 3,
        "demo_post_per_ip_per_min": 100,
    }
    base.update(over)
    return create_app(Settings(_env_file=None, **base), factory=factory, redis=redis)


def client_for(app: Any, ip: str = "127.0.0.1") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 1234)), base_url="http://test"
    )


@pytest.fixture
async def demo(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(make_app(factory, redis)) as c:
        yield c


def _goal_event(pid: str, gid: str, type_: EventType, payload: dict[str, Any]) -> Event:
    return Event(
        project_id=pid,
        actor=Actor(type="system", id="orchestrator"),
        type=type_,
        subject=Subject(entity="goal", id=gid),
        payload=payload,
        correlation_id=gid,
        causation_id=None,
    )


# (a) POST /projects는 관리 토큰
async def test_create_project_requires_admin_token(demo: httpx.AsyncClient, pump: Pump) -> None:
    r = await demo.post("/projects", json={"name": "demo", "repo": REPO})
    assert r.status_code == 401
    r = await demo.post("/projects", json={"name": "demo", "repo": REPO}, headers={**ADMIN})
    assert r.status_code == 201, r.text
    bad = await demo.post(
        "/projects", json={"name": "x", "repo": "acme/other"}, headers={"X-Admin-Token": "wrong"}
    )
    assert bad.status_code == 401


# (b) cancel / task patch도 관리 토큰. Goal 생성·승인·거절·읽기는 익명 허용
async def test_cancel_and_task_patch_require_admin(demo: httpx.AsyncClient, pump: Pump) -> None:
    pid = (await demo.post("/projects", json={"name": "d", "repo": REPO}, headers=ADMIN)).json()[
        "id"
    ]
    await pump()
    r = await demo.post(f"/projects/{pid}/goals", json={"title": "g"})
    assert r.status_code == 202, r.text
    gid = r.json()["id"]
    await pump()
    assert (await demo.post(f"/projects/{pid}/goals/{gid}/cancel", json={})).status_code == 401
    assert (
        await demo.patch(f"/projects/{pid}/tasks/T1", json={"action": "cancel"})
    ).status_code == 401
    assert (
        await demo.post(f"/projects/{pid}/goals/{gid}/cancel", json={}, headers=ADMIN)
    ).status_code == 202
    assert (await demo.get(f"/projects/{pid}/goals")).status_code == 200
    assert (await demo.delete(f"/projects/{pid}")).status_code == 401  # 삭제(보관)도 관리 토큰
    assert (await demo.delete(f"/projects/{pid}", headers=ADMIN)).status_code == 202


# (c) 프로젝트에 진행 중(draft|planning|active) Goal이 있으면 429; 끝나면 다시 허용
# (d) 1시간 내 N개면 429
async def test_goal_quota(demo: httpx.AsyncClient, pump: Pump, publish: Any) -> None:
    pid = (await demo.post("/projects", json={"name": "d", "repo": REPO}, headers=ADMIN)).json()[
        "id"
    ]
    await pump()
    g1 = (await demo.post(f"/projects/{pid}/goals", json={"title": "g1"})).json()["id"]
    await pump()
    r = await demo.post(f"/projects/{pid}/goals", json={"title": "g2"})
    assert r.status_code == 429 and "already running" in r.json()["detail"]
    # awaiting_plan_approval은 실행 중으로 세지 않는다 (사람 대기)
    await publish(
        _goal_event(
            pid, g1, EventType.GOAL_PLAN_PROPOSED, {"plan_discussion_number": 1, "revision": 1}
        )
    )
    await pump()
    r = await demo.post(f"/projects/{pid}/goals", json={"title": "g2"})
    assert r.status_code == 202, r.text
    g2 = r.json()["id"]
    await pump()
    await publish(_goal_event(pid, g2, EventType.GOAL_CANCELLED, {"reason": "", "by": "t"}))
    await pump()
    assert (await demo.post(f"/projects/{pid}/goals", json={"title": "g3"})).status_code == 202
    await pump()
    # 3개째까지 만들었으니(시간당 3) 4번째는 시간당 한도
    g3 = (await demo.get(f"/projects/{pid}/goals")).json()["items"][0]["id"]
    await publish(_goal_event(pid, g3, EventType.GOAL_CANCELLED, {"reason": "", "by": "t"}))
    await pump()
    r = await demo.post(f"/projects/{pid}/goals", json={"title": "g4"})
    assert r.status_code == 429 and "hour" in r.json()["detail"]


# (e) 같은 IP에서 POST N+1번째는 429 — 다른 IP는 영향 없음, GET은 무제한
async def test_rate_limit_per_ip(factory: async_sessionmaker[AsyncSession], redis: Redis) -> None:
    app = make_app(factory, redis, demo_post_per_ip_per_min=3)
    async with client_for(app, "9.9.9.9") as a, client_for(app, "8.8.8.8") as b:
        codes = [
            (await a.post("/projects", json={"name": "x", "repo": "bad path"})).status_code
            for _ in range(4)
        ]
        assert codes == [401, 401, 401, 429]
        assert (await a.get("/projects")).status_code == 200
        assert (await b.post("/projects", json={"name": "x", "repo": "x"})).status_code == 401


# (f) demo_mode=False → 기존 동작 (토큰·한도 없음)
async def test_demo_mode_off_is_unchanged(
    factory: async_sessionmaker[AsyncSession], redis: Redis, pump: Pump
) -> None:
    app = make_app(factory, redis, demo_mode=False, demo_post_per_ip_per_min=1)
    async with client_for(app) as c:
        pid = (await c.post("/projects", json={"name": "d", "repo": REPO})).json()["id"]
        await pump()
        assert (await c.post(f"/projects/{pid}/goals", json={"title": "a"})).status_code == 202
        assert (await c.post(f"/projects/{pid}/goals", json={"title": "b"})).status_code == 202
        info = (await c.get("/demo")).json()
        assert info["demo_mode"] is False


# (g) GET /demo — 콘솔이 읽는 데모 설정 (토큰은 절대 안 나감)
async def test_demo_info(demo: httpx.AsyncClient) -> None:
    info = (await demo.get("/demo")).json()
    assert info == {
        "demo_mode": True,
        "user_id": "judge",
        "max_running_goals": 1,
        "goals_per_hour": 3,
    }


# (h) demo_mode인데 admin_token이 비어 있으면 관리 라우트는 항상 401 (fail-closed)
async def test_empty_admin_token_fails_closed(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    app = make_app(factory, redis, admin_token="")
    async with client_for(app) as c:
        r = await c.post(
            "/projects", json={"name": "d", "repo": REPO}, headers={"X-Admin-Token": ""}
        )
        assert r.status_code == 401
