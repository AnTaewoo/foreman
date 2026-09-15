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
    for _ in range(attempts):
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
            return parsed
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
