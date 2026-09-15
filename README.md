# Foreman — HITL Multi-Agent Dev Platform

GitHub 저장소와 Goal 한 문장을 넣으면, 역할별 AI 에이전트 팀이 **Plan → Issue → 브랜치 → PR**로 협업해 일을 진행하고,
위험한 결정(Plan 승인, PR 머지, 의존성 추가 등)만 사람이 GitHub 위에서 승인하는 플랫폼입니다.

- 모든 상태 변화는 **서명된 append-only 이벤트**로만 기록됩니다(이벤트 소싱, 해시 체인).
- 코드를 고치는 워커는 **격리된 컨테이너**에서 돌고, DB·시크릿·GitHub 토큰을 받지 않습니다.
- GitHub 쓰기(Issue/PR/Discussion/push)는 전부 **멱등**이고, 기본은 `DRY_RUN=true`(실 호출 0)입니다.

현재 단계: **MVP 1** — Repo → Goal → Orchestrator → Issue → Coding Agent → PR. 실 GitHub 연결(App)까지 검증됨(PC-7).
설계 원문은 `docs/design.md`, 실행 계획·결정 로그는 `ROADMAP.md`, 운영 절차는 `docs/runbook.md`.

## 한눈에 보는 흐름

```
사람  POST /projects {repo}  →  POST /goals {title}
          │
          ▼
Orchestrator (LangGraph)  repo 요약 → Plan(6섹션) → GitHub Discussion "Plan #n"   ── interrupt ──▶  사람이 /approve 코멘트
          │  (승인 웹훅으로 resume)
          ▼
분해 → Epic/Task 그래프 → Milestone + Issue 생성 (task.created)
          │
          ▼
Scheduler  ready ∧ 의존 done ∧ 파일 겹침 없음 → task.assigned → 워커 기동 (docker | in-process)
          │
          ▼
Coding Agent (워커)  clone → 계획 → 편집(owned_paths 안에서만) → 테스트 → 커밋(트레일러) → push → task.completed
          │
          ▼
Control plane  PrOpener: 브랜치를 GitHub로 push → draft PR + Issue 요약 코멘트 (pr.opened)
          │
          ▼
사람이 PR 머지  → 웹훅 pull_request.closed → pr.merged → Task done → 의존 Task 배정 …
```

Dry 모드(`HITL_DRY_RUN=true`)에서는 GitHub 쓰기가 메모리에서만 일어나고, `DryMerger`가 사람 머지를 대신해 로컬 git `main`을 옮깁니다.

## 아키텍처

```
┌─ API 프로세스 (uvicorn control_plane.api.app:app) ────────────────────────────┐
│ FastAPI: /projects /goals /tasks /events  WS /stream  /webhooks/github        │
│ GoalRunner: LangGraph Orchestrator (thread_id = goal_id, Postgres 체크포인터) │
└───────────────┬────────────────────────────────────────────────────────────────┘
                │ EventBus.publish = chain.append_signed → events 테이블 (outbox)
                ▼
     Postgres 16 (events / tool_calls / projects goals epics tasks runs …)
     Redis 7 Streams  events:{project_id}  (+ :retry 지연 재시도 큐)
                ▲
┌─ 상주 control plane (python -m control_plane) ─────────────────────────────────┐
│ OutboxRelay → consumer group 한 개에서 순서대로:                                │
│   Projection(유일한 DB 갱신자) → Scheduler(배정·워커 기동·ingest)              │
│   → PrOpener(브랜치 push + PR) → DryMerger(dry 모드만)                          │
│ retry 루프(D-30), 죽은 워커 정리(P8)                                            │
└───────────────┬────────────────────────────────────────────────────────────────┘
                │ docker run --user <uid> -v <repo> … foreman-worker  (또는 in-process)
                ▼
     워커 컨테이너  WORKER_* env만 · Redis XADD(미서명 → Scheduler.ingest가 서명)
     Coding Agent + 툴(fs / shell / git / github) — 경로·명령·브랜치 가드레일
```

핵심 불변식

| 원칙 | 코드 |
|---|---|
| 이벤트 append 경로는 하나 | `control_plane/events/chain.py::append_signed` (프로젝트별 락, SHA-256 체인, `canonical_json`) |
| DB 갱신은 projection만 | `control_plane/events/projection.py` (AST 가드 테스트) |
| 상태 전이 표는 한 곳 | `control_plane/store/transitions.py` (설계 §6.1) |
| 워커는 신뢰하지 않는다 | secrets·토큰 없음, `agents/tools/`가 owned_paths 밖 쓰기·`.env`·`main` push·임의 명령을 거부하고 거부를 `run.tool_denied`로 남김 |
| GitHub 쓰기는 멱등 | 본문 마커 `ai-platform:meta` / `ai-platform:comment`, head+base로 PR 존재 확인 |
| 이벤트 스키마는 동결 | `control_plane/events/schema.py` — 44 타입, 추가만 가능 |

