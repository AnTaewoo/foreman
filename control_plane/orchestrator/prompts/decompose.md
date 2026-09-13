<!--
근거 (decompose 노드, 설계 §4.1 Task, §10.1 owned_paths):
- JSON 스키마를 인라인으로 박아 구조화 출력이 실패해도 재요청 메시지가 스키마를 다시 보여준다.
- owned_paths를 필수·구체 경로로 강제해야 Scheduler가 겹침을 직렬화할 수 있다 (§10.1).
- Task 크기(30분~2시간)와 depends_on(제목 참조)을 명시해 너무 잘게 쪼개거나 순환을 만들지 않게 한다.
-->
# Goal

{goal}

# Approved plan

{plan}

# Repository summary

{repo_summary}

# Instructions

Decompose the approved plan into Tasks. Return ONLY a JSON object matching this schema:

{{
  "epics": [ {{"title": "string", "order": 1, "summary": "string"}} ],
  "tasks": [
    {{
      "title": "string (unique)",
      "spec": "markdown: what to change, where, how to verify (test names)",
      "kind": "feature | bugfix | test | refactor | research | fix_from_review | fix_from_test",
      "role_required": "coding | architect | research | test | review",
      "depends_on": ["other task title", "..."],
      "owned_paths": ["src/app/users.py", "tests/test_users.py"],
      "estimated_tier": "T0 | T1 | T2 | T3",
      "epic": "epic title from the epics list"
    }}
  ]
}}

Rules:
- Each Task takes one agent 30 minutes to 2 hours. Do not split below that; do not exceed it.
- "owned_paths" is REQUIRED and must not be empty: list the exact files (or narrow globs) the Task
  may modify. Two Tasks that touch the same file must be ordered with "depends_on".
- "depends_on" refers to other Task titles in this same list. No cycles, no self reference.
- Titles must be unique. Every "epic" must be one of the titles in "epics".
- A Task that would add a dependency or change a schema/public API gets estimated_tier "T2" and
  says so in its spec.
- Prefer 3–8 Tasks. Put tests inside the same Task as the code they verify unless the plan says
  otherwise.
