"""컨텍스트 조립 (설계 §5.3): system → policy → CONTEXT.md → Role 노트 → Task spec → 요약 → 파일.

토큰 예산(``len/4`` 추정)을 넘으면 **뒤에서부터** 섹션을 비운다. system은 절대 비우지 않는다.
CONTEXT.md가 없으면 경고만 남기고 계속한다 (§5.1 "읽기 전에 쓰지 않는다"의 최소 형태).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from agents.base import AgentInput
from agents.llm.base import estimate_tokens

log = structlog.get_logger(__name__)

SECTION_ORDER = (
    "system",
    "policy",
    "context_md",
    "role_notes",
    "task_spec",
    "related_summaries",
    "related_files",
)


@dataclass(frozen=True)
class Section:
    name: str
    text: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class AssembledContext:
    sections: list[Section]
    dropped: list[str] = field(default_factory=list)

    @property
    def system(self) -> str:
        return self.sections[0].text if self.sections else ""

    @property
    def tokens(self) -> int:
        return sum(s.tokens for s in self.sections)

    def render(self, *, include_system: bool = True) -> str:
        parts: list[str] = []
        for s in self.sections:
            if not s.text or (s.name == "system" and not include_system):
                continue
            title = s.name.replace("_", " ")
            parts.append(f"## {title}\n{s.text}" if s.name != "system" else s.text)
        return "\n\n".join(parts)

    def user_message(self) -> str:
        """system을 뺀 나머지 — LLM user 턴."""
        return self.render(include_system=False)


def _files_block(files: dict[str, str] | None) -> str:
    if not files:
        return ""
    return "\n\n".join(f"### {path}\n```\n{body}\n```" for path, body in files.items())


def assemble_context(
    input: AgentInput,
    *,
    token_budget: int,
    system: str,
    related_files: dict[str, str] | None = None,
) -> AssembledContext:
    pc = input.project_context
    if pc.context_md is None:
        log.warning("context.missing_context_md", repo=pc.repo, task_id=input.task.id)
    task_text = f"# Task: {input.task.title}\n{input.task.spec}"
    if input.task.owned_paths:
        task_text += (
            "\n\nowned_paths (you may modify only these; put tests ONLY in the tests/ paths "
            "listed here, never create other test files):\n"
            + "\n".join(f"- {p}" for p in input.task.owned_paths)
        )
    sections = [
        Section("system", system),
        Section("policy", pc.policy_summary),
        Section("context_md", pc.context_md or ""),
        Section("role_notes", input.memory.role_notes),
        Section("task_spec", task_text),
        Section("related_summaries", "\n".join(f"- {s}" for s in pc.related_summaries)),
        Section("related_files", _files_block(related_files)),
    ]
    dropped: list[str] = []
    total = sum(s.tokens for s in sections)
    idx = len(sections) - 1
    while total > token_budget and idx > 0:
        if sections[idx].text:
            total -= sections[idx].tokens
            dropped.append(sections[idx].name)
            sections[idx] = Section(sections[idx].name, "")
        idx -= 1
    if dropped:
        log.info("context.trimmed", dropped=dropped, tokens=total, budget=token_budget)
    return AssembledContext(sections=sections, dropped=dropped)
