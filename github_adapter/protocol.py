"""GitHub 쓰기 인터페이스 (설계 §7). 실 client와 DryRun client가 같은 시그니처를 구현한다 (D-10).

모든 쓰기 메서드는 **멱등**이다: 같은 입력으로 두 번 부르면 두 번째는 쓰지 않고 기존 것을 돌려준다.
반환 모델의 ``created``가 이번 호출에서 새로 만들었는지 알려준다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- 입력


class TaskIssue(_Frozen):
    """Task → Issue (§7.2 라벨, 본문 상단 마커)."""

    task_id: str
    title: str
    spec: str
    role: str  # role:<role>
    kind: str  # kind:<kind> (언더스코어 → 하이픈)
    tier: str  # tier:T0~T3
    epic_number: int | None = None  # epic:<n> 라벨
    milestone_number: int | None = None


class EpicMilestone(_Frozen):
    epic_id: str
    title: str
    description: str = ""


class PrMeta(_Frozen):
    """§7.3 PR 본문 메타 블록의 값."""

    task_id: str
    run_id: str
    agent_id: str
    tier: str


# --------------------------------------------------------------------------- 출력


class IssueRef(_Frozen):
    number: int
    url: str
    created: bool


class PullRef(_Frozen):
    number: int
    url: str
    created: bool


class CommentRef(_Frozen):
    id: int
    url: str
    created: bool


class BranchRef(_Frozen):
    name: str
    sha: str
    created: bool


class MilestoneRef(_Frozen):
    number: int
    title: str
    created: bool


# --------------------------------------------------------------------------- Protocol


@runtime_checkable
class GitHubClient(Protocol):
    async def ensure_labels(self, repo: str) -> list[str]:
        """§7.2 라벨 전부 보장. 새로 만든 라벨 이름 목록 반환."""
        ...

    async def create_task_issue(self, repo: str, task: TaskIssue) -> IssueRef: ...

    async def create_plan_issue(self, repo: str, key: str, title: str, body: str) -> IssueRef:
        """P9.11: Plans 카테고리가 없으면 Plan을 Issue로. ``key`` 마커로 멱등."""
        ...

    async def update_issue_status_label(self, repo: str, issue_number: int, status: str) -> bool:
        """``status:*`` 라벨 교체. 바뀌었으면 True."""
        ...

    async def comment(
        self, repo: str, number: int, body: str, key: str | None = None
    ) -> CommentRef:
        """``key``가 있으면 같은 key 마커 코멘트가 있을 때 다시 쓰지 않는다."""
        ...

    async def create_branch(self, repo: str, name: str, from_ref: str) -> BranchRef: ...

    async def open_pr(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
        meta: PrMeta,
    ) -> PullRef: ...

    async def create_milestone(self, repo: str, epic: EpicMilestone) -> MilestoneRef: ...
