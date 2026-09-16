"""Orchestrator 산출물 모델과 프롬프트 (설계 §5.2 Plan 형식, §4.1 Task).

- ``PlanDraft`` → ``to_markdown()``이 §5.2의 6섹션 Discussion 본문을 만든다.
- ``DecomposeResult``(epics + ``TaskDraft`` 목록)는 제목 유일·depends_on·자기 참조·epic 참조 검증.
- ``decompose_with_retry``: 실패 시 이전 응답+오류를 붙여 재요청, 또 실패면 ``DecomposeError``.
- ``render_prompt(name, **vars)``: ``prompts/<name>.md``의 상단 ``<!-- -->`` 근거 주석을 떼고 치환.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents.llm.base import Message, ModelProvider

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

PLAN_SECTIONS: tuple[str, ...] = (
    "Understanding",
    "Acceptance Criteria",
    "Epics",
    "Task Graph",
    "Decisions Expected",
    "Budget Estimate",
)

TaskKindValue = Literal[
    "feature", "bugfix", "test", "refactor", "research", "fix_from_review", "fix_from_test"
]  # fmt: skip
RoleValue = Literal["coding", "architect", "research", "test", "review"]
TierValue = Literal["T0", "T1", "T2", "T3"]


log = structlog.get_logger(__name__)


class DecomposeError(Exception):
    """재시도 후에도 유효한 분해를 얻지 못했다."""


# --------------------------------------------------------------------------- Plan


class EpicDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1)
    order: int = 1
    summary: str = ""
    task_count: int | None = None
    risk_tier: TierValue = "T1"


class PlanDraft(BaseModel):
    """§5.2 Plan 산출 형식."""

    model_config = ConfigDict(extra="ignore")

    understanding: str
    acceptance_criteria: list[str] = Field(min_length=1)
    epics: list[EpicDraft] = Field(min_length=1)
    task_graph: str = ""
    decisions_expected: list[str] = Field(default_factory=lambda: ["none"])
    budget_estimate: str = ""

    def to_markdown(self, *, goal_title: str, goal_number: int | str = "?") -> str:
        lines = [
            f"## Plan for Goal #{goal_number}: {goal_title}",
            f"### {PLAN_SECTIONS[0]}",
            self.understanding.strip(),
            f"### {PLAN_SECTIONS[1]}",
            *[
                f"- [ ] AC-{i} {_strip_ac_prefix(ac)}"
                for i, ac in enumerate(self.acceptance_criteria, start=1)
            ],
            f"### {PLAN_SECTIONS[2]}",
        ]
        for i, epic in enumerate(sorted(self.epics, key=lambda e: e.order), start=1):
            count = "?" if epic.task_count is None else str(epic.task_count)
            lines.append(f"{i}. {epic.title} — Tasks: {count}, est. risk: {epic.risk_tier}")
            if epic.summary:
                lines.append(f"   {epic.summary}")
        lines += [
            f"### {PLAN_SECTIONS[3]}",
            self.task_graph.strip() or "(single task)",
            f"### {PLAN_SECTIONS[4]}",
            *[f"- {d}" for d in (self.decisions_expected or ["none"])],
            f"### {PLAN_SECTIONS[5]}",
            self.budget_estimate.strip() or "(not estimated)",
        ]
        return "\n".join(lines)


_AC_PREFIX = re.compile(r"^\s*AC-?\d+\s*[:.)-]?\s*", re.IGNORECASE)


def _strip_ac_prefix(text: str) -> str:
    """모델이 'AC-1: …'처럼 접두를 이미 붙였으면 떼고 렌더링한다 (PC-3에서 발견)."""
    return _AC_PREFIX.sub("", text.strip(), count=1)


def missing_plan_sections(markdown: str) -> list[str]:
    return [s for s in PLAN_SECTIONS if f"### {s}" not in markdown]


# --------------------------------------------------------------------------- Tasks


class TaskDraft(BaseModel):
    """LLM이 내놓는 Task 초안. ``depends_on``은 제목 참조 (emit이 id로 바꾼다)."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1)
    spec: str
    kind: TaskKindValue = "feature"
    role_required: RoleValue = "coding"
    depends_on: list[str] = Field(default_factory=list)
    owned_paths: list[str] = Field(min_length=1)
    estimated_tier: TierValue = "T1"
    epic: str = ""


_REF_RE = re.compile(r"^(T-\d+)\s*:?\s*$")


