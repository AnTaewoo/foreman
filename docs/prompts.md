# Claude Code 프롬프트 모음 (MVP 1)

사용 원칙
- 한 프롬프트 = 한 세션 = 하나의 PR. 다음 프롬프트로 넘어가기 전에 `make check` 통과 + 직접 코드 확인.
- 각 프롬프트 시작 전 `/clear` 로 컨텍스트 초기화. CLAUDE.md와 docs/design.md는 자동으로 읽힌다.
- 설계 결정이 필요한 지점에서는 Claude Code가 묻게 되어 있다(CLAUDE.md). 대답을 미루지 말고 그때 결정한다.
- 큰 프롬프트(P3, P4)는 먼저 plan mode(`Shift+Tab`)로 계획을 받아보고 승인한 뒤 실행.

---

## P0. 스캐폴딩

```
docs/design.md 의 §14 기술 스택과 §15.1 디렉토리 구조에 맞춰 프로젝트를 초기화해줘.

해야 할 것:
1. uv 기반 pyproject.toml — 의존성: fastapi, uvicorn[standard], langgraph, langchain-core, sqlalchemy[asyncio], asyncpg, alembic, redis, pygithub, httpx, pydantic-settings, python-ulid, structlog. dev: pytest, pytest-asyncio, respx, ruff, mypy, pre-commit
2. §15.1 디렉토리를 __init__.py 포함해 전부 생성. 각 패키지 최상단에 역할을 한 줄 docstring으로.
3. Makefile: install / check (ruff+mypy+pytest) / run-api / run-worker / migrate / docker-up / docker-down
4. docker-compose.yml: postgres 16, redis 7, minio. 볼륨과 healthcheck 포함.
5. control_plane/config.py: pydantic-settings 기반 Settings. 필드는 .env.example 과 동일하게. DRY_RUN 기본 true.
6. .env.example (값은 비워두고 주석으로 설명)
7. ruff / mypy 설정, pre-commit 훅
8. 빈 FastAPI 앱 (control_plane/api/app.py) 에 GET /health 만.

아직 로직은 만들지 마. 마지막에 `make check` 가 통과하는 것을 보여주고, 만든 파일 목록을 요약해줘.
```

---

## P1. 이벤트 스키마 + State Store

```
docs/design.md §4 데이터 모델을 구현해줘. 이 단계의 목표는 이벤트 스키마를 확정하는 것이고, 이후 단계에서는 필드 추가만 가능하다는 점을 염두에 둬.

범위:
1. control_plane/events/schema.py — §4.2 이벤트 타입을 Enum으로, Event 를 pydantic 모델로. actor, subject, payload, correlation_id, causation_id, signature(이전 이벤트 signature + 본문의 sha256 해시 체인) 포함.
2. control_plane/store/models.py — SQLAlchemy 모델: Project, Goal, Epic, Task, Run, Decision, Agent, Event. Decision/Agent 는 MVP1에서 쓰지 않지만 테이블은 만든다. Task.status 는 §6.1 상태값을 Enum으로.
3. alembic 초기 마이그레이션.
4. control_plane/events/bus.py — Redis Streams 기반 publish / subscribe(consumer group). publish 는 Event 를 받아 DB events 테이블에 append 한 뒤 stream 에 넣는다 (같은 트랜잭션 보장이 어려우면 outbox 패턴으로 — 어떤 방식을 택했는지 설명해줘).
5. control_plane/events/projection.py — 이벤트를 받아 Task/Goal/Run 등의 상태를 갱신하는 유일한 곳. MVP1에 필요한 이벤트만 핸들러 구현, 나머지는 명시적 no-op.
6. 테스트: 해시 체인 검증, projection 이 task.created → task.assigned → task.started → task.completed 를 순서대로 처리했을 때 Task 상태가 맞는지, 순서 뒤바뀐 이벤트 처리 시 동작(어떻게 할지 옵션 제시 후 결정).

주의: Task 의 §6.1 상태 전이 중 허용되지 않는 전이는 projection 에서 예외를 던져야 한다. 전이 테이블을 한 곳(control_plane/store/transitions.py)에 두고 테스트로 전부 커버해줘.
```

---

## P2. GitHub Adapter

