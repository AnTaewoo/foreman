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


# X.2: 최소 Task 수는 코드가 검증하고 오류를 붙여 재요청한다 (PC-5 14b: Task 2개로 분해)
async def test_decompose_min_tasks_retry() -> None:
    provider = FakeProvider(script=[
        result(task("A"), task("B", ["A"])),  # 2개 → 거부
        result(task("A"), task("B", ["A"]), task("C")),  # 3개 → 통과
    ])  # fmt: skip
    out = await decompose_with_retry(provider, repo_summary="R", plan="P", goal="G", min_tasks=3)
    assert [t.title for t in out.tasks] == ["A", "B", "C"] and len(provider.calls) == 2
    assert "at least 3" in provider.calls[1].messages[-1].content


# P9 (foreman_demo 1차 Goal, 2026-09-16): 분해가 소스만 owned_paths로 주고 모델은 tests/를 쓰려 해
# 9/9회 scope_violation → 전부 blocked. 코드 Task는 테스트 경로를 하나 이상 소유해야 한다
def src_task(
    title: str, paths: list[str], deps: list[str] | None = None, **kw: object
) -> dict[str, object]:
    t = task(title, deps)
    t["owned_paths"] = paths
    t.update(kw)
    return t


async def test_decompose_requires_test_paths_then_retry() -> None:
    provider = FakeProvider(script=[
        result(src_task("Setup server", ["main.py", "server/__init__.py"])),  # 테스트 없음 → 거부
        result(src_task("Setup server", ["main.py", "server/__init__.py", "tests/test_server.py"])),
    ])  # fmt: skip
    out = await decompose_with_retry(provider, repo_summary="R", plan="P", goal="G")
    assert out.tasks[0].owned_paths == ["main.py", "server/__init__.py", "tests/test_server.py"]
    assert len(provider.calls) == 2
    err = provider.calls[1].messages[-1].content
    assert "Setup server" in err and "test" in err.lower() and "owned_paths" in err


async def test_decompose_augments_test_paths_on_final_attempt() -> None:
    # 재요청해도 안 고치면(작은 모델) 실패시키지 않고 보강한다: 파일 stem + 디렉토리 이름 기반
    provider = FakeProvider(script=[
        result(
            src_task("Setup server", ["main.py", "server/__init__.py"]),
            src_task("Calculator logic", ["server/logic/calculator.py"], ["Setup server"]),
        ),
        result(
            src_task("Setup server", ["main.py", "server/__init__.py"]),
            src_task("Calculator logic", ["server/logic/calculator.py"], ["Setup server"]),
        ),
    ])  # fmt: skip
    out = await decompose_with_retry(provider, repo_summary="R", plan="P", goal="G")
    by = {t.title: t for t in out.tasks}
    assert "tests/test_main.py" in by["Setup server"].owned_paths
    assert "tests/test_server.py" in by["Setup server"].owned_paths
    assert by["Setup server"].owned_paths[:2] == ["main.py", "server/__init__.py"]
    logic = by["Calculator logic"].owned_paths
    assert "tests/test_calculator.py" in logic and "tests/test_logic.py" in logic
    assert "tests/test_server.py" in logic  # 디렉토리 이름 — 모델이 실제로 고른 이름들


async def test_decompose_merges_test_only_tasks_into_implementation() -> None:
    # 패턴 2: "write tests for X" Task가 구현보다 먼저 돌아 import 실패 → 구현 Task로 합친다
    provider = FakeProvider(script=[
        result(
            src_task("Create maths module", ["src/maths.py"]),
            src_task(
                "Write tests for maths",
                ["tests/test_maths.py"],
                ["Create maths module"],
                kind="test",
            ),
            src_task(
                "Use maths in app", ["src/app.py", "tests/test_app.py"], ["Write tests for maths"]
            ),
        ),
    ])  # fmt: skip
    out = await decompose_with_retry(provider, repo_summary="R", plan="P", goal="G")
    titles = [t.title for t in out.tasks]
    assert titles == ["Create maths module", "Use maths in app"]
    impl = out.tasks[0]
    assert impl.owned_paths == ["src/maths.py", "tests/test_maths.py"]
    assert "Write tests for maths" in impl.spec  # 합쳐진 Task의 spec(기대값)은 보존
    assert out.tasks[1].depends_on == ["Create maths module"]  # 의존은 구현 Task로 옮긴다
    assert len(provider.calls) == 1


