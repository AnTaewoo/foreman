# Foreman — HITL Multi-Agent Dev Platform

GitHub 저장소와 Goal 한 문장을 넣으면, AI 에이전트 팀이 **Plan → Issue → 브랜치 → PR**로 일을 진행하고,
위험한 결정(Plan 승인, PR 머지, 의존성 추가)만 사람이 승인하는 플랫폼입니다.

- 모든 상태 변화는 **서명된 append-only 이벤트**로만 기록됩니다(이벤트 소싱, 해시 체인).
- 코드를 고치는 워커는 **격리된 컨테이너**에서 돌고, DB·시크릿·GitHub 토큰을 받지 않습니다.
- GitHub 쓰기(Issue/PR/Discussion/push)는 전부 **멱등**이고, 기본은 `HITL_DRY_RUN=true`(실 GitHub 호출 0)입니다.

설계 원문 `docs/design.md`, 실행 계획·결정 로그 `ROADMAP.md`, 운영 절차 `docs/runbook.md`.

## 흐름

```
사람  POST /projects {repo}  →  POST /goals {title}
          ▼
Orchestrator (LangGraph)  repo 요약 → Plan(6섹션) → GitHub Discussion "Plan #n"   ── 대기 ──▶  사람 승인
          ▼  (POST …/goals/{gid}/approve  또는 Discussion에 /approve 코멘트)
분해 → Epic/Task 그래프 → Milestone + Issue (task.created)
          ▼
Scheduler  ready ∧ 의존 done ∧ 파일 겹침 없음 → task.assigned → 워커 컨테이너 기동
          ▼
Coding Agent (워커)  clone → 계획 → 편집(owned_paths 안에서만) → 테스트 → 커밋 → push → task.completed
          ▼
Control plane  브랜치를 GitHub로 push → draft PR + Issue 요약 코멘트 (pr.opened)
          ▼
사람이 PR 머지  → 웹훅 pull_request.closed → pr.merged → Task done → 의존 Task 배정 …
```

Dry 모드에서는 GitHub 쓰기가 로그(`would …`)로만 남고, 플랫폼이 사람 머지를 대신해 로컬 git `main`을 옮깁니다.
그래서 GitHub App 없이도 로컬 repo 하나로 전체 흐름이 돕니다.

## 아키텍처

```
┌─ API 프로세스 (make run-api) ──────────────────────────────────────────────────┐
│ FastAPI: /projects /goals /tasks /events  WS /stream  /webhooks/github        │
│ GoalRunner: LangGraph Orchestrator (thread_id = goal_id, Postgres 체크포인터) │
└───────────────┬────────────────────────────────────────────────────────────────┘
                │ EventBus.publish = chain.append_signed → events 테이블 (outbox)
                ▼
     Postgres 16 (events / tool_calls / projects goals epics tasks runs …)
     Redis 7 Streams  events:{project_id}  (+ :retry 지연 재시도 큐)
                ▲
┌─ 상주 control plane (make run-control-plane) ──────────────────────────────────┐
│ OutboxRelay → consumer group 한 개에서 순서대로:                                │
│   Projection(유일한 DB 갱신자) → Scheduler(배정·기동·ingest·죽은 워커 정리)    │
│   → PrOpener(브랜치 push + PR) → DryMerger(dry 모드만)                          │
└───────────────┬────────────────────────────────────────────────────────────────┘
                │ docker run --user <호스트 uid> -v <repo>:<repo> … foreman-worker
                ▼
     워커 컨테이너  WORKER_* env만 · Redis XADD(미서명 → Scheduler.ingest가 서명)
     Coding Agent + 툴(fs / shell / git) — 경로·명령·브랜치 가드레일
```

| 원칙 | 코드 |
|---|---|
| 이벤트 append 경로는 하나 | `control_plane/events/chain.py::append_signed` (프로젝트별 락, SHA-256 체인) |
| DB 갱신은 projection만 | `control_plane/events/projection.py` (AST 가드 테스트) |
| 상태 전이 표는 한 곳 | `control_plane/store/transitions.py` |
| 워커는 신뢰하지 않는다 | secrets·토큰 없음; `agents/tools/`가 owned_paths 밖 쓰기·`.env`·`main` push·임의 명령을 거부 |
| GitHub 쓰기는 멱등 | 본문 마커 `ai-platform:meta`, head+base로 PR 존재 확인 |
| 이벤트 스키마는 동결 | `control_plane/events/schema.py` — 44 타입, 추가만 가능 |

패키지: `control_plane/{store,events,api,orchestrator,scheduler}` · `runtime.py`(상주 조립) · `repo_cache.py` ·
`pr_opener.py` · `dry_merge.py` / `agents`(BaseAgent, CodingAgent, 툴, LLM provider, 프롬프트) / `github_adapter`
(REST·GraphQL·App 인증·Dry client·웹훅·점검/정리) / `worker`(컨테이너 entrypoint, Dockerfile) / `scripts`.

