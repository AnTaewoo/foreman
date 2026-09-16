# Foreman 경계 — 지금 이 시스템으로 만들 수 있는 Goal의 수준

작성 2026-09-16 (main @ f1a6b95, MVP 1 + P9 공개 데모, LLM = 로컬 `qwen2.5-coder:14b`). 근거는 아래 실측 기록과
코드에 박힌 제약뿐이며, 추정은 "추정"으로 표시한다. Anthropic 모델 결과는 아직 없다(PC-5 pending).

## 0. 한 줄 판단

> **"작은 Python(Flask/pytest) 저장소에 새 모듈·함수·엔드포인트와 그 테스트를 *추가*하는, Task 3~6개짜리 Goal"** 까지다.
> 그 안에서도 Task 단위 성공률은 첫 시도 기준 약 1/3, 3회 재시도 후 약 1/2이고, Goal 전체가 사람 손 없이 끝까지 가는 경우는
> 아직 없다(사람이 PR을 Task마다 머지해야 다음 Task가 열린다). **기존 코드를 고치거나(refactor), 의존성을 추가하거나,
> Python 밖(프론트/다른 언어)으로 나가는 Goal은 지금 경계 밖이다.**

등급으로 나누면:

| 등급 | 뜻 | 예 |
|---|---|---|
| **A. 된다** | Plan → Issue → PR까지 대체로 자동, 사람은 승인·머지만 | 새 헬퍼 모듈 + 테스트, 새 GET 엔드포인트 + 테스트 |
| **B. 되지만 불안정** | 절반쯤의 Task가 3회 안에 통과, 나머지는 blocked → 사람이 Issue를 직접 처리 | 기존 라우트 파일(`main.py`)에 CRUD 추가, 입력 검증 추가 |
| **C. 안 된다** | 설계상 막히거나(의존성 승인 후 재개 경로 없음), 모델·컨텍스트가 감당 못 함 | 새 라이브러리 도입, 구조 리팩터, 프론트엔드, 대형 repo |

## 1. 판단 근거 (실측)

같은 fixture(Flask + 인메모리 UserStore + pytest, 파일 10여 개)에서:

| 실행 | 모델 | Goal / Task | Task 수 | done | 비고 |
|---|---|---|---|---|---|
| PC-4 (간단 Task) | 7b | maths 모듈 / users 모듈 / greet_all | 3 | **3/3 ×2회** | 새 파일 추가형. 1회차 통과, Task당 3 호출 10~16초 |
| PC-4 (기존 파일 수정) | 7b | 라우트 수정·삭제 동작 | 3 | 2/3, 2/3 | 모델이 쓴 테스트가 틀린 기대값 |
| PC-5 e2e | 14b | "Add a /users CRUD endpoint with tests" | 2 | 1 | 분해가 2개(기준 ≥3 미달) |
| X.2 튜닝 후 4회 | 14b | 같은 Goal | 6 / 9 / 8 / 6 | 1 / 0 / 1 / 1 | 분해는 좋아짐. 코딩 실패는 "전역 store를 import하지 않고 새로 만듦" 한 종류가 반복 |
| P9.5 비교 | 14b / gemma4:e4b | 같은 Goal ×2 | 4, 6 / 6, 6 | 1, 0 / 0, 1 | 동률 → 14b 유지 |
| PC-7 (실 GitHub) | 7b | "Add a maths helpers module…" | 4 | 1 (+1 in_review) | 사람 머지 → 의존 Task 자동 배정 확인. 1개는 owned_paths 오타로 blocked |
| PC-8 (docker 런처) | 14b | 같은 Goal | 6 | 2 | 2개 blocked(owned_paths 밖 쓰기 3회 / 테스트 Task 선행), 2개 영구 대기 |
| 공개 데모 showcase | 14b | 같은 Goal (실 GitHub) | 6 | 승인 후 3분 만에 PR 2개 | 나머지 4개는 사람 머지 대기 중 리셋 |

읽는 법: **"새 파일을 만드는 Task"는 거의 통과하고, "있는 파일을 고치는 Task"는 절반 이하**. Goal 하나에 두 종류가 섞이면
후자가 실패해 그 뒤 Task가 멈춘다.

## 2. 경계 조건 (코드에 박힌 것)

