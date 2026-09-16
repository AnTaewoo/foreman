# PC-8 — 외부 점검 절차 재실행 (fresh clone, docker 런처)

- 일시: 2026-09-16 11:14 ~ 11:23 KST
- 판정: **pass — 자동 5/5** (사용자 서명 대기)
- 구성: `main` @ c45a46a를 **새 디렉토리에 clone**해 README §1~§3을 그대로 실행. `.env`는 `cp .env.example .env` +
  `HITL_LLM_PROVIDER=openai_compat`, `HITL_LLM_MODEL=qwen2.5-coder:14b` 두 줄. 기본값 그대로: **docker 런처**,
  Postgres `hitl`(drop 후 `make migrate`), Redis 0(`FLUSHDB`), `HITL_REPO_ROOT=./repos`. API만 포트 8010(PC-7 프로세스가
  8000을 쓰고 있어서). 사전 초기화는 사용자 결정(2026-09-15).
- 대상 repo: `tests/fixtures/sample_repo`를 `git archive`로 꺼내 `git init`한 **REPO_ROOT 밖** 로컬 경로.
  Goal "Add a maths helpers module with add and mul functions and tests".

## 자동 항목

| # | 항목 | 결과 |
|---|---|---|
| — | `make check` | pass (482) |
| — | `scripts/check_runbook.sh` | 5/5 |
| 1 | 기동 직후 `bus.handler_failed`·`ProjectionTransient` 무한 재전달 0 | control plane 로그 29줄, handler_failed 0, Traceback 0, `projection_error` 0, XPENDING 0, retry 스트림 0 |
| 2 | REPO_ROOT 밖 로컬 repo → Goal → `/approve` → 워커 컨테이너 push → PR(dry) → done | Task #1 done(PR 1, dry merge → `main` 전진), #3 done(PR 2). 컨테이너 `--user 1003:960`, 마운트 `repos`+`demo-repo` 같은 경로. push된 ref 파일 소유자 = 호스트 uid |
| 3 | 같은 repo로 두 번째 프로젝트 | 409 `a project for '…/demo-repo' already exists`. 없는 경로 `/path/to/repo`는 400 |
| 4 | 스트림 distinct id == entry 수 | 137 == 137 (체인 68 + tool_called 69), `verify_chain_db` True |
| 5 | 컨테이너 `docker kill` → `task.failed{worker_died}` → 재배정 | kill 11:17:34 → `scheduler.reaped` 11:17:37 → 3초 뒤 같은 Task 재배정(attempt 2) |

API 승인 경로(D-51): 비멤버 `X-User-Id: stranger` 403 → owner 202 → 재요청 409. Plan(14b) 1분 50초, 분해 Task 6개,
전체 8분(Goal 생성 → 마지막 run).

## Task 결과 (모델 품질, 판정 대상 아님)

| Task | 결과 | 원인 |
|---|---|---|
| #1 Create maths_helpers.py | done (3회차) | 1·2회차 `scope_violation` — 모델이 owned 밖 `tests/test_maths_helpers.py`도 쓰려 함 → 툴 거부(`run.tool_denied`) |
| #2 Create test_maths_helpers.py | blocked | 1회차 `worker_died`(항목 5의 kill), 2·3회차 `tests_failed` — 분해가 테스트 Task를 구현 Task와 독립으로 만들어 import 대상이 없음 |
| #3 Implement add | done | #1이 이미 add를 넣어 diff 없음 → 같은 커밋으로 PR 2, ff 머지 |
| #4, #6 tests | ready(영구 대기) | #2에 의존 — blocked 의존 Task는 §9.2 데드락 감지(MVP 2) |
| #5 Implement mul | blocked | 3회 전부 `scope_violation`(같은 파일) |

X.2에서 본 것과 같은 종류(owned_paths 밖 쓰기, 테스트/구현 분리)로 플랫폼 동작은 설계대로: 거부 → 실패 → 재배정 → 3회 후 blocked.

## 실행 중 발견·수정

1. **`repos/`가 root 소유로 생김** — fresh clone에는 `HITL_REPO_ROOT`(`./repos`)가 없고, docker 런처가 그 경로를 `-v`로
   넘기자 Docker가 `root:root`로 만들었다(`drwxr-xr-x 0 0`). 이후 `owner/name` 프로젝트의 API 쪽 clone이 permission
   denied가 된다. → `build_launcher`가 repo_root를 먼저 `mkdir -p`(호스트 사용자 소유). 테스트
   `test_docker_launcher_creates_repo_root`.
2. **README 예제가 `jq`를 쓰는데 전제에 없었다** — 이 환경에 jq가 없어 첫 시도의 `PID`가 비었다. README §1 전제에 추가.
3. `GET /projects`는 `{items: [...]}` — README에 형태 명시.
4. 쓰기 직후 `GET …/goals/{gid}`는 404(projection 전) — README 문구대로. 몇 초 뒤 200.

## 사람 항목 (외부 점검 리포트의 확인 절차 6개)

| 절차 | 통과 |
|---|---|
| `cp .env.example .env`만으로 migrate·기동 | ○ (`env_ignore_empty`) |
| 워커 이미지 빌드 안내대로 `make worker-image` | ○ |
| 첫 Task 배정 → 컨테이너 기동 → push | ○ (host uid) |
| MinIO 없이 `make docker-up` | ○ (profile storage) |
| `POST /goals` 즉시 성공, 승인 API | ○ (202/403/409) |
| 없는 경로·중복 repo | ○ (400/409) |

내리기: `pc8_down.sh`(job tmp). 남은 상태: DB `hitl`·Redis 0에 이 실행의 이벤트 68건.
