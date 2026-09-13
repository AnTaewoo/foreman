"""P5.3 — WebSocket 스트림 (red a~c). Starlette TestClient(별도 스레드 루프)로 WS, 발행은 pytest 루프에서.

앱은 factory/redis를 주입받지 않고 같은 sqlite 파일·Redis DB에 자기 연결을 만든다(루프가 다르므로)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.testclient import TestClient
from ulid import ULID

from control_plane.api.app import create_app
from control_plane.config import Settings
from control_plane.events.outbox import OutboxRelay
from control_plane.events.schema import Actor, Event, EventType, Subject
from tests.events.conftest import TEST_REDIS_URL

Publish = Callable[..., Awaitable[list[Event]]]


@pytest.fixture
def ws_client(engine: AsyncEngine, redis: Redis) -> Any:
    settings = Settings(
        _env_file=None,
        database_url=engine.url.render_as_string(hide_password=False),
        redis_url=TEST_REDIS_URL,
    )
    with TestClient(create_app(settings)) as tc:
        yield tc


@pytest.fixture
def relay(factory: async_sessionmaker[AsyncSession], redis: Redis) -> Callable[[], Awaitable[int]]:
    r = OutboxRelay(factory, redis, batch=100)

    async def run() -> int:
        total = 0
        while (n := await r.relay_once()) > 0:
            total += n
        return total

    return run


def ev(pid: str, type_: EventType, id_: str, payload: dict[str, Any] | None = None) -> Event:
    return Event(
        project_id=pid,
        actor=Actor(type="system", id="api"),
        type=type_,
        subject=Subject(entity="project", id=id_),
        payload=payload
        if payload is not None
        else {"name": id_, "repo": "org/demo", "default_branch": "main"},
        correlation_id=pid,
        causation_id=None,
    )


# (a) 연결 → publish + relay → JSON 수신 (seq/id/type/payload 포함)
async def test_live_events(ws_client: TestClient, publish: Publish, relay: Any) -> None:
    pid = str(ULID())
    with ws_client.websocket_connect(f"/projects/{pid}/stream") as ws:
        (e,) = await publish(ev(pid, EventType.PROJECT_CREATED, pid))
        assert await relay() == 1
        msg = ws.receive_json()
        assert msg["type"] == "project.created" and msg["id"] == e.id
        assert msg["subject"] == {"entity": "project", "id": pid}
        assert msg["payload"]["name"] == pid and isinstance(msg["seq"], int)
        assert msg["correlation_id"] == pid and msg["causation_id"] is None
        (e2,) = await publish(ev(pid, EventType.PROJECT_UPDATED, pid, {"name": "renamed"}))
        await relay()
        assert ws.receive_json()["id"] == e2.id


# (b) ?since=<seq> → seq 이후를 replay 한 뒤 live, 중복 없음
async def test_since_replays_then_live(ws_client: TestClient, publish: Publish, relay: Any) -> None:
    pid = str(ULID())
    created = await publish(
        ev(pid, EventType.PROJECT_CREATED, pid),
        ev(pid, EventType.PROJECT_UPDATED, pid, {"name": "a"}),
        ev(pid, EventType.PROJECT_UPDATED, pid, {"name": "b"}),
    )
    await relay()
    first_seq = ws_client.get(f"/projects/{pid}/events", params={"limit": 1}).json()["items"][0][
        "seq"
    ]
    with ws_client.websocket_connect(f"/projects/{pid}/stream?since={first_seq}") as ws:
        got = [ws.receive_json(), ws.receive_json()]
        assert [g["id"] for g in got] == [created[1].id, created[2].id]
        assert got[0]["seq"] > first_seq and got[1]["seq"] > got[0]["seq"]
        (live,) = await publish(ev(pid, EventType.PROJECT_UPDATED, pid, {"name": "c"}))
        await relay()
        msg = ws.receive_json()
        assert msg["id"] == live.id and msg["payload"] == {"name": "c"}
    # since가 정수가 아니면 연결 거부
    with pytest.raises(Exception):  # noqa: B017 — WebSocketDisconnect(1008) 또는 핸드셰이크 오류
        with ws_client.websocket_connect(f"/projects/{pid}/stream?since=abc") as ws:
            ws.receive_json()


# (c) 다른 project 이벤트는 오지 않는다
async def test_other_project_not_delivered(
    ws_client: TestClient, publish: Publish, relay: Any
) -> None:
    pid, other = str(ULID()), str(ULID())
    with ws_client.websocket_connect(f"/projects/{pid}/stream") as ws:
        await publish(ev(other, EventType.PROJECT_CREATED, other))
        (mine,) = await publish(ev(pid, EventType.PROJECT_CREATED, pid))
        await relay()
        msg = ws.receive_json()  # 먼저 발행된 other는 건너뛰고 내 것이 첫 메시지
        assert msg["id"] == mine.id and msg["subject"]["id"] == pid