```
docs/design.md §7 GitHub 매핑 규약을 구현해줘. github_adapter/ 패키지.

1. auth.py — GitHub App JWT → installation token 발급/캐시/갱신.
2. client.py — 다음 메서드. 모두 멱등:
   - ensure_labels(repo): §7.2 라벨 스키마 전부 생성 (이미 있으면 skip)
   - create_task_issue(repo, task): 본문 상단에 `<!-- ai-platform:meta task=<id> -->` 마커. 같은 task id 마커가 있는 Issue가 이미 있으면 그걸 반환.
   - update_issue_status_label(repo, issue_number, status)
   - comment(repo, issue_or_pr_number, body)
   - create_branch(repo, name, from_ref)
   - open_pr(repo, head, base, title, body, draft) — 본문에 §7.3 메타 블록
   - create_milestone(repo, epic)
   - Discussions 는 GraphQL 필요: create_discussion(repo, category, title, body), list_discussions. httpx 로 직접 구현.
3. webhooks.py — FastAPI 라우터 POST /webhooks/github. HMAC 서명 검증. issues / issue_comment / pull_request / pull_request_review / check_suite / push 이벤트를 §4.2 내부 Event 로 변환해 bus 에 publish. 매핑 표를 docstring 으로.
4. DRY_RUN=true 일 때는 실제 API 를 호출하지 않고 structlog 로 "would call ..." 을 남기고 가짜 번호를 반환.
5. 테스트: respx 로 REST mock, 멱등성(같은 task 두 번 → Issue 하나), 웹훅 서명 실패 시 401, 각 웹훅 → 이벤트 변환.

GitHub GraphQL Discussions API 의 정확한 mutation 이름과 필드는 추측하지 말고 확인이 필요하면 나에게 물어봐.
```

---

## P3. Orchestrator (LangGraph) — plan mode 권장

```
docs/design.md §5.2 Orchestrator 와 §15.1 의 MVP1 그래프를 구현해줘:

analyze_repo → draft_plan → wait_plan_approval → decompose → emit_issues → END

control_plane/orchestrator/ 에:
1. context.py — repo 를 얕게 clone (또는 GitHub tree API) 해서 언어/프레임워크/디렉토리/테스트 러너/기존 docs 를 요약한 RepoSummary 생성. 파일 내용은 README, 설정 파일, 상위 2 depth 트리까지만.
2. graph.py — LangGraph StateGraph. State 에 goal, repo_summary, plan(§5.2 Plan 형식), tasks[] 포함. wait_plan_approval 은 LangGraph interrupt 로 구현하고, Plan Discussion 을 만든 뒤 멈춘다. 재개는 외부에서 (webhook 으로 `/approve` 코멘트 수신 시) Command(resume=...) 로.
3. prompts/ — 각 노드 프롬프트를 별도 .md 파일로. analyze / plan / decompose. decompose 출력은 JSON 스키마(TaskDraft: title, spec, kind, role_required, depends_on(제목 참조), owned_paths, estimated_tier)로 강제하고 pydantic 으로 검증, 실패 시 1회 재시도.
4. emit_issues — TaskDraft 들을 의존성 순서로 정렬(사이클이면 실패 이벤트), Task 생성 이벤트 발행, github_adapter 로 Issue 생성, Task 에 issue_number 반영. owned_paths 가 겹치는 Task 쌍은 depends_on 으로 직렬화 (§10.1).
5. LangGraph 체크포인터는 Postgres 로.
6. agents/llm.py 의 ModelProvider 인터페이스 (complete(messages, schema=None) -> str | BaseModel) 와 anthropic 어댑터를 여기서 같이 만들어. 테스트에서는 FakeProvider 로 대체.

테스트: FakeProvider 가 고정 JSON 을 반환할 때 그래프가 interrupt 에서 멈추고, resume 후 Issue 가 의존성 순서로 생성되는지. 사이클이 있는 decompose 결과에 대한 처리.

프롬프트 파일의 내용은 초안이면 된다. 대신 어떤 정보를 프롬프트에 넣었고 왜인지 짧게 설명해줘.
```

---

## P4. Coding Agent + Worker — plan mode 권장

