# Runbook — MVP 1 (dev)

로컬에서 control plane + 워커를 띄우고 Goal 하나를 끝까지 돌리는 절차. 사용자용 요약은 `README.md`, 여기는 운영
세부. 코드블록 중 ` ```bash check `로 표시된 것은 `scripts/check_runbook.sh`가 순서대로 실행해 검증한다(Docker
스택이 떠 있어야 한다). 나머지는 설명용이다.

## 0. 전제

- Python 3.12 + `uv` (`.python-version`; 시스템 python3가 오래됐어도 `uv run …`은 3.12를 쓴다)
- Docker (Postgres 16, Redis 7, 워커 컨테이너). 소켓 권한이 셸에 반영 안 되면 `echo "<cmd>" | newgrp docker`
- LLM: Anthropic 키(`HITL_LLM_PROVIDER=anthropic`) 또는 로컬 Ollama(`openai_compat`, `ollama pull qwen2.5-coder:14b`)
- `.env`는 `cp .env.example .env`. 빈 값은 미설정(`env_ignore_empty`)이라 그대로 두면 기본값이다

```bash check
uv sync --all-groups >/dev/null
uv run python -c "import control_plane, agents, github_adapter, worker; print('imports ok')"
```

## 1. 기동 순서

1. **docker-up** — Postgres/Redis. 순서: docker-up → migrate → worker-image → run-control-plane → run-api.
   호스트 포트가 잡혀 있으면 `*_HOST_PORT`로 바꾸고 `.env`의 URL도 맞춘다. MinIO는 코드에서 쓰지 않으므로
   compose `profiles: ["storage"]`에 두었고 기본으로 뜨지 않는다(`docker compose --profile storage up`).

   ```bash
   make docker-up
   # 포트 충돌 시 예: POSTGRES_HOST_PORT=15432 REDIS_HOST_PORT=16379 make docker-up
   ```

2. **migrate** — Alembic으로 스키마 적용(`events` append-only 트리거 포함). `HITL_DATABASE_URL` 기본값은
   `postgresql+asyncpg://hitl:hitl@localhost:5432/hitl`.

   ```bash check
   uv run alembic upgrade head
   uv run alembic current | tail -1
   ```

3. **worker-image** — docker 런처가 띄울 이미지. 기본 태그 `foreman-worker:dev`(`HITL_WORKER_IMAGE`로 변경). 통합
   테스트는 자기 것을 `foreman-worker:test`로 따로 빌드하므로 dev 태그를 건드리지 않는다. 워커 코드를 바꿨으면
   다시 빌드한다.

   ```bash
   make worker-image
   ```

4. **run-control-plane** — 상주 프로세스(P6.1): outbox relay + projection + Scheduler + retry + reaper, dry 모드면
   `DryMerger`(PR 자동 머지, D-36), `PrOpener`(PR 생성·브랜치 push, D-37/D-41). Task 배정·워커 기동·PR은 전부
   여기서 일어난다.
   - 워커 기동 `HITL_WORKER_LAUNCHER`: `docker`(기본) 또는 `inprocess`(Docker 없는 개발 — 이 프로세스 안에서
     Coding Agent 실행).
   - docker 런처는 `HITL_REPO_ROOT`를 컨테이너에 같은 경로로 마운트하고, 그 **밖의 로컬 repo 경로도 프로젝트마다
     같은 경로로 개별 마운트**한다(D-38). 컨테이너는 **호스트 uid:gid로 실행**(`--user`, D-43)되어 push 결과 파일의
     소유자가 호스트 사용자와 같다. 컨테이너 안 `HOME=/tmp/worker-home`, 작업 디렉토리 `/tmp/work`.
   - 죽은 워커 정리(D-44): 5초마다 `docker inspect`로 생존을 보고, 사라진 컨테이너는 `task.failed{worker_died}`,
     타임아웃+5분을 넘긴 것은 `task.failed{timeout}` 후 재배정(attempt<3). 기동 시 `recover_orphans`가 projection의
     `running`/`assigned` Task 중 컨테이너가 없는 것을 같은 경로로 정리한다.

   ```bash
   make run-control-plane                 # HITL_WORKER_LAUNCHER=inprocess 도 가능
   ```

