"""P9.6a — Goal·Epic 완료 판정 (§6 옵션 a): Scheduler가 epic.completed → goal.completed 발행."""

from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.store import models as m
from control_plane.store.enums import EpicStatus, GoalStatus
from tests.scheduler.test_scheduler import BOOTSTRAP, GID, E, Harness, ev, task_created


async def _finish(h: Harness, tid: str, pr: int) -> None:
    """워커 완료 + GitHub 머지 — Task가 done이 되는 정상 경로."""
    run_id = next(s.run_id for s in h.launcher.specs if s.task_id == tid)
    await h.publish(
        ev(E.TASK_STARTED, "task", tid, {"run_id": run_id}),
        ev(E.TASK_COMPLETED, "task", tid, {"run_id": run_id, "pr_number": pr}),
        ev(E.PR_MERGED, "pr", str(pr), {"task_id": tid, "pr_number": pr}),
    )
    await h.pump()


async def _statuses(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[GoalStatus, EpicStatus, EpicStatus]:
    async with factory() as s:
        g, e1, e2 = (
            await s.get(m.Goal, GID),
            await s.get(m.Epic, "E1"),
            await s.get(m.Epic, "E2"),
        )
        assert g is not None and e1 is not None and e2 is not None
        return g.status, e1.status, e2.status


# Epic의 마지막 Task done → epic.completed 1건, 모든 Task 종료 → goal.completed 1건 (epic 뒤)
async def test_epic_then_goal_completed(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    h = Harness(factory, redis)
    await h.publish(
        *BOOTSTRAP,
        task_created("T1", [], ["src/a/**"], epic="E1", issue=10),
        task_created("T2", ["T1"], ["src/b/**"], epic="E2", issue=11),
    )
    await h.pump()
    await _finish(h, "T1", 5)
    assert [e.subject_id for e in await h.events("epic.completed")] == ["E1"]
    assert await h.events("goal.completed") == []
    assert await _statuses(factory) == (GoalStatus.ACTIVE, EpicStatus.DONE, EpicStatus.ACTIVE)

    await _finish(h, "T2", 6)
    epics = await h.events("epic.completed")
    goals = await h.events("goal.completed")
    assert [e.subject_id for e in epics] == ["E1", "E2"]
    assert len(goals) == 1 and goals[0].subject_id == GID and goals[0].seq > epics[-1].seq
    assert await _statuses(factory) == (GoalStatus.DONE, EpicStatus.DONE, EpicStatus.DONE)

    # 같은 머지가 다시 와도(웹훅 재전송) 중복 발행 없음
    await h.publish(ev(E.PR_MERGED, "pr", "6", {"task_id": "T2", "pr_number": 6}))
    await h.pump()
    assert len(await h.events("epic.completed")) == 2
    assert len(await h.events("goal.completed")) == 1


# 일부 cancelled여도 done ≥ 1이면 완료, 빈 Epic은 Goal 완료를 막지 않는다
async def test_cancelled_tasks_count_as_finished(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    h = Harness(factory, redis)
    await h.publish(
        *BOOTSTRAP,  # E2에는 Task가 없다
        task_created("T1", [], ["src/a/**"], epic="E1", issue=10),
        task_created("T2", [], ["src/b/**"], epic="E1", issue=11),
    )
    await h.pump()
    await h.publish(ev(E.TASK_CANCELLED, "task", "T2", {"reason": "user", "by": "u"}))
    await h.pump()
    assert await h.events("epic.completed") == []  # T1이 아직 진행 중
    await _finish(h, "T1", 5)
    assert [e.subject_id for e in await h.events("epic.completed")] == ["E1"]
    assert len(await h.events("goal.completed")) == 1
    g, e1, e2 = await _statuses(factory)
    assert (g, e1, e2) == (GoalStatus.DONE, EpicStatus.DONE, EpicStatus.PENDING)


# 전부 cancelled(done 0) → 완료 아님
async def test_all_cancelled_is_not_completed(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    h = Harness(factory, redis)
    await h.publish(*BOOTSTRAP, task_created("T1", [], ["src/a/**"], epic="E1", issue=10))
    await h.pump()
    await h.publish(ev(E.TASK_CANCELLED, "task", "T1", {"reason": "user", "by": "u"}))
    await h.pump()
    assert await h.events("epic.completed") == []
    assert await h.events("goal.completed") == []
