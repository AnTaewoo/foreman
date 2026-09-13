"""P3.3 — prompts + drafts (red a~e): TaskDraft/PlanDraft, render_prompt, decompose_with_retry."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.llm.fake import FakeProvider
from control_plane.orchestrator.drafts import (
    PLAN_SECTIONS,
    DecomposeError,
    DecomposeResult,
    EpicDraft,
    PlanDraft,
    TaskDraft,
    decompose_with_retry,
    missing_plan_sections,
    render_prompt,
)

PROMPTS = Path("control_plane/orchestrator/prompts")


def task(title: str, deps: list[str] | None = None, epic: str = "E") -> dict[str, object]:
    return {
        "title": title, "spec": f"do {title}", "kind": "feature", "role_required": "coding",
        "depends_on": deps or [], "owned_paths": [f"src/{title.lower()}/**"],
        "estimated_tier": "T1",
        "epic": epic,
    }  # fmt: skip


def result(*tasks: dict[str, object], epics: list[str] | None = None) -> dict[str, object]:
    return {
        "epics": [
            {"title": e, "order": i + 1, "summary": f"{e} epic"}
            for i, e in enumerate(epics or ["E"])
        ],
        "tasks": list(tasks),
    }


# (a) TaskDraft
def test_task_draft_validation() -> None:
    t = TaskDraft(**task("A"))  # type: ignore[arg-type]
    assert t.title == "A" and t.owned_paths == ["src/a/**"] and t.estimated_tier == "T1"
    with pytest.raises(ValidationError, match="owned_paths"):
        TaskDraft(**{**task("A"), "owned_paths": []})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TaskDraft(**{**task("A"), "kind": "epic"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TaskDraft(**{**task("A"), "estimated_tier": "T9"})  # type: ignore[arg-type]


# (b) PlanDraft.to_markdown — §5.2 6섹션
def test_plan_draft_markdown() -> None:
    plan = PlanDraft(
        understanding="Flask app, in-memory store.",
        acceptance_criteria=["GET /users returns list", "tests pass"],
        epics=[EpicDraft(title="Users API", order=1, summary="CRUD", task_count=3, risk_tier="T1")],
        task_graph="T-1 → T-2 → T-3",
        decisions_expected=["none"],
        budget_estimate="~$2, ~4 runs",
    )
    md = plan.to_markdown(goal_title="Add users endpoint", goal_number=7)
    assert md.startswith("## Plan for Goal #7: Add users endpoint")
    for section in PLAN_SECTIONS:
        assert f"### {section}" in md
    assert "- [ ] AC-1 GET /users returns list" in md
    dup = plan.model_copy(update={"acceptance_criteria": ["AC-1: already prefixed", "ac2) other"]})
    dup_md = dup.to_markdown(goal_title="g", goal_number=1)
    assert "- [ ] AC-1 already prefixed" in dup_md and "- [ ] AC-2 other" in dup_md
    assert "1. Users API — Tasks: 3, est. risk: T1" in md
    assert missing_plan_sections(md) == []
    assert missing_plan_sections("## Plan\n### Understanding\nx") == list(PLAN_SECTIONS[1:])


# (c) 프롬프트 파일 + render_prompt
@pytest.mark.parametrize("name", ["analyze", "plan", "decompose"])
def test_prompt_files_have_rationale_comment(name: str) -> None:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    assert text.startswith("<!--")
    comment = text[: text.index("-->")]
    assert len([ln for ln in comment.splitlines() if ln.strip().startswith("-")]) >= 3


def test_render_prompt_strips_comment_and_substitutes() -> None:
    out = render_prompt("plan", goal="G", repo_summary="R")
    assert "<!--" not in out and "-->" not in out
    assert "G" in out and "R" in out and "{goal}" not in out
    with pytest.raises(KeyError):
        render_prompt("plan", goal="G")  # repo_summary 누락
    dec = render_prompt("decompose", goal="G", repo_summary="R", plan="P")
    assert '"owned_paths"' in dec and "{{" not in dec  # JSON 예시의 {{ }}가 { }로
    assert "30" in dec and "2" in dec  # 30분~2시간
    assert "owned_paths" in dec


def test_prompt_content_hints() -> None:
    plan = (PROMPTS / "plan.md").read_text(encoding="utf-8")
    assert all(s in plan for s in PLAN_SECTIONS)
    dec = (PROMPTS / "decompose.md").read_text(encoding="utf-8")
    assert "depends_on" in dec and "estimated_tier" in dec


# (e) DecomposeResult 검증
def test_decompose_result_validation() -> None:
    ok = DecomposeResult.model_validate(result(task("A"), task("B", ["A"])))
    assert [t.title for t in ok.tasks] == ["A", "B"]
    with pytest.raises(ValidationError, match="unique"):
        DecomposeResult.model_validate(result(task("A"), task("A")))
    with pytest.raises(ValidationError, match="unknown"):
        DecomposeResult.model_validate(result(task("A", ["Z"])))
    with pytest.raises(ValidationError, match="itself"):
        DecomposeResult.model_validate(result(task("A", ["A"])))
    with pytest.raises(ValidationError, match="epic"):
        DecomposeResult.model_validate(result(task("A", epic="Nope")))


# (d) decompose_with_retry
async def test_decompose_retry_succeeds_second_time() -> None:
    provider = FakeProvider(script=[
        "{not json",  # 1회차 파싱 실패
        result(task("A"), task("B", ["A"])),  # 2회차 성공
    ])  # fmt: skip
    out = await decompose_with_retry(provider, repo_summary="R", plan="P", goal="G")
    assert [t.title for t in out.tasks] == ["A", "B"]
    assert len(provider.calls) == 2
    second = provider.calls[1].messages
    assert second[-1].role == "user" and "{not json" in second[-2].content  # 이전 응답 + 오류
    assert "error" in second[-1].content.lower()
    assert provider.calls[0].schema is DecomposeResult


async def test_decompose_retry_on_validation_error_then_fail() -> None:
    provider = FakeProvider(script=[result(task("A", ["A"])), result(task("A"), task("A"))])
    with pytest.raises(DecomposeError, match="2 attempts"):
        await decompose_with_retry(provider, repo_summary="R", plan="P", goal="G")
    assert len(provider.calls) == 2
    assert "itself" in provider.calls[1].messages[-1].content
