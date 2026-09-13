"""ready 판정 (설계 §3.2 Scheduler, §10.1): projection 상태(DB)를 읽어 배정 가능한 Task를 고른다."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from control_plane.orchestrator.emit import paths_overlap
from control_plane.scheduler.graph import topo_order
from control_plane.store import models as m
from control_plane.store.enums import TaskStatus


@dataclass(frozen=True)
class Candidate:
    id: str
    epic_id: str
    goal_id: str
    title: str
    spec: str
    kind: str
    role_required: str
    owned_paths: tuple[str, ...]
    depends_on: tuple[str, ...]
    issue_number: int | None
    risk_tier: str
    attempt_count: int
    max_attempts: int


def _candidate(t: m.Task) -> Candidate:
    return Candidate(
        id=t.id,
        epic_id=t.epic_id,
        goal_id=t.goal_id,
        title=t.title,
        spec=t.spec,
        kind=t.kind.value,
        role_required=t.role_required.value,
        owned_paths=tuple(t.owned_paths),
        depends_on=tuple(str(d) for d in t.depends_on),
        issue_number=t.issue_number,
        risk_tier=t.risk_tier.value,
        attempt_count=t.attempt_count,
        max_attempts=t.max_attempts,
    )


async def load_project_tasks(session: AsyncSession, project_id: str) -> list[m.Task]:
    stmt = (
        select(m.Task).where(m.Task.project_id == project_id).order_by(m.Task.created_at, m.Task.id)
    )
    return list((await session.execute(stmt)).scalars().all())


def overlaps(a: Iterable[str], b: Iterable[str]) -> bool:
    return any(paths_overlap(x, y) for x in a for y in b)


def pick_ready(
    tasks: list[m.Task],
    *,
    in_flight: set[str],
    free_slots: int,
) -> list[Candidate]:
    """배정 후보 (위상 순): ready ∧ 의존 전부 done ∧ 실행 중·이번 선택과 owned_paths 비겹침."""
    if free_slots <= 0:
        return []
    by_id = {t.id: t for t in tasks}
    done = {t.id for t in tasks if t.status is TaskStatus.DONE}
    active = {
        t.id for t in tasks if t.status in (TaskStatus.ASSIGNED, TaskStatus.RUNNING)
    } | in_flight
    pending = {
        t.id: [str(d) for d in t.depends_on] for t in tasks if t.status is not TaskStatus.DONE
    }
    order = topo_order(pending)  # 사이클이면 SchedulerError
    busy_paths: list[str] = [p for tid in active if tid in by_id for p in by_id[tid].owned_paths]
    chosen: list[Candidate] = []
    for tid in order:
        t = by_id[tid]
        if t.status is not TaskStatus.READY or tid in active:
            continue
        if any(str(d) not in done for d in t.depends_on):
            continue
        if overlaps(t.owned_paths, busy_paths):
            continue
        chosen.append(_candidate(t))
        busy_paths.extend(t.owned_paths)
        if len(chosen) >= free_slots:
            break
    return chosen
