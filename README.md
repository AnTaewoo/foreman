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
                          (repo에 Discussions 카테고리 Plans가 없으면 Issue로)
          ▼  (POST …/goals/{gid}/approve  또는 Plan에 /approve 코멘트)
분해 → Epic/Task 그래프 → Milestone + Issue (task.created)
          ▼
Scheduler  ready ∧ 의존 done ∧ 파일 겹침 없음 → task.assigned → 워커 컨테이너 기동
          ▼
Coding Agent (워커)  clone → 계획 → 편집(owned_paths 안에서만) → 테스트 → 커밋 → push → task.completed
          ▼
Control plane  브랜치를 GitHub로 push → draft PR + Issue 요약 코멘트 (pr.opened)
          ▼
사람이 PR 머지  → 웹훅 pull_request.closed → pr.merged → Task done → 의존 Task 배정 …
          ▼
Task 전부 끝남 → epic.completed → goal.completed (Goal done)
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
| 이벤트 스키마는 동결 | `control_plane/events/schema.py` — 45 타입, 추가만 가능 |

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

두 가지 방법이 있습니다.

- **A. 웹에서 바로 써 보기** — <https://foreman.antaewoo.com>. 설치·로그인·API 키가 필요 없습니다. 내 GitHub repo에
  App만 설치하면 됩니다.
- **B. 직접 운영하기** — 이 저장소를 받아 내 서버에서 띄웁니다(개발·시험용).

---

### A. 웹에서 바로 써 보기

필요한 것: **GitHub 계정**, 그리고 그 **개인 계정 아래에 커밋이 1개 이상 있는 repo**.
결과부터 보고 싶다면 콘솔 위 "프로젝트" 선택 상자에서 기존 프로젝트를 고르고 Goal을 누르세요(Plan·Issue·PR 링크).

#### 1) repo 준비

| 항목 | 방법 | 필수 |
|---|---|---|
| 커밋 1개 이상 | GitHub에서 repo를 만들 때 **"Add a README file"** 체크 | 필수 — 빈 repo는 PR을 만들 base가 없습니다 |
| 코드 | 비어 있어도 됩니다(README만). 기존 프로젝트여도 됩니다 | 선택 |
| Discussions의 `Plans` 카테고리 | Settings → Features → Discussions 켜기 → 카테고리 `Plans` 추가 | 선택 — 없으면 Plan이 **Issue**로 올라갑니다 |

#### 2) GitHub App 설치

