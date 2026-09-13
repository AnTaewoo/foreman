<!--
근거 (draft_plan 노드, 설계 §5.2 Plan 형식; X.2 2026-09-13 Task Graph를 T-n 형식으로):
- 출력은 PlanDraft JSON(구조화 출력)으로 받고 마크다운 렌더링은 코드가 한다 — 6섹션 누락을 코드로 검증 가능.
- Acceptance Criteria를 먼저 쓰게 해 Task 분해가 목표에서 벗어나지 않게 한다.
- Epic 수와 Task 수를 대략 예측시켜(Budget) 사람이 승인 전에 규모를 본다.
-->
# Goal

{goal}

# Repository summary

{repo_summary}

# Instructions

Produce a Plan for this Goal as a JSON object with exactly these fields
(the platform renders them into the six sections Understanding / Acceptance Criteria / Epics /
Task Graph / Decisions Expected / Budget Estimate):

- "understanding": 3–8 sentences. What the repository is, where the change goes, constraints you see.
- "acceptance_criteria": list of testable statements (each becomes a checkbox "AC-n").
- "epics": list of {{"title", "order", "summary", "task_count", "risk_tier"}} — 1 to 4 epics,
  risk_tier one of T0/T1/T2/T3 (T2+ means a human decision is expected). Tests belong to the
  feature they verify: no separate "tests" epic.
- "task_graph": numbered tasks "T-1: <short title>" one per line, then dependency arrows using
  those numbers, e.g. "T-1 → T-2, T-1 → T-3, T-2 → T-4". Tasks that touch different files should
  not depend on each other. The decomposition step will follow this numbering.
- "decisions_expected": list of decisions humans must make (new dependency, schema change, ...).
  Use ["none"] if there are none.
- "budget_estimate": one line, e.g. "~$3, ~6 runs".

Keep it grounded in the repository summary. Do not list Tasks here — that is the next step.