### 패키지

| 패키지 | 책임 |
|---|---|
| `control_plane/store` | SQLAlchemy 2.x async 모델(9 테이블), StrEnum, 전이 표 |
| `control_plane/events` | 이벤트 봉투·체인·outbox relay·Redis Streams 버스·projection |
| `control_plane/api` | FastAPI 라우터(쓰기는 이벤트 발행만, 읽기는 projection), Idempotency-Key, 웹훅 승인 |
| `control_plane/orchestrator` | Goal → Plan → 승인 interrupt → 분해 → Issue 발행 (LangGraph), 프롬프트 `prompts/*.md` |
| `control_plane/scheduler` | ready 판정, 경로 겹침 직렬화, 슬롯, 런처(docker / in-process), 워커 이벤트 ingest |
| `control_plane/runtime.py` | 상주 프로세스 조립 (`python -m control_plane`) |
| `control_plane/{repo_cache,pr_opener,dry_merge}.py` | repo clone/fetch, 브랜치 push + PR, Dry 자동 머지 |
| `agents` | `BaseAgent`/`CodingAgent`(LangGraph), 컨텍스트 조립, 툴 4종, LLM provider(Anthropic / OpenAI 호환(Ollama) / Fake), 프롬프트 `prompts/*.md` |
| `github_adapter` | REST client(멱등 7 메서드), GraphQL Discussions, App 인증, Dry-run client, 웹훅 → 이벤트, App 점검·정리 스크립트 로직 |
| `worker` | 컨테이너 entrypoint(env만으로 실행, 타임아웃, exit code), Dockerfile |
| `scripts` | PC/e2e 검증 스크립트 (`e2e_dry_run.py`, `pc4_run_tasks.py`, `pc6_via_api.py`, `github_app_check.py`, `seed_test_repo.py`, `cleanup_repo.py`) |

## 스택

| 영역 | 사용 |
|---|---|
| 언어/패키징 | Python 3.12, `uv`, 단일 `pyproject.toml` |
| API | FastAPI + uvicorn, pydantic v2, pydantic-settings(`HITL_*`) |
| 에이전트 그래프 | LangGraph (`interrupt` / `Command(resume)`, Postgres 체크포인터) |
| 저장소 | SQLAlchemy 2.x async + asyncpg, Alembic(append-only 트리거), Redis Streams(consumer group, XAUTOCLAIM) |
| GitHub | httpx REST + GraphQL(Discussions), GitHub App(JWT → installation token, PyJWT), 웹훅 HMAC |
| LLM | Anthropic SDK(`claude-opus-5`), OpenAI 호환 엔드포인트(로컬 Ollama `qwen2.5-coder`), FakeProvider(테스트) |
| 관측 | structlog(json/console), 이벤트 해시 체인 검증 |
| 테스트 | pytest + pytest-asyncio, respx(HTTP mock, `assert_all_mocked`), 진짜 Redis(DB 15), aiosqlite; Docker 통합 테스트 |
| 품질 | ruff(lint/format, banned-api로 SDK import 강제), mypy(`control_plane/` strict), pre-commit |
| 인프라 | docker compose: Postgres 16, Redis 7, MinIO(선언만), 워커 이미지 `foreman-worker` |

## 사용 방법

### 1. 준비

```bash
uv sync --all-groups
cp .env.example .env            # 값은 비워도 기본값으로 동작 (DRY_RUN=true)
make docker-up                  # Postgres / Redis / MinIO   (포트 충돌: *_HOST_PORT=…)
make migrate                    # alembic upgrade head
```

LLM은 셋 중 하나를 `.env`에서 고릅니다.

```
HITL_LLM_PROVIDER=anthropic     # + HITL_ANTHROPIC_API_KEY
HITL_LLM_PROVIDER=openai_compat # 로컬 Ollama: ollama pull qwen2.5-coder:14b, HITL_LLM_MODEL=qwen2.5-coder:14b
HITL_LLM_PROVIDER=fake          # 테스트
```

### 2. 가장 빠른 확인 — e2e dry-run (네트워크 0)