# P9 버그 #2 (foreman_demo Goal #2): depends_on 문자열이 Task 제목과 글자 단위로 안 맞아 Goal 즉사.
# 정규화 매칭: 대소문자·구두점·공백 무시 동치 → "T-n" 접두 유무 → 유일한 접두/포함
def test_depends_on_normalized_matching() -> None:
    out = DecomposeResult.model_validate(
        result(
            src_task(
                "T-1: Create calculator.py with add/minus",
                ["calculator.py", "tests/test_calculator.py"],
            ),
            src_task(
                "T-2: Create app.py to set up Flask and handle /calc endpoints",
                ["app.py", "tests/test_app.py"],
                ["Create calculator.py with add/minus"],  # 번호 접두 없이
            ),
            src_task(
                "T-3: Wire it up",
                ["main.py", "tests/test_main.py"],
                ["create app.py to set up flask and handle /calc endpoints."],  # 대소문자·마침표
            ),
        )
    )
    assert out.tasks[1].depends_on == ["T-1: Create calculator.py with add/minus"]
    assert out.tasks[2].depends_on == [
        "T-2: Create app.py to set up Flask and handle /calc endpoints"
    ]
    with pytest.raises(ValidationError, match="unknown task"):
        DecomposeResult.model_validate(
            result(
                src_task("A", ["a.py", "tests/test_a.py"]),
                src_task("B", ["b.py"], ["Nothing like it"]),
            )
        )


async def test_decompose_error_carries_raw_tail() -> None:
    provider = FakeProvider(script=[result(task("A", ["A"])), result(task("A"), task("A"))])
    with pytest.raises(DecomposeError) as exc:
        await decompose_with_retry(provider, repo_summary="R", plan="P", goal="G")
    assert "raw:" in str(exc.value) and '"tasks"' in str(exc.value)  # 모델 원문 꼬리 (진단용)


# P9 버그 #6: 테스트 전용 Task를 합친 뒤 Task가 0개 남은 Epic은 버린다 (빈 마일스톤 방지)
async def test_merge_drops_empty_epics() -> None:
    provider = FakeProvider(script=[
        result(
            src_task("Create maths module", ["src/maths.py"], epic="Impl"),
            src_task(
                "Write tests for maths",
                ["tests/test_maths.py"],
                ["Create maths module"],
                kind="test",
                epic="Tests",
            ),
            epics=["Impl", "Tests"],
        ),
    ])  # fmt: skip
    out = await decompose_with_retry(provider, repo_summary="R", plan="P", goal="G")
    assert [t.title for t in out.tasks] == ["Create maths module"]
    assert [e.title for e in out.epics] == ["Impl"]


# X.2: 프롬프트 세트 규칙 — 심볼 색인 재사용, 기대값·fresh state, 겹치지 않으면 의존 금지, T-n
def test_prompt_set_rules_x2() -> None:
    analyze = (PROMPTS / "analyze.md").read_text(encoding="utf-8")
    assert "Symbols" in analyze and "already exist" in analyze
    plan = (PROMPTS / "plan.md").read_text(encoding="utf-8")
    assert "T-1" in plan
    dec = (PROMPTS / "decompose.md").read_text(encoding="utf-8")
    for hint in ("Symbols", "already exist", "expected", "fresh", "must NOT depend", "at least 3"):
        assert hint in dec, hint


# PC-7 재실행 발견: 모델이 depends_on에 제목 대신 Task Graph 번호("T-1")나 번호 접두를 쓴다 →
# 제목이 "T-1: …"/"T-1 …"로 시작하는 Task로 해석한다. 못 찾으면 종전처럼 ValidationError
def test_depends_on_accepts_task_numbers() -> None:
    out = DecomposeResult.model_validate(
        result(
            task("T-1: Create maths.py"),
            task("T-2: Write test for add", ["T-1"]),
            task("T-3: Write test for mul", ["T-1:", "T-2: Write test for add"]),
        )
    )
    assert out.tasks[1].depends_on == ["T-1: Create maths.py"]
    assert out.tasks[2].depends_on == ["T-1: Create maths.py", "T-2: Write test for add"]
    with pytest.raises(ValueError, match="unknown task"):
        DecomposeResult.model_validate(result(task("T-1: a"), task("T-2: b", ["T-9"])))