5. **run-api** — FastAPI(`/health`, `/projects…`, `/webhooks/github`, `WS /projects/{id}/stream`).
   `POST /projects/{id}/goals`가 Orchestrator를 백그라운드로 돌리고 Plan 승인 interrupt에서 기다린다.
   재시작하면 `awaiting_plan_approval` Goal의 대기 목록을 projection에서 복원한다(P6.2). 기본은 no-reload —
   `--reload`는 `API_RELOAD=1`일 때만(D-48: 워커의 파일 쓰기가 리로드를 유발했음).

   ```bash
   make run-api          # API_PORT=8000 기본
   curl -s localhost:8000/health
   ```

6. **run-worker(수동)** — 보통은 Scheduler가 띄운다. 디버그용 수동 실행은 `WORKER_*` 환경변수를 주고
   `python -m worker`. 워커는 push와 `task.completed`까지만 하고 PR은 열지 않는다(D-37).

   ```bash
   make run-worker       # WORKER_REPO_URL / WORKER_BRANCH / WORKER_TASK_JSON / WORKER_REDIS_URL 필요
   ```

앱 팩토리가 실제로 만들어지는지(설정·provider 배선) 확인:

```bash check
uv run python -c "from control_plane.api.app import app; a = app(); print('app ok:', a.title)"
```

## 2. `.env` 키 (`HITL_` 프리픽스, `.env.example` 참고)

| 키 | 뜻 | 기본 |
|---|---|---|
| `HITL_DRY_RUN` | 실제 GitHub API 호출 여부. `true`면 Issue/PR/Discussion은 "would …" 로그 + Dry 자동 머지. `false`는 §3b 점검 뒤에만 | `true` |
| `HITL_DATABASE_URL` | SQLAlchemy async URL. Postgres(`postgresql+asyncpg://`) 또는 sqlite(`sqlite+aiosqlite:///…`, 단위 테스트) | Postgres localhost |
| `HITL_REDIS_URL` | Redis Streams(outbox relay, 워커 XADD). 테스트는 DB 15, 스크립트는 DB 14를 쓴다 | `redis://localhost:6379/0` |
| `HITL_WORKER_LAUNCHER` / `HITL_WORKER_IMAGE` / `HITL_SCHEDULER_MAX_WORKERS` | 상주 프로세스의 워커 기동 방식·이미지·동시 수 (P6.1) | `docker`, `foreman-worker:dev`, 4 |
| `HITL_REPO_ROOT` | `owner/name` 프로젝트를 clone 하는 루트(D-38). 로컬 경로 프로젝트는 그대로(밖이어도 개별 마운트) | `./repos` (gitignore) |
| `HITL_LLM_PRICE_IN_PER_MTOK` / `HITL_LLM_PRICE_OUT_PER_MTOK` | 비용 단가 USD per 1M tokens (D-39). 비우면 cost_usd 0 | 0 |
| `HITL_LLM_PROVIDER` | `anthropic` \| `openai_compat`(Ollama) (D-33) | `anthropic` |
| `HITL_LLM_BASE_URL` / `HITL_LLM_MODEL` / `HITL_LLM_API_KEY` | openai_compat 엔드포인트·모델·키(Ollama는 아무 값) | `http://localhost:11434/v1`, `qwen2.5-coder:7b`, `ollama` |
| `HITL_ANTHROPIC_API_KEY` / `HITL_ANTHROPIC_MODEL` | Anthropic 키·모델(provider가 anthropic일 때 필수) | 없음, `claude-opus-5` |
| `HITL_GITHUB_APP_ID` / `HITL_GITHUB_APP_PRIVATE_KEY` / `HITL_GITHUB_INSTALLATION_ID` | GitHub App(실 연결 §3b). PEM은 개행을 `\n`으로 | 비움 |
| `HITL_GITHUB_WEBHOOK_SECRET` | 웹훅 HMAC(`X-Hub-Signature-256`) 검증. 비어 있으면 웹훅은 503(D-50) — Dry·로컬은 API 승인을 쓴다 | 비움 |
| `HITL_LOG_LEVEL` / `HITL_LOG_FORMAT` | 로깅 | `INFO`, `console` |

## 3. Goal 하나 돌리기 (Dry)

README §3이 사용자용 절차다: 로컬 git repo 경로로 `POST /projects` → `POST /goals` → Goal이
`awaiting_plan_approval`이 되면 **`POST /projects/{id}/goals/{gid}/approve`**(`X-User-Id`가 members의
owner|approver, D-51) → `GET /projects/{id}/goals/{gid}` 진행률, `GET /projects/{id}/events?since=<seq>` 또는
`WS /projects/{id}/stream`으로 이벤트. 거절은 `…/reject {"reason"}`, 취소는 `…/cancel`.
읽기 API는 projection 반영 후(control plane이 떠 있어야 함) 값이 보인다.

