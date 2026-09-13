"""P6 런타임 테스트 픽스처: P1 이벤트 픽스처(aiosqlite + 진짜 Redis) 재노출 + 이벤트 빌더."""

from __future__ import annotations

from typing import Any

from control_plane.events.schema import Actor, EntityType, Event, EventType
from tests.events import conftest as _events

engine = _events.engine
factory = _events.factory
redis = _events.redis
session = _events.session

SYSTEM = Actor(type="system", id="test")


def ev(
    project_id: str,
    type_: EventType,
    entity: EntityType,
    id_: str,
    payload: dict[str, Any],
    *,
    correlation_id: str | None = None,
    actor: Actor = SYSTEM,
) -> Event:
    from control_plane.events.schema import Subject

    return Event(
        project_id=project_id,
        actor=actor,
        type=type_,
        subject=Subject(entity=entity, id=id_),
        payload=payload,
        correlation_id=correlation_id or project_id,
        causation_id=None,
    )


def bootstrap(pid: str, gid: str, repo: str, default_branch: str = "main") -> list[Event]:
    """project → goal → plan → activated → epic. Task는 호출자가 붙인다."""
    E = EventType
    return [
        ev(
            pid,
            E.PROJECT_CREATED,
            "project",
            pid,
            {"name": pid, "repo": repo, "default_branch": default_branch},
        ),
        ev(
            pid, E.GOAL_CREATED, "goal", gid, {"title": "g", "description": "d"}, correlation_id=gid
        ),
        ev(
            pid,
            E.GOAL_PLAN_PROPOSED,
            "goal",
            gid,
            {"plan_discussion_number": 1, "revision": 1},
            correlation_id=gid,
        ),
        ev(pid, E.GOAL_ACTIVATED, "goal", gid, {}, correlation_id=gid),
        ev(
            pid,
            E.EPIC_CREATED,
            "epic",
            f"{pid}-E1",
            {"goal_id": gid, "title": "E1", "order": 1, "milestone_number": 1},
            correlation_id=gid,
        ),
    ]


def task_created(pid: str, gid: str, tid: str, owned: list[str], issue: int) -> Event:
    return ev(
        pid,
        EventType.TASK_CREATED,
        "task",
        tid,
        {
            "epic_id": f"{pid}-E1",
            "epic_title": "E1",
            "title": tid,
            "spec": "s",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": [],
            "owned_paths": owned,
            "risk_tier": "T1",
            "issue_number": issue,
            "issue_url": None,
        },
        correlation_id=gid,
    )
