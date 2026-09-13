"""Orchestrator 그래프 상태 (설계 §15.1). JSON 직렬화 가능한 값만 — 체크포인터에 저장된다.

노드는 여기 정의된 키만 돌려줄 수 있다 (LangGraph가 스키마 밖 키를 거부한다).
"""

from __future__ import annotations

from typing import Any, TypedDict


class OrchestratorState(TypedDict, total=False):
    # 입력
    project_id: str
    goal_id: str
    goal_title: str
    goal_description: str
    repo_path: str  # 로컬 경로 (D-11)
    repo_full_name: str  # Discussion/Issue 대상 repo
    model: str | None
    # analyze_repo
    repo_summary: str
    # draft_plan
    plan: str  # §5.2 6섹션 마크다운
    plan_json: dict[str, Any]  # PlanDraft dump
    plan_discussion_number: int
    plan_discussion_id: str
    plan_revision: int
    # wait_plan_approval
    approval: dict[str, Any]  # {"approved": bool, "by": str, "reason"?: str}
    # decompose
    epics: list[dict[str, Any]]  # EpicDraft dumps
    tasks: list[dict[str, Any]]  # TaskDraft dumps (depends_on = 제목)
    # emit_issues (P3.5)
    issues: list[dict[str, Any]]  # [{"task_id", "issue_number", ...}]
    error: str | None
    # 이벤트 체인
    last_event_id: str | None


def initial_state(
    *,
    project_id: str,
    goal_id: str,
    goal_title: str,
    goal_description: str,
    repo_path: str,
    repo_full_name: str,
    model: str | None = None,
    last_event_id: str | None = None,
) -> OrchestratorState:
    """API가 goal.created를 발행한 뒤 그 id를 ``last_event_id``로 넘긴다 (그래프는 goal.created를 안 만든다)."""
    return OrchestratorState(
        project_id=project_id,
        goal_id=goal_id,
        goal_title=goal_title,
        goal_description=goal_description,
        repo_path=repo_path,
        repo_full_name=repo_full_name,
        model=model,
        plan_revision=0,
        issues=[],
        error=None,
        last_event_id=last_event_id,
    )
