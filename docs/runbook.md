# Runbook — MVP 1 (dev)

로컬에서 control plane + 워커를 띄우고 e2e dry-run을 돌리는 절차. 코드블록 중 ` ```bash check `로 표시된 것은
`scripts/check_runbook.sh`가 순서대로 실행해 검증한다(Docker 스택이 떠 있어야 한다). 나머지는 설명용이다.

## 0. 전제

- Python 3.12 + `uv` (시스템 python3가 오래됐어도 `uv run …`은 3.12를 쓴다)
- Docker (Postgres 16, Redis 7, MinIO). 소켓 권한이 셸에 반영 안 되면 `echo "<cmd>" | newgrp docker`
- LLM: Anthropic 키 또는 로컬 Ollama(`ollama serve`, `ollama pull qwen2.5-coder:7b`)

```bash check
uv sync --all-groups >/dev/null
uv run python -c "import control_plane, agents, github_adapter, worker; print('imports ok')"
```

## 1. 기동 순서

1. **docker-up** — Postgres/Redis/MinIO. 순서: docker-up → migrate → run-control-plane → run-api. 호스트 포트가 잡혀 있으면 `*_HOST_PORT`로 바꾼다.

   ```bash
   make docker-up
   # 포트 충돌 시 예: MINIO_HOST_PORT=9100 MINIO_CONSOLE_HOST_PORT=9101 make docker-up
   ```

2. **migrate** — Alembic으로 스키마 적용(`events` append-only 트리거 포함). `HITL_DATABASE_URL` 기본값은
   `postgresql+asyncpg://hitl:hitl@localhost:5432/hitl`.

   ```bash check
   uv run alembic upgrade head
   uv run alembic current | tail -1
   ```

3. **run-control-plane** — 상주 프로세스(P6.1): outbox relay + projection + Scheduler + retry, dry 모드면
   `DryMerger`(PR 자동 머지, D-36), `PrOpener`(PR 생성, D-37). Task 배정·워커 기동·PR은 전부 여기서 일어난다.
   워커 기동 방식은 `HITL_WORKER_LAUNCHER`: `docker`(기본, `HITL_WORKER_IMAGE`, `HITL_REPO_ROOT`를 컨테이너에
   마운트) 또는 `inprocess`(Docker 없는 개발 — 이 프로세스 안에서 Coding Agent 실행).

   ```bash
   docker build -f worker/Dockerfile -t foreman-worker:dev .   # docker 런처일 때 한 번
   make run-control-plane                                       # HITL_WORKER_LAUNCHER=inprocess 도 가능
   ```

4. **run-api** — FastAPI(`/health`, `/projects…`, `/webhooks/github`, `WS /projects/{id}/stream`).
   `POST /projects/{id}/goals`가 Orchestrator를 백그라운드로 돌리고 Plan 승인 interrupt에서 기다린다.
   재시작하면 `awaiting_plan_approval` Goal의 대기 목록을 projection에서 복원한다(P6.2).

   ```bash
   make run-api          # API_PORT=8000 기본, --reload
   curl -s localhost:8000/health
   ```

5. **run-worker(수동)** — 보통은 Scheduler가 띄운다. 디버그용 수동 실행은 `WORKER_*` 환경변수를 주고
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
| `HITL_DRY_RUN` | 실제 GitHub API 호출 여부. MVP 1 전 구간 `true` (Issue/PR/Discussion은 "would …" 로그) | `true` |
| `HITL_DATABASE_URL` | SQLAlchemy async URL. Postgres(`postgresql+asyncpg://`) 또는 sqlite(`sqlite+aiosqlite:///…`, 단위 테스트) | Postgres localhost |
| `HITL_REDIS_URL` | Redis Streams(outbox relay, 워커 XADD). 테스트는 DB 15, 스크립트는 DB 14를 쓴다 | `redis://localhost:6379/0` |
| `HITL_WORKER_LAUNCHER` / `HITL_WORKER_IMAGE` / `HITL_SCHEDULER_MAX_WORKERS` | 상주 프로세스의 워커 기동 방식·이미지·동시 수 (P6.1) | `docker`, `foreman-worker:dev`, 4 |
| `HITL_REPO_ROOT` | `owner/name` 프로젝트를 clone 하는 루트(D-38). 로컬 경로 프로젝트는 그대로 | `./repos` |
| `HITL_LLM_PRICE_IN_PER_MTOK` / `HITL_LLM_PRICE_OUT_PER_MTOK` | 비용 단가 USD per 1M tokens (D-39). 비우면 cost_usd 0 | 0 |
| `HITL_LLM_PROVIDER` | `anthropic` \| `openai_compat`(Ollama) \| `fake` (D-33) | `anthropic` |
| `HITL_LLM_BASE_URL` / `HITL_LLM_MODEL` / `HITL_LLM_API_KEY` | openai_compat 엔드포인트·모델·키(Ollama는 아무 값) | `http://localhost:11434/v1`, `qwen2.5-coder:7b`, `ollama` |
| `HITL_ANTHROPIC_API_KEY` / `HITL_ANTHROPIC_MODEL` | Anthropic 키·모델. PC-5 최종 판정용 | 없음, `claude-opus-5` |
| `HITL_GITHUB_APP_ID` / `HITL_GITHUB_APP_PRIVATE_KEY` / `HITL_GITHUB_INSTALLATION_ID` | GitHub App(실 연결은 §8 후속). PEM은 개행을 `\n`으로 | 비움 |
| `HITL_GITHUB_WEBHOOK_SECRET` | 웹훅 HMAC(`X-Hub-Signature-256`) 검증. `/approve`·`/reject` 코멘트가 이 경로로 들어온다 | 비움 |
| `HITL_MINIO_*` | Run 로그/아티팩트 저장소 | localhost:9000, minioadmin |
| `HITL_LOG_LEVEL` / `HITL_LOG_FORMAT` | 로깅 | `INFO`, `console` |

