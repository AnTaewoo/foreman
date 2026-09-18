"""P9.13 — 분해 정규화: 모르는 kind/role은 오류가 아니라 매핑, MVP 1에 없는 비코드 Task는 버린다.

라이브(2026-09-19, Goal …A05F06 "README 작성"): Plan은 Task 1개인데 분해가 3개로 쪼갰다 —
research(모든 파일 소유) → README 작성(coding) → `kind: "review"` 최종 검토. 스키마에 없는 "review" 한
글자로 Goal 전체가 blocked 됐다.
"""

from __future__ import annotations

from typing import Any

from agents.llm.fake import FakeProvider
from control_plane.orchestrator.drafts import coerce_task_enums, decompose_with_retry_full

ALL = ["index.html", "app.js", "styles.css", "README.md", "tests/test_static_entry.py"]


def live_a05f06() -> dict[str, Any]:
    epic = "프로젝트 README 작성"
    return {
        "epics": [{"title": epic, "order": 1, "summary": "README"}],
        "tasks": [
            {
                "title": "기존 프로젝트 구조 및 동작 확인",
                "spec": "소스와 테스트를 읽는다",
                "kind": "research",
                "role_required": "research",
                "depends_on": [],
                "owned_paths": ALL,
                "estimated_tier": "T0",
                "epic": epic,
            },
            {
                "title": "사실 기반 README.md 작성",
                "spec": "README.md를 작성한다",
                "kind": "feature",
                "role_required": "coding",
                "depends_on": ["기존 프로젝트 구조 및 동작 확인"],
                "owned_paths": ["README.md", "tests/test_static_entry.py"],
                "estimated_tier": "T0",
                "epic": epic,
            },
            {
                "title": "README 최종 검토 및 단일 커밋 반영 준비",
                "spec": "README를 검토한다",
                "kind": "review",  # 스키마에 없다
                "role_required": "reviewer",  # 스키마에 없다
                "depends_on": ["사실 기반 README.md 작성"],
                "owned_paths": ["README.md"],
                "estimated_tier": "T0",
                "epic": epic,
            },
        ],
    }


def test_coerce_unknown_kind_and_role() -> None:
    data = {
        "tasks": [
            {"title": "a", "kind": "review", "role_required": "reviewer"},
            {"title": "b", "kind": "docs", "role_required": "coding"},
            {"title": "c", "kind": "Feature", "role_required": "Coding"},
            {"title": "d", "kind": "feature"},
        ]
    }
    notes = coerce_task_enums(data)
    kinds = [(t["kind"], t.get("role_required")) for t in data["tasks"]]
    assert kinds == [
        ("research", "review"),  # 검토·확인류 → 비코드
        ("feature", "coding"),  # 문서·잡무류 → 코드 변경
        ("feature", "coding"),  # 대소문자만 다름
        ("feature", None),
    ]
    assert any("'review'" in n and "'a'" in n for n in notes) and len(notes) == 3


async def test_live_a05f06_keeps_only_the_code_task() -> None:
    provider = FakeProvider(script=[live_a05f06()])
    result, changes, _, _ = await decompose_with_retry_full(
        provider, repo_summary="R", plan="P", goal="README"
    )
    assert len(provider.calls) == 1  # 재요청 없이 한 번에
    assert [t.title for t in result.tasks] == ["사실 기반 README.md 작성"]
    assert result.tasks[0].depends_on == []  # 버린 research에 대한 의존은 풀린다
    assert any("dropped non-code task '기존 프로젝트 구조 및 동작 확인'" in c for c in changes)
    assert any("dropped non-code task 'README 최종 검토" in c for c in changes)
    assert any("kind 'review'" in c for c in changes)


async def test_all_non_code_is_kept_as_coding() -> None:
    """전부 비코드면 버리지 않는다(빈 Goal 방지) — coding/feature로 바꿔 실행한다."""
    data = live_a05f06()
    data["tasks"] = [data["tasks"][0]]
    provider = FakeProvider(script=[data])
    result, changes, _, _ = await decompose_with_retry_full(
        provider, repo_summary="R", plan="P", goal="README"
    )
    assert [(t.title, t.role_required, t.kind) for t in result.tasks] == [
        ("기존 프로젝트 구조 및 동작 확인", "coding", "feature")
    ]
    assert any("only non-code tasks" in c for c in changes)