콘솔의 **"내 GitHub repo 연결" → ① GitHub App 설치 ↗**(또는 <https://github.com/apps/foreman-antaewoo>) →
Install → 연결할 repo 선택(**Only select repositories** 권장).

App이 받는 권한: Contents·Issues·Pull requests·Discussions **쓰기**, Metadata 읽기. 코드를 쓰는 워커는 격리
컨테이너에서 돌고 토큰을 받지 않으며, `main`에 직접 push하지 않습니다 — 모든 변경은 **draft PR**로 올라옵니다.

#### 3) repo 연결

**② repo(`내아이디/repo이름`) 입력 → "점검" → "연결"**. 관리 토큰 칸은 비워 둡니다.

| 점검 표시 | 의미 |
|---|---|
| ✔ | 통과 |
| △ (노란색) | 경고 — 연결은 됩니다. 예: `discussions`(Plans 카테고리 없음 → Plan은 Issue로) |
| ✘ | 연결 불가. `installation` ✘면 App이 그 repo에 설치되지 않은 것 → 표시된 설치 링크로 |

연결하면 App이 설치된 계정이 이 프로젝트의 **owner**가 됩니다. 승인과 머지는 그 계정으로 GitHub에서 합니다.
그래서 **개인 계정 repo**를 쓰세요 — organization repo는 owner가 조직 이름이 되어 개인의 `/approve`가 인정되지 않습니다.

#### 4) Goal 만들기

만들 기능을 **한 문장**으로 적고 LLM을 고른 뒤 **"Goal 생성"**. 무엇을 적을지 막막하면 콘솔의 **예시 Goal**을
누르면 그대로 채워집니다. 예: `Add a slugify(text) utility module that lowercases and hyphenates, with tests, and
document it in README.md`.
어느 정도 크기의 Goal이 잘 되는지는 [`docs/boundary.md`](docs/boundary.md)에 있습니다 — 파일 몇 개, 테스트로 확인할 수
있는 기능이 가장 안정적입니다.

#### 5) Plan 승인 — GitHub에서

1~3분 뒤 Plan이 GitHub에 올라옵니다(콘솔의 **Discussion ↗** / Issue 링크). 본문(이해한 내용, 수용 기준, Epic,
Task 그래프, 예상 결정, 예산)을 읽고 **그 Plan에 댓글**을 답니다.

| 댓글 | 결과 |
|---|---|
| `/approve` | Goal 시작 — Task가 Issue로 만들어지고 워커가 첫 Task부터 코드를 씁니다 |
| `/reject <사유>` | Goal 취소 |

repo **owner 계정의 댓글만** 인정됩니다(웹훅 서명으로 확인). 내 repo에서는 콘솔에 Approve 버튼 대신
"GitHub에서 승인" 안내와 Plan 링크가 나옵니다(버튼은 콘솔 계정이 승인자인 서버 데모 repo에서만). 승인하면 콘솔이
자동으로 다음 단계로 넘어갑니다.

#### 6) PR 머지 — GitHub에서

Task마다 **draft PR**이 생깁니다(Issue에 요약 코멘트). 변경을 확인하고 **Ready for review → Merge**. 머지되면 그
Task가 done이 되고, 그것을 기다리던 다음 Task가 방금 머지된 코드 위에서 시작합니다. 모든 Task가 끝나면 Goal이
**done**이 됩니다.

콘솔 화면이 실시간으로 따라옵니다: 진행 단계, "지금 누구 차례인지" 한 줄, Task 표(# → Issue, PR 링크), 이벤트 로그
(접힘, 테스트 출력 포함).

#### 문제가 생기면

| 증상 | 원인 / 해결 |
|---|---|
| 연결 시 `app_not_installed` | App이 그 repo에 설치되지 않음 → 메시지의 링크로 설치(설치 범위에 repo 추가) |
| 연결 시 `repo check failed — content: empty repository` | 커밋이 없음 → README 하나 커밋 후 다시 |
| 연결 시 409 `already exists` | 같은 repo가 이미 연결됨 → 목록에서 그 프로젝트를 고르세요 |
| `/approve`를 달았는데 그대로 | 댓글 계정이 repo owner인지, Plan이 달린 **그** Discussion/Issue인지 확인 |
| Task가 `blocked` | 워커가 3번 시도 후 실패 — Issue의 실패 코멘트와 이벤트 로그(테스트 출력)를 보세요 |
| 오래 멈춘 Goal | 콘솔의 "Goal 취소" 후 더 작은 Goal로 다시 |

---

### B. 직접 운영하기

#### 1. 준비 (한 번)

필요한 것: git, Docker(compose v2), `uv`, `curl` + `jq`(아래 예제용). Python 3.12는 `uv sync`가 받습니다.

```bash
git clone git@github.com:AnTaewoo/foreman.git && cd foreman
uv sync --all-groups
cp .env.example .env                 # 빈 값은 기본값. 아래 LLM 한 줄만 채우면 된다
make docker-up                       # Postgres :5432, Redis :6379  (충돌 시 POSTGRES_HOST_PORT=… REDIS_HOST_PORT=…)
make migrate                         # alembic upgrade head
make worker-image                    # foreman-worker:dev 빌드 (Scheduler가 이 이미지로 워커를 띄운다)
```

LLM은 키가 있는 프로파일 중에서 콘솔이 Goal마다 고릅니다(D-57; 콘솔 선택지는 `ollama`·`openai`).
고르지 않았을 때의 기본은 **openai 키가 있으면 `openai`**, 없으면 `HITL_LLM_PROVIDER`에서 유도합니다
(`openai_compat` → `ollama`). `HITL_LLM_DEFAULT_PROFILE=ollama`처럼 고정할 수도 있습니다.

```
HITL_LLM_PROVIDER=openai_compat    HITL_LLM_MODEL=qwen2.5-coder:14b   # 프로파일 ollama (로컬, 항상 사용 가능)
HITL_OPENAI_API_KEY=sk-…           HITL_OPENAI_MODEL=gpt-5.6          # 프로파일 openai (OpenAI 호환 API)
HITL_ANTHROPIC_API_KEY=sk-ant-…    HITL_ANTHROPIC_MODEL=claude-opus-5 # 프로파일 anthropic
```

포트를 바꿨으면 `.env`의 `HITL_DATABASE_URL`/`HITL_REDIS_URL`도 맞춥니다. Docker 없이 워커를 돌리려면
`HITL_WORKER_LAUNCHER=inprocess`(control plane 프로세스 안에서 실행).

#### 2. 기동 (터미널 2개)

```bash
make run-control-plane     # 상주: relay + projection + scheduler + PR + (dry) 머지
make run-api               # http://localhost:8000  (--reload는 API_RELOAD=1 일 때만)
```

#### 3. Goal 하나 돌리기 (Dry 모드, GitHub App 불필요)

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
전진)로 봅니다. 실행 중인 워커는 `docker ps --filter name=foreman-worker-`(종료 시 `--rm`). 전체 목록: `GET /projects`(`{items: [...]}`), `GET /projects/{id}/tasks?status=`. 프로젝트 삭제는 `DELETE /projects/{id}` — 남은 Goal·Task를 취소하고 목록에서 뺍니다. 이벤트 기록과 GitHub Issue/PR/Discussion은 남고, 같은 repo를 다시 연결할 수 있습니다.

#### 4. 실 GitHub 연결

1. GitHub App: 권한 Contents/Issues/Pull requests/Discussions **write**, Metadata read; 구독 issue_comment, discussion_comment,
   pull_request, pull_request_review; **Webhook → Active 체크**, URL은 터널 주소, secret 설정.
2. `.env`: `HITL_GITHUB_APP_ID`, `HITL_GITHUB_APP_PRIVATE_KEY`(개행은 `\n`), `HITL_GITHUB_WEBHOOK_SECRET`.
   `HITL_GITHUB_INSTALLATION_ID`는 **선택** — installation은 연결할 때 repo마다 자동으로 찾아 프로젝트에 기록합니다
   (없는 프로젝트의 기본값으로만 쓰임). 다른 계정의 repo도 받으려면 App 설정 → Advanced → **Make public**.
3. 대상 repo: 기본 브랜치에 **커밋 1개 이상**(GitHub에서 만들 때 "Add a README file" 체크 — 빈 repo는 PR의 base가 없어 진행 불가), App을 그 repo에 설치. Discussions 카테고리 **Plans**는 선택(없으면 Plan이 Issue로).

```bash
uv run python scripts/github_app_check.py --repo owner/name        # 읽기 전용 점검 — 전부 [ok] 이어야 진행
uv run python scripts/seed_test_repo.py owner/name                 # (빈 테스트 repo면) 샘플 앱 push
SMEE_URL=https://smee.io/<channel> make run-webhook-tunnel         # 또는 cloudflared tunnel --url http://localhost:8000
HITL_DRY_RUN=false make run-control-plane ;  HITL_DRY_RUN=false make run-api
```

그 뒤 콘솔(`http://localhost:8000/`, A의 3~6단계와 같음) 또는 3번과 같이 `repo: "owner/name"`으로 프로젝트·Goal을
만들면 실제 Plan(Discussion 또는 Issue) → 승인(API 또는 `/approve` 코멘트)
→ Issue → PR이 생기고, 사람이 PR을 머지하면 Task가 done이 됩니다. 브랜치 push와 PR 생성은 control plane이 App 토큰으로
하고 워커는 토큰을 받지 않습니다. 정리: `uv run python scripts/cleanup_repo.py owner/name --apply`(마커 있는 Issue/PR·`ai/*`만).

#### 5. 개발

```bash
make check              # ruff + mypy + pytest (진짜 Redis 필요: make docker-up)
make test-integration   # Docker: Postgres 체인 검증, 워커 컨테이너, docker 런처
scripts/check_runbook.sh
```

개발 절차는 `ROADMAP.md` §0(Task마다 Red → Green → Gate → `main` 커밋). 이 저장소 자체 개발에는 브랜치/PR을 쓰지 않습니다.

## 공개 데모

웹 콘솔은 `GET /`(정적 1페이지, 설계 §11.0)에서 서빙되고, <https://foreman.antaewoo.com>에 공개 GitHub App
`foreman-antaewoo`와 함께 떠 있습니다(사용법은 위 A). 배포 절차는 `docs/deploy.md`(nginx → :8000, 공개 App,
마이그레이션·재시작). 크레딧을 제한하려면 `HITL_DEMO_MODE=true`로 띄웁니다 — Goal·IP 한도, 서버 소유 repo 연결·취소 등
관리 라우트는 `X-Admin-Token`(App이 설치된 외부 repo 연결은 토큰 없이 허용).

## 문서

- `docs/boundary.md` **지금 만들 수 있는 Goal의 수준**(등급 A/B/C, 실측 근거, 경계 조건) · `docs/design.md` 설계 · `ROADMAP.md` 계획/보드/결정 D-01~ · `docs/runbook.md` 기동·`.env`·터널·흔한 에러
- `docs/pc/PC-*.md` 단계별 검증 · `docs/review/` 리뷰·외부 점검 · `docs/postmortem/` · `CLAUDE.md` 에이전트 규칙