설정이 실제로 읽히는지(빈 `.env` 값은 기본값):

```bash check
uv run python -c "from control_plane.config import Settings; s = Settings(); print('settings ok:', s.llm_provider, s.worker_launcher, s.dry_run)"
```

### 상주 프로세스 없이 한 번에 (스크립트)

`scripts/e2e_dry_run.py`는 relay/projection/Scheduler/PrOpener/DryMerger를 프로세스 안에서 조립해 repo 경로 +
Goal → Plan → (자동 승인) → TaskDraft → `would create issue` → Coding Agent → tmp bare remote의 `ai/*` 브랜치까지
돌린다. GitHub는 항상 Dry, LLM은 `.env`의 provider(Redis DB 14, sqlite 임시 파일).

```bash
uv run python scripts/e2e_dry_run.py tests/fixtures/sample_repo "Add a users API with tests"
uv run python scripts/e2e_dry_run.py --no-coding tests/fixtures/sample_repo "Add a users API with tests"   # Plan+Issue까지만
```

### 로컬 모델 비교 (Ollama)

`.env`의 `HITL_LLM_PROVIDER=openai_compat`이면 `HITL_LLM_MODEL`이 쓰인다. 스크립트의 `--model`로 그때그때
바꿀 수 있다. RTX 3060 12GB 기준 `qwen2.5-coder:14b`(Q4_K_M, ~9GB VRAM)까지 GPU에 다 올라가고,
`32b`(~20GB)는 CPU로 넘쳐 매우 느리다.

```bash
ollama pull qwen2.5-coder:14b
# Coding Agent만 (Task 3개, 실행당 1~2분). --tasks hard = 기존 파일 수정 포함, simple = D-35 간단 Task
uv run python scripts/pc4_run_tasks.py --tasks hard --model qwen2.5-coder:14b --dump /tmp/pc4-dump
uv run python scripts/pc4_run_tasks.py --tasks hard --model qwen2.5-coder:7b
# 전체 흐름 (Plan → Task → 코딩, 실행당 3~10분)
uv run python scripts/e2e_dry_run.py --model qwen2.5-coder:14b tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests"
# 결과 보기: 브랜치 diff와 모델 응답
git -C <출력의 remote=경로> log --all --oneline
git -C <출력의 remote=경로> diff main <ai/브랜치>
cat /tmp/pc4-dump/101.jsonl | python -m json.tool   # Task 1의 프롬프트/응답
```

## 3a. 공개 데모 배포

`docs/deploy.md` — foreman.antaewoo.com(nginx → :8000), systemd 유닛(`deploy/systemd/`), 데모 모드 한도, 데모 준비 스크립트(P9).

## 3b. 실 GitHub 연결 전 점검 (X.1 / P7)

`.env`에 App 값(`HITL_GITHUB_APP_ID`, `HITL_GITHUB_APP_PRIVATE_KEY`(개행은 `\n`), `HITL_GITHUB_INSTALLATION_ID`,
`HITL_GITHUB_WEBHOOK_SECRET`)을 넣은 뒤, **읽기 전용** 점검으로 인증·설치·권한·웹훅 구독을 확인한다.
필요한 권한: Contents/Issues/Pull requests/Discussions write, Metadata read. 구독 이벤트: issue_comment,
discussion_comment, pull_request, pull_request_review (`check_suite`는 Checks 권한이 있을 때만 보이며 선택). `HITL_DRY_RUN=false`는 이 점검이 전부 `[ok]`인
뒤에만.

```bash
uv run python scripts/github_app_check.py --repo <owner>/<name>
```

App의 Webhook URL을 저장하면 GitHub가 `ping`을 보낸다 — 서명이 맞으면 `200 {"pong": true}`.
**Webhook 섹션의 `Active` 체크가 꺼져 있으면 이벤트가 전혀 배달되지 않는다**(API로는 확인 불가, ping 재전송은 됨) — PC-7에서 겪음.

### 웹훅 받기 (로컬 API에 공개 URL 붙이기, P7.3)

GitHub → 로컬 `:8000/webhooks/github`로 배달하려면 터널이 필요하다. 둘 중 하나:

```bash
# (1) smee.io: https://smee.io/new 에서 채널을 만들고, App 설정의 Webhook URL에 그 채널 URL을 넣는다
SMEE_URL=https://smee.io/<channel> make run-webhook-tunnel
# (2) cloudflared: 임시 공개 URL을 발급받아 App 설정의 Webhook URL에 넣는다
cloudflared tunnel --url http://localhost:8000
```

순서: `make run-api`(`HITL_GITHUB_WEBHOOK_SECRET` = App의 secret) → 터널 → App 설정 저장 → `ping`이 `200`으로
찍히면 연결 완료. `scripts/github_app_check.py`의 `[ok] events`가 `discussion_comment`를 포함해야 Plan 승인이
Discussion 코멘트로 들어온다(`issue_comment`는 개발용 우회).

## 4. 검사

```bash check
make lint
```

전체 게이트는 `make check`(ruff + mypy + pytest, 진짜 Redis 필요), Docker 통합은 `make test-integration`
(전용 DB `hitl_test`를 비우고 다시 만든다 — 개발·데모 DB `hitl`은 건드리지 않음, `FOREMAN_TEST_DATABASE_URL`).

## 5. 흔한 에러

| 증상 | 원인 → 해결 |
|---|---|
| `docker compose up`에서 `port is already allocated` | 호스트 포트 점유 → `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT`로 바꾸고 `.env`의 URL도 맞춘다 |
| `task.failed{launch_failed}` + `Unable to find image 'foreman-worker:dev'` | 워커 이미지 미빌드 → `make worker-image` |
| `POST /projects` 400 `local path … does not exist` / `must be an existing local path, owner/name, or a git URL` | repo 검증(D-51) → 존재하는 git repo 경로(README §3처럼 준비) 또는 `owner/name` |
| `POST /projects` 409 | 같은 repo의 프로젝트가 이미 있다(D-45) → 기존 프로젝트를 쓰거나 다른 repo |
| `POST …/approve` 403 / 409 | `X-User-Id`가 members의 owner\|approver가 아님 / Goal이 `awaiting_plan_approval`이 아님(아직 Plan 중이거나 이미 승인) |
| `GET /projects/{id}/goals/{gid}` 404가 계속 | control plane이 안 떠 있어 projection이 멈춤 → `make run-control-plane` |
| 워커가 push에서 `Permission denied` / repo에 root 소유 파일 | 구버전 이미지·런처 → 컨테이너는 호스트 uid로 돈다(D-43). `make worker-image` 후 control plane 재시작 |
| `goal.cancelled{reason: "repo_unavailable: dry-run mode never clones remote repos"}` | Dry 모드에서 `owner/name` 프로젝트(D-48) → 로컬 경로를 쓰거나 §3b 후 `HITL_DRY_RUN=false` |
| 웹훅 503 `webhook secret not configured` | `HITL_GITHUB_WEBHOOK_SECRET` 비움(D-50) → 실 연결이면 App secret, Dry면 API 승인 사용 |
| `ValueError: Could not deserialize key data` (GitHub App JWT) | `HITL_GITHUB_APP_PRIVATE_KEY`의 PEM 개행이 깨짐 → 개행을 `\n`으로 이스케이프한 한 줄로 넣는다 |
| 테스트에서 `respx.models.AllMockedAssertionError` | mock에 없는 URL/메서드로 호출 → 어댑터는 `GITHUB_API_BASE_URL`만 쓰고, 테스트 라우트를 그 경로로 등록한다(`assert_all_mocked=True`가 기본) |
| `httpx2`와 `respx` 혼용 오류 | Anthropic SDK는 `httpx2`, GitHub 어댑터는 `httpx` → SDK 호출은 `DefaultAsyncHttpxClient(transport=httpx2.MockTransport)`로, GitHub은 respx로 각각 mock |
| sqlite에서는 되는데 Postgres에서 체인 검증 실패/`asyncpg` 오류 | JSONB 왕복·enum 차이 → 서명은 `canonical_json` 텍스트로만(D-29), Postgres 전용 검증은 `tests/integration`(Docker) |
| `Docker not available` / `permission denied while trying to connect to the Docker daemon` | 통합 테스트는 skip(`-m integration`), 소켓 권한은 `echo "<cmd>" \| newgrp docker` 또는 `docker` 그룹 재로그인 |