## 3. e2e dry-run

repo 경로 + Goal → Plan → (자동 승인) → TaskDraft → `would create issue` → Scheduler → Coding Agent
→ tmp bare remote에 `ai/*` 브랜치. GitHub는 항상 Dry, LLM은 `--fake`(네트워크 0) 또는 `.env`의 provider.

```bash check
uv run python scripts/e2e_dry_run.py --fake tests/fixtures/sample_repo "Add a users API with tests" | tail -4
```

```bash
# 실 LLM (Anthropic 또는 Ollama, .env 설정대로). Plan+Issue까지만 보려면 --no-coding
uv run python scripts/e2e_dry_run.py tests/fixtures/sample_repo "Add a users API with tests"
uv run python scripts/e2e_dry_run.py --no-coding tests/fixtures/sample_repo "Add a users API with tests"
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

API로 같은 흐름을 돌리려면: `POST /projects {name, repo, members}` → `POST /projects/{id}/goals` → Goal이
`awaiting_plan_approval`이 되면 서명된 `issue_comment` 웹훅(`/approve`)을 `POST /webhooks/github`로
(`tests/api/test_goal_flow.py`의 `comment_body`/`signed` 참고) → `GET /projects/{id}/goals/{gid}` 진행률,
`GET /projects/{id}/events?since=<seq>` 또는 `WS /projects/{id}/stream`으로 이벤트.

## 4. 검사

```bash check
make lint
```

전체 게이트는 `make check`(ruff + mypy + pytest, 진짜 Redis 필요), Docker 통합은 `make test-integration`.

## 5. 흔한 에러

| 증상 | 원인 → 해결 |
|---|---|
| `docker compose up`에서 `port is already allocated` | 호스트 포트 점유 → `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` / `MINIO_HOST_PORT` / `MINIO_CONSOLE_HOST_PORT`로 바꾸고 `.env`의 URL도 맞춘다 |
| `ValueError: Could not deserialize key data` (GitHub App JWT) | `HITL_GITHUB_APP_PRIVATE_KEY`의 PEM 개행이 깨짐 → 개행을 `\n`으로 이스케이프한 한 줄로 넣는다 |
| 테스트에서 `respx.models.AllMockedAssertionError` | mock에 없는 URL/메서드로 호출 → 어댑터는 `GITHUB_API_BASE_URL`만 쓰고, 테스트 라우트를 그 경로로 등록한다(`assert_all_mocked=True`가 기본) |
| `httpx2`와 `respx` 혼용 오류 | Anthropic SDK는 `httpx2`, GitHub 어댑터는 `httpx` → SDK 호출은 `DefaultAsyncHttpxClient(transport=httpx2.MockTransport)`로, GitHub은 respx로 각각 mock |
| sqlite에서는 되는데 Postgres에서 체인 검증 실패/`asyncpg` 오류 | JSONB 왕복·enum 차이 → 서명은 `canonical_json` 텍스트로만(D-29), Postgres 전용 검증은 `tests/integration`(Docker) |
| `Docker not available` / `permission denied while trying to connect to the Docker daemon` | 통합 테스트는 skip(`-m integration`), 소켓 권한은 `echo "<cmd>" \| newgrp docker` 또는 `docker` 그룹 재로그인 |
