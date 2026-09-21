"""P9.23 — 분해: 문서를 쓰는 research Task는 남기고, 고아 test-only Task는 구현 Task에 합친다.

라이브(2026-09-21, melpes/RhythmTasker Goal …FR5A5B): T-1(research, ``spec.md`` 작성)을 P9.13
규칙이 버리자 T-2(테스트 계약만, T-1에만 의존)의 의존이 비었고, 합치기 규칙은 의존이 없으면
건너뛰어 T-2가 첫 Task로 남았다 — 구현(T-3)보다 먼저 도는 테스트라 3회 모두 실패.
"""

from __future__ import annotations

from typing import Any

from agents.llm.fake import FakeProvider
from control_plane.orchestrator.drafts import decompose_with_retry_full

T1 = "T-1 연구 근거와 리듬 권고 정책을 spec.md에 명세"
T2 = "T-2 관찰 이벤트·모델 출력·기본값에 대한 테스트 계약 작성"
T3 = "T-3 업무-휴식 리듬 모델 모듈 구현"
T4 = "T-4 BPMController와 리듬 모델의 interval·권고 계산 통합"
T5 = "T-5 StateMachine과 Scheduler 이벤트를 모델 입력으로 연결"
T6 = "T-6 리듬 모델과 기존 핵심 동작 회귀 테스트 보강"
T7 = "T-7 Discord status에 권고·관찰 근거 표시 통합"
T8 = "T-8 알림 빈도·자동 적용 경계 및 전체 통합 테스트 보강"


def _task(
    title: str, kind: str, owned: list[str], deps: list[str], role: str = "coding"
) -> dict[str, Any]:
    return {
        "title": title,
        "spec": f"spec of {title}",
        "kind": kind,
        "role_required": role,
        "depends_on": deps,
        "owned_paths": owned,
        "estimated_tier": "T2",
        "epic": "E",
    }


def live_fr5a5b() -> dict[str, Any]:
    """goal.decomposed.parsed 그대로 (spec만 줄임)."""
    return {
        "epics": [{"title": "E", "order": 1, "summary": "s"}],
        "tasks": [
            _task(T1, "research", ["spec.md"], [], role="research"),
            _task(T2, "test", ["tests/test_rhythm_model.py"], [T1], role="test"),
            _task(T3, "feature", ["src/core/rhythm_model.py", "tests/test_rhythm_model.py"], [T1, T2]),
            _task(
                T4, "feature",
                ["src/core/bpm_controller.py", "tests/test_bpm_controller_properties.py"], [T3],
            ),
            _task(
                T5, "feature",
                ["src/core/state_machine.py", "src/core/scheduler.py", "tests/test_scheduler.py",
                 "tests/test_state_machine_properties.py"], [T3],
            ),
            _task(T6, "test", ["tests/test_rhythm_integration.py"], [T4, T5], role="test"),
            _task(
                T7, "feature",
                ["src/discord_bot/commands.py", "src/discord_bot/embeds.py",
                 "tests/test_discord_rhythm_status.py"], [T5],
            ),
            _task(T8, "test", ["tests/test_rhythm_end_to_end.py"], [T6, T7], role="test"),
        ],
    }  # fmt: skip


async def _decompose(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    provider = FakeProvider(script=[data])
    result, changes, _, _ = await decompose_with_retry_full(
        provider, repo_summary="R", plan="P", goal="G"
    )
    assert len(provider.calls) == 1  # 재요청 없이
    return {t.title: t for t in result.tasks}, changes


async def test_live_fr5a5b_keeps_doc_task_and_merges_test_contract() -> None:
    tasks, changes = await _decompose(live_fr5a5b())
    assert list(tasks) == [T1, T3, T4, T5, T7]
    # (a) spec.md를 쓰는 research Task는 버리지 않고 파일을 바꾸는 Task로 (P9.13 관례)
    assert (tasks[T1].role_required, tasks[T1].kind) == ("coding", "research")
    assert tasks[T1].owned_paths == ["spec.md"]
    assert any(f"kept doc task '{T1}'" in c for c in changes)
    # (b) T-2(테스트 계약)는 그 모듈을 구현하는 T-3에 합쳐진다 — 먼저 도는 테스트 없음
    assert any(f"merged test-only task '{T2}' into '{T3}'" in c for c in changes)
    assert tasks[T3].depends_on == [T1]
    assert f"spec of {T2}" in tasks[T3].spec
    assert tasks[T3].owned_paths.count("tests/test_rhythm_model.py") == 1
    # 첫 Task(의존 없음)는 문서 Task 하나뿐
    assert [t for t, v in tasks.items() if not v.depends_on] == [T1]


async def test_orphan_test_task_merges_into_dependent_when_research_dropped() -> None:
    """research가 비문서 파일을 소유해 버려져도, 의존이 빈 test-only는 구현 dependent에 합친다."""
    data = live_fr5a5b()
    data["tasks"][0]["owned_paths"] = ["src/core/rhythm_model.py", "spec.md"]
    tasks, changes = await _decompose(data)
    assert T1 not in tasks and T2 not in tasks
    assert any(f"merged test-only task '{T2}' into '{T3}'" in c for c in changes)
    assert tasks[T3].depends_on == []


async def test_doc_research_task_sharing_a_file_is_still_dropped() -> None:
    """다른 Task도 소유한 문서만 가진 research/review는 검토일 뿐 — 여전히 버린다(A05F06)."""
    data = {
        "epics": [{"title": "E", "order": 1, "summary": "s"}],
        "tasks": [
            _task("Write README", "feature", ["README.md", "tests/test_readme.py"], []),
            _task("Review README", "research", ["README.md"], ["Write README"], role="review"),
        ],
    }
    tasks, changes = await _decompose(data)
    assert list(tasks) == ["Write README"]
    assert any("dropped non-code task 'Review README'" in c for c in changes)


async def test_test_task_for_existing_code_stays_alone() -> None:
    """기존 코드에 테스트를 더하는 독립 Task(대상 모듈을 구현하는 Task 없음)는 그대로 둔다."""
    data = {
        "epics": [{"title": "E", "order": 1, "summary": "s"}],
        "tasks": [
            _task("Add tests for parser", "test", ["tests/test_parser.py"], [], role="test"),
            _task("Add CLI", "feature", ["src/cli.py", "tests/test_cli.py"], []),
        ],
    }
    tasks, changes = await _decompose(data)
    assert list(tasks) == ["Add tests for parser", "Add CLI"]
    assert not any("merged" in c for c in changes)


async def test_reverse_merge_skips_when_it_would_create_a_cycle() -> None:
    """T(테스트)에 의존하는 X가 있고 구현 D가 X에 의존하면, T를 D에 합치면 X↔D 사이클 — 건너뛴다."""
    data = {
        "epics": [{"title": "E", "order": 1, "summary": "s"}],
        "tasks": [
            _task("Contract", "test", ["tests/test_engine.py"], [], role="test"),
            _task("Fixtures", "feature", ["src/fixtures.py", "tests/test_fixtures.py"], ["Contract"]),
            _task("Engine", "feature", ["src/engine.py", "tests/test_engine.py"],
                  ["Contract", "Fixtures"]),
        ],
    }  # fmt: skip
    tasks, changes = await _decompose(data)
    assert "Contract" in tasks
    assert not any("merged test-only task 'Contract'" in c for c in changes)