| 차원 | 지금 값 | 어디에 | 결과 |
|---|---|---|---|
| 대상 언어 | 심볼 색인은 **Python(.py)만** — 최상위 class/def + Flask 라우트 | `control_plane/orchestrator/context.py` | 다른 언어 repo는 Plan이 파일 트리만 보고 쓴다(사실상 C) |
| repo 크기 | 트리 depth ≤ 2, 설정 파일 4,000자, `docs/**.md` 4,000자, 심볼 ≤ 60 파일 × 40개 | 같은 파일 | 수십 파일 규모까지. 그 이상은 요약이 잘려 Plan이 repo를 모른다 |
| 모델 컨텍스트 | Ollama 기본 **4,096 토큰**(provider가 num_ctx를 안 정함) | `agents/llm/ollama.py`, `ollama ps` | 요약+프롬프트가 길면 조용히 잘린다. Anthropic이면 해당 없음 |
| 실행 가능한 명령 | `pytest`, `python -m pytest`, `ruff`, `mypy`, `npm test`, `npm run test`, `make`, `uv run pytest`만. 파이프·리다이렉트 금지, 600초. `PYTHONPATH`=repo 루트 | `agents/tools/shell.py` | 테스트가 이 명령 하나로 돌아야 한다. pyproject 없는 repo도 루트 모듈 import 가능 |
| 워커 실행 환경 | foreman 자체 venv(`uv sync --all-groups`; `fixtures` 그룹 = flask). **대상 repo 의존성은 설치하지 않는다** | `worker/Dockerfile` | stdlib + pytest + flask(+foreman 의존성)로 돌아가는 코드만 |
| 의존성 변경 | `pyproject.toml`/`requirements*`/`package.json`/락 파일을 건드리면 `needs_decision` → Issue 코멘트 + `task.blocked` | `agents/coding.py` | **MVP 1엔 재개 경로가 없다**(`task.retried` 미발행) → 그 Task와 후속은 멈춤 (C) |
| 쓰기 범위 | Task의 `owned_paths` 밖 쓰기는 툴이 거부 → `scope_violation` 실패 | `agents/tools/fs.py` | 모델이 분해 때 경로를 빠뜨리면 3회 실패 → blocked. PC-8·showcase의 주 실패 원인 |
| 재시도·시간 | run 최대 3회 × run 안의 편집→테스트 반복 3회, 워커 타임아웃 45분(+5분 정리), Goal당 Task ≥ 3 | `store/models.py`, `scheduler.py`, `agents/coding.py` | 3 run 뒤 blocked. 후속 Task는 **영원히 대기**(데드락 감지는 MVP 2). 실패마다 Issue 코멘트에 테스트 출력 꼬리 |
| 병렬 | owned_paths가 겹치면 직렬, 동시 워커 = `HITL_SCHEDULER_MAX_WORKERS`(GPU 1개면 2가 현실적) | `scheduler.py` | 라우트가 `main.py` 한 파일이면 전 Task 직렬 → 첫 실패가 전부를 막는다 |
| 사람 개입 지점 | (1) Plan 승인 — 콘솔 또는 Discussion `/approve` (2) **Task마다 PR 머지** — GitHub에서, 웹훅이 서버에 닿아야 done. 머지 뒤 후속 Task는 갱신된 `main`에서 분기 | 설계 §8, D-51 | 6-Task Goal = 머지 6번. 머지가 안 오면 의존 Task는 안 열린다 |
| 에이전트 종류 | Coding Agent 하나. Review/Research/Security 없음, PR은 draft, CI 결과 안 봄 | `agents/` | 코드 품질 검토는 사람 몫 |
| GitHub 전제 | App 설치, Discussions 켜짐 + 카테고리 `Plans`, 기본 브랜치에 **커밋 ≥ 1**(빈 repo 불가). 콘솔 "점검"이 9항목 확인 | `github_adapter/app_check.py` | 하나라도 빠지면 Plan 게시·clone·pytest에서 실패 |
| 보안 경계 | 워커는 secrets·토큰 없음(clone 캐시의 origin도 토큰 없는 URL), `.env` 읽기 거부, `main` push 거부, 임의 명령 거부 | `agents/tools/*`, `repo_cache.py` | Goal이 이 밖을 요구하면 실패가 정상 동작 |

## 3. 관찰된 실패 패턴 (빈도순)

1. **owned_paths 밖 쓰기** — 분해가 `tests/test_x.py`를 소유 경로에 안 넣었는데 모델은 테스트를 같이 쓰려 함 → 3회 거부 → blocked.
   *(2026-09-16 완화: 분해 검증이 테스트 경로를 요구하고, 없으면 파일·디렉토리 이름으로 보강한다 — ROADMAP §6 "P9 버그 #1")*
2. **테스트 Task와 구현 Task 분리** — "테스트 파일 만들기"가 구현보다 먼저 실행돼 import 실패 → 3회 → blocked.
   *(같은 날 완화: 테스트만 소유하고 구현 Task 하나에 의존하는 Task는 그 구현 Task로 합쳐진다)*