## 스택

| 영역 | 사용 |
|---|---|
| 언어/패키징 | Python 3.12 (`.python-version`), `uv`, 단일 `pyproject.toml` |
| API | FastAPI + uvicorn, pydantic v2, pydantic-settings(`HITL_*`) |
| 에이전트 그래프 | LangGraph (`interrupt` / `Command(resume)`, Postgres 체크포인터) |
| 저장소 | SQLAlchemy 2.x async + asyncpg, Alembic(append-only 트리거), Redis Streams(consumer group, XAUTOCLAIM) |
| GitHub | httpx REST + GraphQL(Discussions), GitHub App(JWT → installation token, PyJWT), 웹훅 HMAC |
| LLM | Anthropic SDK(`claude-opus-5`) 또는 OpenAI 호환 엔드포인트(로컬 Ollama `qwen2.5-coder`) |
| 관측 | structlog, 이벤트 해시 체인 검증 |
| 테스트 | pytest + pytest-asyncio, respx, 진짜 Redis(DB 15), aiosqlite; Docker 통합 테스트 |
| 품질 | ruff, mypy(`control_plane/` strict), pre-commit |
| 인프라 | docker compose: Postgres 16, Redis 7 (MinIO는 `--profile storage`, 기본 미기동); 워커 이미지 `foreman-worker:dev` |

## 사용 방법

### 1. 준비 (한 번)

필요한 것: git, Docker(compose v2), `uv`, `curl` + `jq`(아래 예제용). Python 3.12는 `uv sync`가 받습니다.

```bash
git clone git@github.com:AnTaewoo/foreman.git && cd foreman
uv sync --all-groups
cp .env.example .env                 # 빈 값은 기본값. 아래 LLM 한 줄만 채우면 된다
make docker-up                       # Postgres :5432, Redis :6379  (충돌 시 POSTGRES_HOST_PORT=… REDIS_HOST_PORT=…)
make migrate                         # alembic upgrade head
make worker-image                    # foreman-worker:dev 빌드 (Scheduler가 이 이미지로 워커를 띄운다)
```

LLM은 세 프로파일 중 키가 있는 것을 콘솔에서 Goal마다 고릅니다(D-57). `HITL_LLM_PROVIDER`는 기본 선택만 정합니다.

```
HITL_LLM_PROVIDER=openai_compat    HITL_LLM_MODEL=qwen2.5-coder:14b   # 프로파일 ollama (로컬, 항상 사용 가능)
HITL_OPENAI_API_KEY=sk-…           HITL_OPENAI_MODEL=gpt-5.6          # 프로파일 openai (OpenAI 호환 API)
HITL_ANTHROPIC_API_KEY=sk-ant-…    HITL_ANTHROPIC_MODEL=claude-opus-5 # 프로파일 anthropic
```

포트를 바꿨으면 `.env`의 `HITL_DATABASE_URL`/`HITL_REDIS_URL`도 맞춥니다. Docker 없이 워커를 돌리려면
`HITL_WORKER_LAUNCHER=inprocess`(control plane 프로세스 안에서 실행).

### 2. 기동 (터미널 2개)

```bash
make run-control-plane     # 상주: relay + projection + scheduler + PR + (dry) 머지
make run-api               # http://localhost:8000  (--reload는 API_RELOAD=1 일 때만)
```

### 3. Goal 하나 돌리기 (Dry 모드, GitHub App 불필요)

`repo`는 **존재하는 로컬 git repo 경로**(워커가 여기서 clone 하고 `ai/*` 브랜치를 push) 또는 `owner/name`.
존재하지 않는 경로는 400, 같은 repo로 두 번째 프로젝트는 409입니다. `X-User-Id`는 이 프로젝트 안에서의 사용자
이름이고, `members`에 있는 owner/approver만 승인할 수 있습니다. 먼저 데모용 git repo를 하나 만듭니다(자기 repo를
써도 됩니다. 단 `HITL_REPO_ROOT` 밖이어도 되고, 워커 컨테이너에 그 경로가 그대로 마운트됩니다).