```
docs/design.md §5.1 공통 계약, §5.2 Coding Agent, §10.3 워커 격리, §15.1 Coding Agent 루프를 구현해줘.

agents/:
1. base.py — AgentInput / AgentOutput (§5.1 그대로), BaseAgent.run(). 컨텍스트 조립 순서 §5.3.
2. tools/ — git.py(branch, commit, push, diff), fs.py(read/write/list, worktree 밖 경로 차단, .env* 및 *.pem 읽기 차단), shell.py(허용 프리픽스 목록 기반, 타임아웃), github.py(open_pr, comment 만). 각 툴은 호출 시 run.tool_called 이벤트 발행.
3. coding.py — LangGraph: load_context → plan_changes → edit → run_tests → (pass) commit_push → open_pr → summarize / (fail, attempt<3) edit. owned_paths 밖 파일이 diff 에 있으면 즉시 실패로 종료하고 사유 기록. 새 dependency 파일(pyproject, package.json 등) 변경 감지 시 outcome=needs_decision 으로 종료 (MVP1 에서는 그냥 Issue 코멘트로 "승인 필요" 남기고 Task 를 blocked 로).

worker/:
4. Dockerfile — python 3.12 + git + node (테스트 러너 대비). non-root.
5. entrypoint.py — 인자로 task_id, 환경변수로 repo/branch/installation token(단기). worktree 준비 → CodingAgent 실행 → 결과를 run.finished 이벤트로 발행 → 컨테이너 종료. 45분 타임아웃.

control_plane/scheduler/:
6. scheduler.py — task.created 를 구독, depends_on 이 모두 done 인 Task 를 ready 로 전이, max_workers 이하로 워커 컨테이너 실행(docker SDK). 워커 종료 시 Task 상태 갱신은 projection 이 run.finished 를 보고 처리.

테스트: 툴 차단 규칙 단위 테스트(경로 탈출, .env, 비허용 명령), FakeProvider 로 CodingAgent 그래프의 pass/fail/retry 경로, needs_decision 경로. 워커 통합 테스트는 tests/integration 에 두고 Docker 없으면 skip.

docker SDK 대신 subprocess 로 docker CLI 를 호출하는 게 낫다고 판단되면 이유와 함께 제안해줘.
```

---

## P5. API + 엔드투엔드

```
docs/design.md §13 API 중 MVP1 에 필요한 것만 구현하고, 전체를 한 번 이어서 돌려보자.

1. control_plane/api/ — POST /projects, GET /projects/{id}, POST /projects/{id}/goals, GET /projects/{id}/goals/{gid}, GET /projects/{id}/tasks, GET /projects/{id}/events, WS /projects/{id}/stream. 쓰기 API 는 Idempotency-Key 헤더 지원.
2. POST /goals 는 Orchestrator 그래프를 백그라운드로 시작한다.
3. webhook 의 issue_comment 에서 Plan Discussion 에 `/approve` 가 달리면 그래프를 resume 한다 (댓글 작성자가 project.members 의 owner/approver 인지 확인).
4. scripts/e2e_dry_run.py — DRY_RUN=true, FakeProvider 대신 실제 anthropic provider 를 쓰되 GitHub 는 dry run 으로: 샘플 repo 요약 → Plan 출력 → (자동 approve) → Task 목록과 "would create issue ..." 로그를 출력. 이걸로 프롬프트 품질을 눈으로 확인할 수 있게.
5. docs/runbook.md — 로컬에서 전체를 띄우는 순서, 흔한 에러.

실행해서 e2e_dry_run 출력 전체를 보여줘. Plan 과 Task 분해 품질에 대해 네가 보기에 약한 부분도 같이 말해줘.
```

---

## 이후 세션에서 쓰는 짧은 프롬프트들

```
# 실제 GitHub 에 처음 붙일 때
DRY_RUN=false 로 tests/fixtures/sample-repo 를 fork 한 내 테스트 저장소에 Goal 하나를 실행하려고 해. 실행 전에 필요한 GitHub App 설정과 .env 값을 체크리스트로 확인해주고, 실행 중 생성되는 모든 GitHub 리소스를 마지막에 정리하는 scripts/cleanup_repo.py 도 만들어줘.

# 프롬프트 튜닝
e2e_dry_run 결과에서 Task 분해가 너무 잘게 쪼개졌어. control_plane/orchestrator/prompts/decompose.md 를 수정해서 Task 하나가 30분~2시간 규모가 되도록 유도하고, 같은 FakeProvider 테스트가 여전히 통과하는지 확인해줘.

# 설계 변경이 필요할 때
§6.1 의 Task 상태에 `awaiting_rebase` 를 추가하고 싶어. 먼저 docs/design.md 를 수정하는 diff 를 보여주고, 내가 승인하면 transitions.py 와 projection 을 바꿔.

# MVP2 진입
CLAUDE.md 의 "현재 단계" 를 MVP 2 로 올리고, docs/design.md §5.2 의 Review Agent 부터 시작하자. 먼저 plan mode 로 계획만 세워줘.
```
