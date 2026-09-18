"""GitHub 본문 마커 (설계 §7.3). Issue/PR/코멘트에 남긴 마커를 찾아 재생성을 막는다(멱등)."""

from __future__ import annotations

import re

from github_adapter.protocol import PrMeta

ISSUE_MARKER_RE = re.compile(r"<!--\s*ai-platform:meta\s+task=(?P<task>\S+)\s*-->")
PR_MARKER_RE = re.compile(
    r"<!--\s*ai-platform:meta\s+task=(?P<task>\S+)\s+run=(?P<run>\S+)"
    r"\s+agent=(?P<agent>\S+)\s+tier=(?P<tier>\S+)\s*-->"
)
COMMENT_MARKER_RE = re.compile(r"<!--\s*ai-platform:comment\s+key=(?P<key>\S+)\s*-->")
PLAN_MARKER_RE = re.compile(r"<!--\s*ai-platform:plan\s+key=(?P<key>\S+)\s*-->")


def plan_marker(key: str) -> str:
    """P9.11: Plans 카테고리가 없는 repo의 Plan Issue (key = ``<goal>-<revision>``)."""
    return f"<!-- ai-platform:plan key={key} -->"


def parse_plan_marker(body: str | None) -> str | None:
    if not body:
        return None
    m = PLAN_MARKER_RE.search(body)
    return m.group("key") if m else None


def issue_marker(task_id: str) -> str:
    return f"<!-- ai-platform:meta task={task_id} -->"


def parse_issue_marker(body: str | None) -> str | None:
    if not body:
        return None
    m = ISSUE_MARKER_RE.search(body)
    return m.group("task") if m else None


def pr_marker(meta: PrMeta) -> str:
    return (
        f"<!-- ai-platform:meta task={meta.task_id} run={meta.run_id} "
        f"agent={meta.agent_id} tier={meta.tier} -->"
    )


def _box(ok: bool) -> str:
    return "[x]" if ok else "[ ]"


def pr_meta_block(
    meta: PrMeta,
    *,
    summary: str = "",
    changes: str = "",
    tests: str = "",
    decisions: list[str] | None = None,
    owned_paths_only: bool = True,
    no_new_dependency: bool = True,
) -> str:
    """§7.3 PR 본문 상단 블록."""
    decision_lines = "\n".join(f"- {d}" for d in (decisions or [])) or "- (none)"
    return "\n".join(
        [
            pr_marker(meta),
            "### Summary",
            summary,
            "### Changes",
            changes,
            "### Tests",
            tests,
            "### Decisions referenced",
            decision_lines,
            "### Checklist",
            f"- {_box(owned_paths_only)} owned_paths only",
            f"- {_box(no_new_dependency)} no new dependency",
        ]
    )


def parse_pr_meta(body: str | None) -> PrMeta | None:
    if not body:
        return None
    m = PR_MARKER_RE.search(body)
    if not m:
        return None
    return PrMeta(
        task_id=m.group("task"), run_id=m.group("run"), agent_id=m.group("agent"),
        tier=m.group("tier"),
    )  # fmt: skip


def comment_marker(key: str) -> str:
    return f"<!-- ai-platform:comment key={key} -->"


def parse_comment_marker(body: str | None) -> str | None:
    if not body:
        return None
    m = COMMENT_MARKER_RE.search(body)
    return m.group("key") if m else None