```bash
REPO=/tmp/demo-repo                       # 예: 작은 Flask 앱 샘플을 git repo로
mkdir -p $REPO && git archive HEAD tests/fixtures/sample_repo | tar -x -C $REPO --strip-components=3
git -C $REPO init -q -b main && git -C $REPO add -A && git -C $REPO -c user.name=me -c user.email=me@x commit -qm init

export API=http://localhost:8000 U="X-User-Id: me" J="Content-Type: application/json"

PID=$(curl -s -X POST $API/projects -H "$J" -H "$U" \
  -d "{\"name\":\"demo\",\"repo\":\"$REPO\",\"members\":[{\"user_id\":\"me\",\"role\":\"owner\"}]}" | jq -r .id)
GID=$(curl -s -X POST $API/projects/$PID/goals -H "$J" -H "$U" \
  -d '{"title":"Add a maths helpers module with add and mul functions and tests"}' | jq -r .id)

curl -s $API/projects/$PID/goals/$GID | jq '{status, plan_discussion_number}'   # awaiting_plan_approval 까지 30초~수 분
curl -s -X POST $API/projects/$PID/goals/$GID/approve -H "$U"                    # 사람 승인 (거절: …/reject {"reason"})
watch -n 5 "curl -s $API/projects/$PID/goals/$GID | jq .tasks"                  # ready → running → in_review → done
curl -s "$API/projects/$PID/events?since=0" | jq '.items[].type'                # 이벤트 체인 (WS /projects/$PID/stream)
```

읽기 API는 projection 반영(control plane이 떠 있으면 수 초) 후 값이 보입니다. 쓰기 직후의 `GET /projects/{id}`와
`POST /goals`는 이벤트로 존재를 확인하므로 바로 됩니다. 결과는 `git -C $REPO branch`(`ai/*` 브랜치, Dry 머지 후 `main`
전진)로 봅니다. 실행 중인 워커는 `docker ps --filter name=foreman-worker-`(종료 시 `--rm`). 전체 목록: `GET /projects`(`{items: [...]}`), `GET /projects/{id}/tasks?status=`. 프로젝트 삭제는 `DELETE /projects/{id}`(보관: 남은 Goal·Task 취소, 목록 제외, 이벤트·GitHub 산출물은 유지).

### 4. 실 GitHub 연결

1. GitHub App: 권한 Contents/Issues/Pull requests/Discussions **write**, Metadata read; 구독 issue_comment, discussion_comment,
   pull_request, pull_request_review; **Webhook → Active 체크**, URL은 터널 주소, secret 설정.
2. `.env`: `HITL_GITHUB_APP_ID`, `HITL_GITHUB_APP_PRIVATE_KEY`(개행은 `\n`), `HITL_GITHUB_INSTALLATION_ID`, `HITL_GITHUB_WEBHOOK_SECRET`.
3. 대상 repo: 기본 브랜치에 **커밋 1개 이상**(GitHub에서 만들 때 "Add a README file" 체크 — 빈 repo는 PR의 base가 없어 진행 불가), Discussions 켜고 카테고리 **Plans** 생성, App을 그 repo에 설치.

```bash
uv run python scripts/github_app_check.py --repo owner/name        # 읽기 전용 점검 — 전부 [ok] 이어야 진행
uv run python scripts/seed_test_repo.py owner/name                 # (빈 테스트 repo면) 샘플 앱 push
SMEE_URL=https://smee.io/<channel> make run-webhook-tunnel         # 또는 cloudflared tunnel --url http://localhost:8000
HITL_DRY_RUN=false make run-control-plane ;  HITL_DRY_RUN=false make run-api
```

그 뒤 3번과 같이 `repo: "owner/name"`으로 프로젝트·Goal을 만들면 실제 Discussion → 승인(API 또는 `/approve` 코멘트)
→ Issue → PR이 생기고, 사람이 PR을 머지하면 Task가 done이 됩니다. 브랜치 push와 PR 생성은 control plane이 App 토큰으로
하고 워커는 토큰을 받지 않습니다. 정리: `uv run python scripts/cleanup_repo.py owner/name --apply`(마커 있는 Issue/PR·`ai/*`만).

### 5. 개발

```bash
make check              # ruff + mypy + pytest (진짜 Redis 필요: make docker-up)
make test-integration   # Docker: Postgres 체인 검증, 워커 컨테이너, docker 런처
scripts/check_runbook.sh
```

개발 절차는 `ROADMAP.md` §0(Task마다 Red → Green → Gate → `main` 커밋). 이 저장소 자체 개발에는 브랜치/PR을 쓰지 않습니다.

## 공개 데모

심사·시연용 웹 콘솔은 `GET /`(정적 1페이지, 설계 §11.0)에서 서빙됩니다. 인터넷에 노출할 때는 `HITL_DEMO_MODE=true`
(관리 라우트 `X-Admin-Token`, Goal·IP 한도)로 띄우고, 절차는 `docs/deploy.md`(nginx → :8000, systemd)입니다.

## 문서

- `docs/boundary.md` **지금 만들 수 있는 Goal의 수준**(등급 A/B/C, 실측 근거, 경계 조건) · `docs/design.md` 설계 · `ROADMAP.md` 계획/보드/결정 D-01~ · `docs/runbook.md` 기동·`.env`·터널·흔한 에러
- `docs/pc/PC-*.md` 단계별 검증 · `docs/review/` 리뷰·외부 점검 · `docs/postmortem/` · `CLAUDE.md` 에이전트 규칙
