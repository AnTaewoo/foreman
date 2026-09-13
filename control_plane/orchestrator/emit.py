"""emit_issues — TaskDraft 목록 → Epic/Task 이벤트 + Dry Issue (설계 §5.2, §10.1; D-27).

순서: owned_paths 겹침 직렬화 → 위상 정렬(사이클이면 ``goal.blocked``) → 라벨 → Epic마다 milestone +
``epic.created`` → Task마다 Issue + ``task.created`` (depends_on은 task id). 전부 causation 체인.
멱등: ``state["issues"]``에 이미 있는 Task/Epic은 같은 id를 재사용하고 이벤트를 재발행하지 않는다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import structlog
from ulid import ULID

from control_plane.events.schema import Actor, EntityType, Event, EventType, Subject
from control_plane.orchestrator.drafts import EpicDraft, TaskDraft
from control_plane.orchestrator.state import OrchestratorState
from github_adapter.protocol import EpicMilestone, GitHubClient, TaskIssue

log = structlog.get_logger(__name__)

Publish = Callable[[Event], Awaitable[Event]]
ORCHESTRATOR = Actor(type="agent", id="orchestrator")
GLOB_CHARS = ("*", "?", "[")


class CycleError(Exception):
    def __init__(self, remaining: Sequence[str]) -> None:
        self.remaining = list(remaining)
        super().__init__(f"dependency cycle among tasks: {self.remaining}")


# --------------------------------------------------------------------------- 순수 함수


def toposort(tasks: Sequence[TaskDraft]) -> list[TaskDraft]:
    """Kahn 정렬. 준비된 노드는 입력 순서를 유지. 사이클이면 CycleError."""
    by_title = {t.title: t for t in tasks}
    indeg = {t.title: len([d for d in t.depends_on if d in by_title]) for t in tasks}
    ordered: list[TaskDraft] = []
    ready = [t for t in tasks if indeg[t.title] == 0]
    done: set[str] = set()
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        done.add(current.title)
        for t in tasks:
            if t.title in done or t in ready:
                continue
            if current.title in t.depends_on:
                indeg[t.title] -= 1
            if indeg[t.title] == 0 and t not in ordered:
                ready.append(t)
    if len(ordered) != len(tasks):
        raise CycleError([t.title for t in tasks if t.title not in done])
    return ordered


def _fixed_prefix(path: str) -> tuple[str, ...]:
    """글롭 문자 앞까지의 고정 경로 구성요소."""
    parts: list[str] = []
    for part in path.strip("/").split("/"):
        if any(c in part for c in GLOB_CHARS):
            break
        parts.append(part)
    return tuple(parts)


def paths_overlap(a: str, b: str) -> bool:
    """보수적 판정: 한쪽의 고정 접두 경로가 다른 쪽에 포함되면 겹친다고 본다.

    ``src/app/**`` vs ``src/app/users.py`` → True
    ``src/app/users.py`` vs ``src/app/user_store.py`` → False.
    같은 디렉토리의 글롭(``src/app/*.py``)과 그 하위 파일도 겹침으로 본다(직렬화가 안전한 쪽).
    """
    pa, pb = _fixed_prefix(a), _fixed_prefix(b)
    if not pa or not pb:
        return True  # 루트 글롭은 전부와 겹친다
    n = min(len(pa), len(pb))
    if pa[:n] != pb[:n]:
        return False
    if len(pa) == len(pb):
        return pa == pb  # 같은 깊이면 고정 부분이 완전히 같아야 겹침
    return True  # 한쪽이 다른 쪽의 접두 경로(디렉토리/글롭) → 겹침


def serialize_overlaps(tasks: Sequence[TaskDraft]) -> list[TaskDraft]:
    """owned_paths가 겹치는 쌍은 **나중 Task가 앞 Task에 의존**하도록 depends_on을 추가 (§10.1).

    반대 방향 의존이 이미 있으면 그대로 둔다.
    """
    out = [t.model_copy(deep=True) for t in tasks]
    for j in range(len(out)):
        for i in range(j):
            earlier, later = out[i], out[j]
            if not any(paths_overlap(x, y) for x in earlier.owned_paths for y in later.owned_paths):
                continue
            if later.title in earlier.depends_on or earlier.title in later.depends_on:
                continue
            later.depends_on = [*later.depends_on, earlier.title]
    return out


# --------------------------------------------------------------------------- emit


def _event(
    state: OrchestratorState,
    prev: str | None,
    type_: EventType,
    entity: EntityType,
    id_: str,
    payload: dict[str, Any],
) -> Event:
    return Event(
        project_id=state["project_id"],
        actor=ORCHESTRATOR,
        type=type_,
        subject=Subject(entity=entity, id=id_),
        payload=payload,
        correlation_id=state["goal_id"],
        causation_id=prev,
    )


async def emit(
    state: OrchestratorState, *, github: GitHubClient, publish: Publish
) -> dict[str, Any]:
    """반환: ``{"issues", "last_event_id"}`` 또는 ``{"issues": [], "error", "last_event_id"}``."""
    tasks = [TaskDraft.model_validate(t) for t in state.get("tasks") or []]
    epics = [EpicDraft.model_validate(e) for e in state.get("epics") or []]
    repo = state["repo_full_name"]
    prev: str | None = state.get("last_event_id")
    existing: list[dict[str, Any]] = list(state.get("issues") or [])

    try:
        ordered = toposort(serialize_overlaps(tasks))
    except CycleError as exc:
        blocked = await publish(
            _event(
                state,
                prev,
                EventType.GOAL_BLOCKED,
                "goal",
                state["goal_id"],
                {"reason": f"cycle: {exc.remaining}"},
            )
        )
        log.warning("emit.cycle", tasks=exc.remaining)
        return {
            "issues": [],
            "error": f"dependency cycle: {exc.remaining}",
            "last_event_id": blocked.id,
        }

    await github.ensure_labels(repo)

    # Epic: 이름 → (epic_id, milestone_number). 이미 낸 것은 state["issues"]에서 복원.
    epic_titles = [e.title for e in epics]
    for t in tasks:
        if t.epic and t.epic not in epic_titles:
            epic_titles.append(t.epic)
            epics.append(EpicDraft(title=t.epic, order=len(epic_titles)))
    if not epic_titles:
        epic_titles = [state["goal_title"]]
        epics = [EpicDraft(title=state["goal_title"], order=1)]
    known_epics: dict[str, tuple[str, int | None]] = {
        e["epic_title"]: (e["epic_id"], e.get("milestone_number"))
        for e in existing
        if "epic_title" in e
    }
    epic_info: dict[str, tuple[str, int | None]] = {}
    for order, epic in enumerate(sorted(epics, key=lambda e: e.order), start=1):
        if epic.title in known_epics:
            epic_info[epic.title] = known_epics[epic.title]
            continue
        epic_id = str(ULID())
        ms = await github.create_milestone(
            repo, EpicMilestone(epic_id=epic_id, title=epic.title, description=epic.summary)
        )
        ev = await publish(
            _event(
                state,
                prev,
                EventType.EPIC_CREATED,
                "epic",
                epic_id,
                {
                    "goal_id": state["goal_id"],
                    "title": epic.title,
                    "order": order,
                    "milestone_number": ms.number,
                },
            )
        )
        prev = ev.id
        epic_info[epic.title] = (epic_id, ms.number)

    # Task id: 제목 → id (기존 재사용)
    known_tasks = {e["title"]: e for e in existing if "task_id" in e}
    task_ids = {
        t.title: (known_tasks[t.title]["task_id"] if t.title in known_tasks else str(ULID()))
        for t in ordered
    }
    issues: list[dict[str, Any]] = []
    for t in ordered:
        task_id = task_ids[t.title]
        epic_title = t.epic or epic_titles[0]
        epic_id, milestone = epic_info[epic_title]
        issue = await github.create_task_issue(
            repo,
            TaskIssue(
                task_id=task_id,
                title=t.title,
                spec=t.spec,
                role=t.role_required,
                kind=t.kind,
                tier=t.estimated_tier,
                epic_number=milestone,
                milestone_number=milestone,
            ),
        )
        entry = {
            "task_id": task_id,
            "title": t.title,
            "issue_number": issue.number,
            "issue_url": issue.url,
            "epic_id": epic_id,
            "epic_title": epic_title,
            "milestone_number": milestone,
        }
        if t.title in known_tasks:
            issues.append(known_tasks[t.title])
            continue
        ev = await publish(
            _event(
                state,
                prev,
                EventType.TASK_CREATED,
                "task",
                task_id,
                {
                    "epic_id": epic_id,
                    "epic_title": epic_title,
                    "title": t.title,
                    "spec": t.spec,
                    "kind": t.kind,
                    "role_required": t.role_required,
                    "depends_on": [task_ids[d] for d in t.depends_on if d in task_ids],
                    "owned_paths": list(t.owned_paths),
                    "risk_tier": t.estimated_tier,
                    "issue_number": issue.number,
                    "issue_url": issue.url,
                },
            )
        )
        prev = ev.id
        issues.append(entry)
    log.info("emit.done", tasks=len(issues), epics=len(epic_info))
    return {"issues": issues, "last_event_id": prev}