3. **기존 객체 재사용 실패** — Flask 앱의 전역 `store`를 import하지 않고 새 `UserStore()`를 만들어 테스트만 통과/실패 반복(14b가 지시를 3회 무시).
4. **중복 Task** — 선행 Task가 이미 구현한 함수를 "정의"하는 Task가 diff 0으로 PR만 생김(무해, 머지 필요).
5. **첫 Task 실패 → 나머지 대기** — 같은 파일을 공유해 직렬인 경우.
6. **의존성 파일 변경** → blocked(재개 없음).

## 4. 등급별 Goal 예시

**A. 된다** (검증됨 또는 같은 형태)
- `Add a maths helpers module with add and mul functions and tests`
- `Add a greet_all(names) helper in src/app/utils.py with tests`
- `Add GET /health returning {"status": "ok"} with a test`
- `Add a slugify(text) utility with tests` — 새 파일 + 테스트, 다른 파일 안 건드림

**B. 되지만 불안정** (Task 절반 통과, 나머지는 사람이 Issue를 닫거나 직접 구현)
- `Add a /users CRUD endpoint with tests` — `main.py` 수정, 전역 store 재사용 필요
- `Add input validation for POST /users and return 400 on bad payloads, with tests`
- `Add pagination (?page=&size=) to GET /users with tests`

**C. 안 된다** (지금 설계·환경 밖)
- `Persist users with SQLAlchemy` / `Add requests-based external API client` — 새 의존성 → blocked
- `Refactor routes into Flask blueprints` / `Split main.py into a package` — 여러 파일 동시 수정, 기존 테스트 유지 필요
- `Add a React frontend` / Go·Rust·TS repo — 심볼 색인·설치 없음
- `Fix the flaky CI` / `Add GitHub Actions workflow` — CI 실행·결과 확인 불가
- 파일 수백 개 repo, 문서가 긴 repo — 4k 컨텍스트에서 요약이 잘림

## 5. 시험할 때 Goal 쓰는 법

- 영어 한 문장, **무엇을 어느 파일에** 추가하는지 명시 ("in `src/app/utils.py`") — 분해가 owned_paths를 맞게 잡는다.
- "…with tests"를 붙이되, 테스트는 구현과 **같은 Task**에서 쓰이게 한다(분해 프롬프트가 분리 금지를 요구하지만 14b는 가끔 어긴다).
- 새 라이브러리를 요구하지 않는다. 표준 라이브러리 + flask + pytest 안에서.
- Goal 하나에 3~6 Task 크기(30분~2시간짜리). 그 이상은 쪼개서 Goal 여러 개로.
- PR이 열리면 GitHub에서 머지해야 다음 Task가 열린다. 머지가 플랫폼에 들어오려면 App 웹훅 URL이 서버를 가리켜야 한다.

## 6. 경계를 넓히는 순서 (효과 큰 것부터, 추정)

1. **Anthropic 모델**(`HITL_LLM_PROVIDER=anthropic`) — 실패 패턴 1·3은 모델 지시 이행 문제. 코드는 준비돼 있고 PC-5가 이걸 기다린다.
2. **워커에서 대상 repo 의존성 설치**(`uv sync`/`pip install -r` 허용 목록 추가) — 경계의 "실행 환경" 행을 없앤다.
3. **blocked 재개**(`task.retried` API/버튼)와 **데드락 감지**(MVP 2 §9.2) — 한 Task 실패가 Goal 전체를 멈추지 않게.
4. **Review Agent**(MVP 2) — PR 자동 검토로 사람 머지 부담을 줄인다.
5. 로컬 모델이면 `OLLAMA_CONTEXT_LENGTH`(또는 Modelfile num_ctx) 8k~16k — 요약 잘림 해소.
6. 비-Python 심볼 색인(TS/Go) — 다른 언어 repo.

## 7. 요약

| 질문 | 답 |
|---|---|
| 어떤 repo? | 작은 Python 프로젝트, pytest로 테스트, 의존성이 이미 워커 환경에 있는 것 |
| 어떤 Goal? | 새 모듈·함수·엔드포인트 + 테스트를 **추가**하는 것. 3~6 Task |
| 얼마나 자동? | Plan·Issue·브랜치·PR까지 자동. 승인 1번 + Task마다 머지 1번은 사람 |
| 얼마나 걸리나? | 14b 기준 Plan 1~3분, Task 시도당 1~3분, Goal 10~30분 + 머지 대기 |
| 무엇이 안 되나? | 기존 코드 리팩터, 의존성 추가, 다른 언어, 큰 repo, CI/인프라 변경 |