```bash
uv run python scripts/e2e_dry_run.py --fake tests/fixtures/sample_repo "Add a users API with tests"
uv run python scripts/e2e_dry_run.py tests/fixtures/sample_repo "Add a maths helpers module with add and mul functions and tests"   # 실 LLM
```

repo 요약 → Plan → 자동 승인 → TaskDraft → `would create issue` → Coding Agent → tmp bare remote의 `ai/*` 브랜치까지 한 프로세스에서 돕니다.

### 3. 플랫폼으로 돌리기 (Dry 모드)

```bash
make run-control-plane          # 상주: relay + projection + scheduler + PrOpener + DryMerger
make run-api                    # :8000
```

```bash
# 프로젝트(로컬 경로 또는 owner/name) → Goal
curl -X POST localhost:8000/projects -H 'Content-Type: application/json' -H 'X-User-Id: me' \
  -d '{"name":"demo","repo":"/path/to/repo","members":[{"user_id":"me","role":"owner"}]}'
curl -X POST localhost:8000/projects/<pid>/goals -H 'Content-Type: application/json' -H 'X-User-Id: me' \
  -d '{"title":"Add a maths helpers module with add and mul functions and tests"}'
# Plan 승인: 서명된 discussion_comment 웹훅(/approve)을 POST /webhooks/github 로 (tests/api/test_goal_flow.py 참고)
curl localhost:8000/projects/<pid>/goals/<gid>       # 진행률
curl localhost:8000/projects/<pid>/events?since=0    # 이벤트(seq 커서)   WS /projects/<pid>/stream
```

Dry 모드에서는 Issue/PR/Discussion이 로그(`would …`)로만 남고, 워커는 `HITL_WORKER_LAUNCHER=docker`(기본, 이미지 `foreman-worker:dev`) 또는 `inprocess`로 돕니다. `scripts/pc6_via_api.py`가 이 흐름을 HTTP·git만으로 자동 실행합니다.

### 4. 실 GitHub 연결

1. GitHub App 생성: 권한 Contents/Issues/Pull requests/Discussions **write**, Metadata read; 구독 issue_comment, discussion_comment, pull_request, pull_request_review; Webhook **Active** 체크, URL은 터널 주소.
2. `.env`에 `HITL_GITHUB_APP_ID / HITL_GITHUB_APP_PRIVATE_KEY(개행은 \n) / HITL_GITHUB_INSTALLATION_ID / HITL_GITHUB_WEBHOOK_SECRET`.
3. 테스트 repo에 Discussions를 켜고 카테고리 **Plans** 생성.

```bash
uv run python scripts/github_app_check.py --repo owner/name      # 읽기 전용 점검, 전부 [ok]이어야 진행
uv run python scripts/seed_test_repo.py owner/name               # 빈 repo에 픽스처 push
SMEE_URL=https://smee.io/<channel> make run-webhook-tunnel       # 또는 cloudflared tunnel --url http://localhost:8000
HITL_DRY_RUN=false make run-control-plane ; HITL_DRY_RUN=false make run-api
```

그 뒤 위 3번과 같이 프로젝트(`repo: owner/name`)와 Goal을 만들면 실제 Discussion → (사람 `/approve`) → Issue → PR이 생기고, 사람이 PR을 머지하면 Task가 done이 됩니다. 끝나면 `uv run python scripts/cleanup_repo.py owner/name --apply`(마커 있는 Issue/PR·`ai/*` 브랜치만 정리).

워커는 GitHub 토큰을 받지 않습니다. 브랜치 push와 PR 생성은 control plane이 installation 토큰으로 수행합니다.

### 5. 개발

```bash
make check              # ruff + mypy + pytest (진짜 Redis 필요: make docker-up)
make test-integration   # Docker: Postgres 체인 검증, 워커 컨테이너, docker 런처
scripts/check_runbook.sh
```

개발 절차는 `ROADMAP.md` §0(Task마다 Red → Green → Gate → `main` 커밋)입니다. 이 저장소 자체 개발에는 브랜치/PR을 쓰지 않습니다.

## 문서

- `docs/design.md` — 상세 설계(섹션 번호로 참조)
- `ROADMAP.md` — 실행 계획, 상태 보드, 결정 로그 D-01~, 기록 로그
- `docs/runbook.md` — 기동 순서, `.env` 키, 웹훅 터널, 흔한 에러
- `docs/pc/PC-*.md` — 단계별 검증 결과, `docs/review/` — 코드 리뷰·외부 점검 리포트, `docs/postmortem/`
- `CLAUDE.md` — 에이전트가 이 저장소에서 지키는 규칙
