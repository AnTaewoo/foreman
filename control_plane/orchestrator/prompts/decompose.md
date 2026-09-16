<!--
근거 (decompose 노드, 설계 §4.1 Task, §10.1 owned_paths; X.2 2026-09-13 PC-3/4/5 결과로 규칙 추가):
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
  Produce at least 3 Tasks and at most 8, following the plan's Task Graph numbering.
- Names under "Symbols" in the repository summary already exist (including `route GET /users`
  style entries = routes already implemented). Never create a Task whose job is to add or
  "implement" one of them; write Tasks that extend or use them (say which symbol).
- "owned_paths" is REQUIRED and must not be empty: list the exact files (or narrow globs) the Task
  may modify. Two Tasks that touch the same file must be ordered with "depends_on". Tasks that
  touch different files must NOT depend on each other unless one imports the other's new code.
- "depends_on" refers to other Task titles in this same list. No cycles, no self reference.
- Titles must be unique. Every "epic" must be one of the titles in "epics".
- "spec" must state, for the tests the Task adds: the test file, each test name, and the exact
  expected values it asserts (e.g. `add_user("ann") == {{"id": 1, "name": "ann"}}`). Tests start
  from fresh state of the same objects the code uses (clear the module-level store the app reads,
  or build the app fresh) — never assume ids left by other tests, never fill a separate object the
  code does not read. Use the import style of the existing tests (see pytest `pythonpath`).
- A Task that would add a dependency or change a schema/public API gets estimated_tier "T2" and
  says so in its spec.
- Only Tasks that serve the Acceptance Criteria. No "optional", "refactor" or "nice to have" Tasks.
- Tests live in the same Task as the code they verify. Never make separate "write tests for X"
  Tasks. Every Task that writes code MUST list the test file it writes in "owned_paths"
  (e.g. "tests/test_calculator.py") — a Task cannot write files outside its owned_paths.