def _resolve_ref(dep: str, titles: list[str]) -> str:
    """depends_on 정규화: 제목이면 그대로, "T-1"·"T-1:" 번호면 그 번호로 시작하는 제목."""
    dep = dep.strip()
    if dep in titles:
        return dep
    m = _REF_RE.match(dep)
    if m:
        num = m.group(1)
        hits = [t for t in titles if re.match(rf"^{re.escape(num)}\b", t)]
        if len(hits) == 1:
            return hits[0]
    return dep


class DecomposeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    epics: list[EpicDraft] = Field(default_factory=list)
    tasks: list[TaskDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_references(self) -> DecomposeResult:
        titles = [t.title for t in self.tasks]
        if len(set(titles)) != len(titles):
            dupes = sorted({t for t in titles if titles.count(t) > 1})
            raise ValueError(f"task titles must be unique: {dupes}")
        known = set(titles)
        epic_titles = {e.title for e in self.epics}
        for t in self.tasks:
            t.depends_on = [_resolve_ref(dep, titles) for dep in t.depends_on]  # PC-7: "T-1" → 제목
            for dep in t.depends_on:
                if dep == t.title:
                    raise ValueError(f"task {t.title!r} depends on itself")
                if dep not in known:
                    raise ValueError(f"task {t.title!r} depends on unknown task {dep!r}")
            if t.epic and epic_titles and t.epic not in epic_titles:
                raise ValueError(f"task {t.title!r} references unknown epic {t.epic!r}")
        return self


# --------------------------------------------------------------------------- Prompts


def render_prompt(name: str, **variables: str) -> str:
    """``prompts/<name>.md`` → 근거 주석 제거 + ``str.format`` 치환 (빠진 변수는 KeyError)."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    if text.startswith("<!--"):
        end = text.index("-->") + len("-->")
        text = text[end:].lstrip("\n")
    return text.format(**variables)


def system_prompt() -> str:
    return render_prompt("analyze")


# --------------------------------------------------------------------------- decompose


# --------------------------------------------------------------------------- P9: 테스트 경로 정규화
# foreman_demo 1차 Goal(2026-09-16): 분해가 소스만 owned_paths로 주고 모델은 tests/를 쓰려 해
# 9/9회 scope_violation. 프롬프트 문구가 아니라 코드가 (1) 검증→재요청 (2) 마지막엔 보강한다.
_TEST_DIRS = ("tests", "test")
_GENERIC_DIRS = frozenset({"src", "app", "lib", "pkg", "core", "server", "api"} - {"server", "api"})
_CODE_KINDS = frozenset({"feature", "bugfix", "fix_from_review", "fix_from_test", "test"})


def is_test_path(path: str) -> bool:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if parts[0] in _TEST_DIRS:
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def _is_code_task(t: TaskDraft) -> bool:
    return t.role_required == "coding" and t.kind in _CODE_KINDS


def missing_test_paths(result: DecomposeResult) -> list[str]:
    """테스트 경로를 하나도 소유하지 않은 코드 Task 제목들."""
    return [
        t.title
        for t in result.tasks
        if _is_code_task(t) and not any(is_test_path(p) for p in t.owned_paths)
    ]


def suggested_test_paths(owned: list[str]) -> list[str]:
    """소유 소스 경로에서 테스트 파일 후보: 파일 stem + 디렉토리 이름 (모델이 고르는 이름)."""
    out: list[str] = []

    def add(stem: str) -> None:
        cand = f"tests/test_{stem}.py"
        if stem and cand not in out:
            out.append(cand)

    for raw in owned:
        parts = [p for p in raw.replace("\\", "/").split("/") if p and "*" not in p]
        if not parts or is_test_path(raw):
            continue
        name = parts[-1]
        if name.endswith(".py") and name != "__init__.py":
            add(name[:-3])
        for d in parts[:-1] if name.endswith(".py") or "." in name else parts:
            if d not in _GENERIC_DIRS and not d.startswith("."):
                add(d)
    return out


def merge_test_only_tasks(result: DecomposeResult) -> tuple[DecomposeResult, list[str]]:
    """테스트만 소유하고 구현 Task 하나에 의존하는 Task는 그 구현 Task에 합친다 (패턴 2)."""
    notes: list[str] = []
    tasks = list(result.tasks)
    by_title = {t.title: t for t in tasks}
    merged_into: dict[str, str] = {}
    for t in list(tasks):
        only_tests = bool(t.owned_paths) and all(is_test_path(p) for p in t.owned_paths)
        if not only_tests or len(t.depends_on) != 1:
            continue
        target = by_title.get(t.depends_on[0])
        if target is None or target is t or all(is_test_path(p) for p in target.owned_paths):
            continue
        for p in t.owned_paths:
            if p not in target.owned_paths:
                target.owned_paths.append(p)
        target.spec = f"{target.spec}\n\nAlso (merged Task '{t.title}'): {t.spec}"
        merged_into[t.title] = target.title
        tasks.remove(t)
        notes.append(f"merged test-only task '{t.title}' into '{target.title}'")
    if merged_into:
        for t in tasks:
            deps: list[str] = []
            for d in t.depends_on:
                d2 = merged_into.get(d, d)
                if d2 != t.title and d2 not in deps:
                    deps.append(d2)
            t.depends_on = deps
    return DecomposeResult(epics=result.epics, tasks=tasks), notes


def augment_test_paths(result: DecomposeResult) -> tuple[DecomposeResult, list[str]]:
    """테스트 경로가 없는 코드 Task에 후보 경로를 보강한다(패턴 1: owned_paths 밖 테스트 쓰기)."""
    notes: list[str] = []
    for t in result.tasks:
        if _is_code_task(t) and not any(is_test_path(p) for p in t.owned_paths):
            extra = suggested_test_paths(t.owned_paths)
            t.owned_paths.extend(extra)
            notes.append(f"added test paths to '{t.title}': {extra}")
    return result, notes


def normalize_tasks(result: DecomposeResult) -> tuple[DecomposeResult, list[str]]:
    """merge_test_only_tasks → augment_test_paths (스크립트·테스트용 한 번에)."""
    merged, n1 = merge_test_only_tasks(result)
    out, n2 = augment_test_paths(merged)
    return out, [*n1, *n2]


async def decompose_with_retry(
    provider: ModelProvider,
    *,
    repo_summary: str,
    plan: str,
    goal: str,
    model: str | None = None,
    attempts: int = 2,
    min_tasks: int = 1,
) -> DecomposeResult:
    """LLM 분해 → 검증. 실패하면 이전 응답 + 오류를 붙여 재요청 (최대 ``attempts``회).

    ``min_tasks`` (X.2): 그보다 적게 쪼개면 오류를 붙여 재요청 — 작은 모델의 뭉뚱그리기 방지.
    """
    messages: list[Message] = [
        Message(
            role="user",
            content=render_prompt("decompose", goal=goal, plan=plan, repo_summary=repo_summary),
        )
    ]
    last_error = ""
    for attempt in range(attempts):
        completion = await provider.complete(
            messages, system=system_prompt(), schema=DecomposeResult, model=model
        )
        parsed = completion.parsed
        if parsed is None:
            try:
                parsed = DecomposeResult.model_validate_json(completion.text)
            except (ValidationError, ValueError) as exc:
                last_error = _short_error(exc)
                parsed = None
        if isinstance(parsed, DecomposeResult) and len(parsed.tasks) < min_tasks:
            last_error = (
                f"expected at least {min_tasks} tasks, got {len(parsed.tasks)} — split the work "
                "per the plan's Task Graph (one Task per T-n), each 30 minutes to 2 hours"
            )
            parsed = None
        if isinstance(parsed, DecomposeResult):
            parsed, merge_notes = merge_test_only_tasks(
                parsed
            )  # 합치면 테스트 경로가 채워질 수 있다
            missing = missing_test_paths(parsed)
            if missing and attempt < attempts - 1:
                last_error = (
                    "these Tasks own no test file — tests live in the same Task as the code, "
                    "so add the test file each Task writes (e.g. tests/test_<module>.py) "
                    "to its owned_paths: " + ", ".join(repr(m) for m in missing)
                )
                parsed = None
        if isinstance(parsed, DecomposeResult):
            normalized, notes = augment_test_paths(parsed)
            if merge_notes or notes:
                log.warning("decompose.normalized", changes=[*merge_notes, *notes])
            return normalized
        messages = [
            *messages,
            Message(role="assistant", content=completion.text or "(empty)"),
            Message(
                role="user",
                content=(
                    f"Your previous answer was rejected with this error:\n{last_error}\n"
                    "Return the corrected JSON object only, matching the schema above."
                ),
            ),
        ]
    raise DecomposeError(f"decompose failed after {attempts} attempts: {last_error}")


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
    return str(exc)[:500]


def task_titles(tasks: Sequence[TaskDraft]) -> list[str]:
    return [t.title for t in tasks]
