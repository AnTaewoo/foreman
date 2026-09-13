"""P0~P2 통합 흐름 (진짜 Postgres + Redis, 네트워크 0):

FastAPI 앱(/health + 웹훅 라우터) → DryRun GitHub(Issue/PR) → EventBus(outbox) → relay → projection
→ 서명된 GitHub 웹훅(`/approve` 슬래시, pull_request.closed merged) → pr.merged → Task done
→ verify_chain_db → TRUNCATE → replay 재구축. D-30(b) 순서 역전(pr.merged 선착)도 포함.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from ulid import ULID

from control_plane.api.app import create_app
from control_plane.config import Settings
from control_plane.events.bus import EventBus
from control_plane.events.chain import verify_chain_db
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.store import models as m
from control_plane.store.enums import TaskStatus
from github_adapter.dry_run import DryRunGitHubClient
from github_adapter.markers import pr_meta_block
from github_adapter.protocol import PrMeta, TaskIssue
from github_adapter.webhooks import (
    DeliveryCache,
    ProjectRef,
    SlashCommand,
    WebhookHandler,
    build_webhook_router,
)

pytestmark = pytest.mark.integration
E = EventType
REPO = "org/e2e"
SECRET = "hook-secret"
TEST_REDIS_URL = os.environ.get("FOREMAN_TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await r.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis not reachable: {exc}")
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


class Flow:
    """테스트 하나가 쓰는 조립체."""

    def __init__(self, engine: AsyncEngine, redis: Redis) -> None:
        self.factory = async_sessionmaker(engine, expire_on_commit=False)
        self.bus = EventBus(redis)
        self.relay = OutboxRelay(self.factory, redis, batch=100)
        self.projection = Projection(self.factory, self.bus)
        self.github = DryRunGitHubClient()
        self.slash: list[SlashCommand] = []
        self.pid = str(ULID())
        self.gid = str(ULID())
        self.prev: str | None = None
        handler = WebhookHandler(
            secret=SECRET,
            publish=self.publish_from_webhook,
            resolve_project=self.resolve_project,
            on_slash_command=self.on_slash,
            cache=DeliveryCache(),
            resolve_goal=self.resolve_goal,
            bot_login="foreman[bot]",
        )
        self.app = create_app(Settings(_env_file=None))
        self.app.include_router(build_webhook_router(handler))

    # -- 웹훅 훅
    async def resolve_project(self, repo: str) -> ProjectRef | None:
        return ProjectRef(project_id=self.pid) if repo == REPO else None

    async def resolve_goal(self, project_id: str, task_id: str) -> str | None:
        async with self.factory() as s:
            task = await s.get(m.Task, task_id)
            return task.goal_id if task is not None else None

    async def on_slash(self, cmd: SlashCommand) -> None:
        self.slash.append(cmd)

    async def publish_from_webhook(self, event: Event) -> None:
        async with self.factory() as s:
            await self.bus.publish(s, event)
            await s.commit()

    # -- 내부 발행 (causation 체인)
    async def emit(
        self,
        type_: EventType,
        entity: str,
        id_: str,
        payload: dict[str, Any],
        *,
        corr: str | None = None,
    ) -> Event:
        e = Event(
            project_id=self.pid,
            actor=Actor(type="system", id="e2e"),
            type=type_,
            subject=Subject(entity=entity, id=id_),  # type: ignore[arg-type]
            payload=payload,
            correlation_id=corr or self.gid,
            causation_id=self.prev,
        )
        async with self.factory() as s:
            out = await self.bus.publish(s, e)
            await s.commit()
        if type_ is not E.RUN_TOOL_CALLED:
            self.prev = out.id
        return out

    async def pump(self) -> int:
        """relay → projection consume → due retries. 처리 건수."""
        while await self.relay.relay_once():
            pass
        handled = 0
        idle = 0
        while idle < 2:
            n = await self.bus.poll_once(
                "projection", self.projection.handle, consumer="e2e", project_id=self.pid
            )
            handled += n
            idle = idle + 1 if n == 0 else 0
        await self.projection.apply_retries(self.pid, now=datetime.now(UTC) + timedelta(hours=3))
        return handled

    async def task(self, task_id: str) -> m.Task:
        async with self.factory() as s:
            t = await s.get(m.Task, task_id)
            assert t is not None
            return t

    # -- 웹훅 POST
    async def webhook(
        self, gh_event: str, payload: dict[str, Any], delivery: str
    ) -> httpx.Response:
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "X-GitHub-Event": gh_event,
                    "X-GitHub-Delivery": delivery,
                    "X-Hub-Signature-256": sig,
                    "Content-Type": "application/json",
                },
            )


def _pr_payload(
    action: str, number: int, meta: PrMeta, *, merged: bool, sender: str
) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "html_url": f"https://gh/p/{number}",
            "draft": False,
            "body": pr_meta_block(meta, summary="s"),
            "head": {"ref": "ai/epic/1-t", "sha": "abc"},
            "base": {"ref": "main"},
            "merged": merged,
            "merged_by": {"login": sender},
        },
        "repository": {"full_name": REPO},
        "sender": {"login": sender, "type": "User"},
    }


async def test_p0_p2_end_to_end(pg_engine: AsyncEngine, redis: Redis) -> None:
    f = Flow(pg_engine, redis)

    # P0: 앱이 뜨고 /health
    transport = httpx.ASGITransport(app=f.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}

    # P1+P2: Goal → Epic → Dry Issue 2개 → task.created
    await f.emit(
        E.PROJECT_CREATED,
        "project",
        f.pid,
        {"name": "e2e", "repo": REPO, "default_branch": "main"},
        corr=f.pid,
    )
    await f.emit(E.GOAL_CREATED, "goal", f.gid, {"title": "g", "description": "d"})
    await f.emit(E.GOAL_PLAN_PROPOSED, "goal", f.gid, {"plan_discussion_number": 1, "revision": 1})
    await f.emit(E.GOAL_ACTIVATED, "goal", f.gid, {})
    eid = str(ULID())
    await f.github.ensure_labels(REPO)
    ms = await f.github.create_milestone(
        REPO,
        __import__("github_adapter.protocol", fromlist=["EpicMilestone"]).EpicMilestone(
            epic_id=eid, title="Epic 1"
        ),
    )
    await f.emit(
        E.EPIC_CREATED,
        "epic",
        eid,
        {"goal_id": f.gid, "title": "Epic 1", "order": 1, "milestone_number": ms.number},
    )
    tids = [str(ULID()), str(ULID())]
    issues = []
    for i, tid in enumerate(tids, start=1):
        issue = await f.github.create_task_issue(
            REPO,
            TaskIssue(
                task_id=tid,
                title=f"task {i}",
                spec="do",
                role="coding",
                kind="feature",
                tier="T1",
                milestone_number=ms.number,
            ),
        )
        again = await f.github.create_task_issue(
            REPO,
            TaskIssue(task_id=tid, title="dup", spec="x", role="coding", kind="feature", tier="T1"),
        )
        assert again.number == issue.number and again.created is False  # 멱등
        issues.append(issue)
        await f.emit(
            E.TASK_CREATED,
            "task",
            tid,
            {
                "epic_id": eid,
                "epic_title": "Epic 1",
                "title": f"task {i}",
                "spec": "do",
                "kind": "feature",
                "role_required": "coding",
                "depends_on": [],
                "owned_paths": [f"src/t{i}/**"],
                "risk_tier": "T1",
                "issue_number": issue.number,
                "issue_url": issue.url,
            },
        )
    await f.pump()
    assert [(await f.task(t)).status for t in tids] == [TaskStatus.READY, TaskStatus.READY]
    assert [(await f.task(t)).issue_number for t in tids] == [issues[0].number, issues[1].number]

    # 사람이 Plan Discussion에 /approve → 슬래시 훅 (이벤트 없음)
    res = await f.webhook(
        "issue_comment",
        {
            "action": "created",
            "issue": {"number": 1},
            "comment": {"id": 1, "body": "/approve", "user": {"login": "owner1", "type": "User"}},
            "repository": {"full_name": REPO},
            "sender": {"login": "owner1", "type": "User"},
        },
        "d-approve",
    )
    assert res.status_code == 202 and [c.command for c in f.slash] == ["approve"]

    # 두 Task 실행: assigned → started → run.started → tool_called → Dry PR → pr.opened → …
    prs = []
    for i, tid in enumerate(tids, start=1):
        rid = str(ULID())
        await f.emit(E.TASK_ASSIGNED, "task", tid, {"agent_id": "coding-1", "run_id": rid})
        if i == 1:
            await f.emit(E.EPIC_ACTIVATED, "epic", eid, {})
        await f.emit(E.TASK_STARTED, "task", tid, {"run_id": rid})
        await f.emit(
            E.RUN_STARTED, "run", rid, {"task_id": tid, "agent_id": "coding-1", "model": "fake"}
        )
        await f.emit(E.RUN_TOOL_CALLED, "run", rid, {"tool": "fs.read", "args_digest": "ab" * 32})
        await f.github.create_branch(REPO, f"ai/epic/{issues[i - 1].number}-t", "main")
        meta = PrMeta(task_id=tid, run_id=rid, agent_id="coding-1", tier="T1")
        pr = await f.github.open_pr(
            REPO,
            f"ai/epic/{issues[i - 1].number}-t",
            "main",
            f"[T-{issues[i - 1].number}] t",
            "b",
            True,
            meta,
        )
        prs.append((pr, meta))
        await f.emit(
            E.PR_OPENED,
            "pr",
            str(pr.number),
            {"task_id": tid, "run_id": rid, "pr_number": pr.number, "head": pr.url, "base": "main"},
        )
        await f.github.comment(REPO, issues[i - 1].number, "done", key=f"summary:{rid}")
        if i == 1:
            await f.emit(E.TASK_COMPLETED, "task", tid, {"run_id": rid, "pr_number": pr.number})
        await f.emit(
            E.RUN_FINISHED,
            "run",
            rid,
            {
                "outcome": "success",
                "agent_outcome": "done",
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.0,
                "duration_s": 1.0,
                "error": None,
            },
        )
    await f.pump()
    assert (await f.task(tids[0])).status is TaskStatus.IN_REVIEW
    assert (
        await f.task(tids[1])
    ).status is TaskStatus.RUNNING  # 아직 completed 안 보냄 (D-30 b 검증용)

    # 앱 봇이 연 PR의 opened 웹훅은 무시(B10) — 사람이 머지한 closed 웹훅은 처리
    (pr1, meta1), (pr2, meta2) = prs
    assert (
        await f.webhook(
            "pull_request",
            _pr_payload("opened", pr1.number, meta1, merged=False, sender="foreman[bot]"),
            "d-bot",
        )
    ).status_code == 204
    assert (
        await f.webhook(
            "pull_request",
            _pr_payload("closed", pr1.number, meta1, merged=True, sender="alice"),
            "d-m1",
        )
    ).status_code == 202
    assert (
        await f.webhook(
            "pull_request",
            _pr_payload("closed", pr1.number, meta1, merged=True, sender="alice"),
            "d-m1",
        )
    ).status_code == 200  # 중복
    # Task 2: pr.merged가 task.completed보다 먼저 (교차 발행자 순서 역전)
    assert (
        await f.webhook(
            "pull_request",
            _pr_payload("closed", pr2.number, meta2, merged=True, sender="alice"),
            "d-m2",
        )
    ).status_code == 202
    await f.pump()
    assert (await f.task(tids[0])).status is TaskStatus.DONE
    t2 = await f.task(tids[1])
    assert t2.status is TaskStatus.RUNNING and t2.pr_merged_at is not None  # 플래그만
    await f.emit(E.TASK_COMPLETED, "task", tids[1], {"run_id": "x", "pr_number": pr2.number})
    await f.pump()
    assert (await f.task(tids[1])).status is TaskStatus.DONE

    # 웹훅 이벤트의 correlation은 goal_id (resolve_goal 경유), actor는 github:alice
    async with f.factory() as s:
        rows = (
            (
                await s.execute(
                    select(m.Event).where(m.Event.project_id == f.pid, m.Event.type == "pr.merged")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2 and all(
            r.correlation_id == f.gid and r.actor_type == "github" for r in rows
        )
        errors = (
            await s.execute(
                select(m.Event.projection_error).where(
                    m.Event.project_id == f.pid, m.Event.projection_error.is_not(None)
                )
            )
        ).all()
        assert errors == []
        assert await verify_chain_db(s, f.pid) is True
        n_events = await s.scalar(
            select(text("count(*)")).select_from(m.Event).where(m.Event.project_id == f.pid)
        )

    # Dry GitHub 스냅샷: Issue 2, PR 2, 브랜치 2, 코멘트 key 2
    snap = f.github.snapshot()["repos"][REPO]
    assert len(snap["issues"]) == 2 and len(snap["pulls"]) == 2 and len(snap["branches"]) == 2
    keys = [c["key"] for i in snap["issues"].values() for c in i["comments"]]
    assert len(keys) == 2 and all(k.startswith("summary:") for k in keys)

    # replay 재구축
    async with pg_engine.begin() as conn:
        await conn.execute(text("TRUNCATE tasks, runs, epics, goals, tool_calls CASCADE"))
    async with f.factory() as s:
        replayed = await f.bus.replay(s, f.pid)
    assert len(replayed) == n_events
    for e in replayed:
        await f.projection.apply(e, force=True)
    assert [(await f.task(t)).status for t in tids] == [TaskStatus.DONE, TaskStatus.DONE]
