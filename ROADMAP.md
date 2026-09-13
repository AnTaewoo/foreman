# ROADMAP.md — foreman (HITL Multi-Agent Dev Platform)

> 코딩 에이전트가 **실행하는** 문서. 세션 시작 시 읽고 §0대로 다음 Task를 고른다. 끝나면 §5 상태 보드를 직접 갱신한다.
> 우선순위: `CLAUDE.md` > `docs/design.md` > 이 파일 > `docs/prompts.md`(원본 의도 참고용). 충돌하면 상위를 따르고 이 파일을 고친다.
> 이 판(v2)은 1차 실행(2026-09-12~13, P0~P4.3)에서 확정된 결정·수정 사항을 처음부터 반영했다. §4는 전부 `확정`이라 결정 때문에 멈추는 지점은 없다.

---

## 0. 실행 프로토콜

**개발 절차는 GitHub을 쓰지 않는다.** 브랜치도 PR도 없다. 이 저장소 `main`에 커밋한다. GitHub은 플랫폼의 *제품 기능*이고, MVP 1 안에서는 전부 mock + `DRY_RUN=true`로만 존재한다 (D-01).

### 0.1 Task 절차

1. **다음 Task** — §5에서 `todo`이고 `depends_on`이 전부 `done`인 것 중 ID가 가장 작은 하나. 없으면 현재 Plan의 **PC**를 실행한다. PC가 `pass`(또는 `pass (조건부)`)가 아니면 다음 Plan의 Task는 시작하지 않는다.
2. **착수** — `[착수]` 보고(§0.2) → 상태 보드를 `running`으로 바꾸고 커밋 (`chore(<ID>): start`).
3. **Red** — Task의 `red`에 적힌 테스트를 먼저 쓴다. `make test`가 **실패하는 것을 확인** → `[Red]` 보고 → 커밋 (`test(<ID>): red`).
4. **Green** — `owned_paths` 안에서만 구현. `make test` 통과 → `[Green]` 보고 → 커밋 (`feat(<ID>): <제목>`). 커밋 본문 3줄: 무엇을 / 어떻게 검증 / 참조한 결정.
5. **Refactor** — 동작 변경 없이 정리. 커밋 (`refactor(<ID>): ...`). 없으면 생략.
6. **Gate** — Task의 `gate` 명령 전부 통과 → `[Gate]` 보고. 실패 시 `[Gate 재시도 n/3]` 보고 후 4로. **3회 실패면 `blocked`**, `[막힘]` 보고, §6에 기록, 멈춘다.
7. **완료** — 상태 보드 `done` + 커밋 해시 기입, 커밋 → `git push origin main`(원격 `AnTaewoo/foreman`, 이 저장소의 로컬 설정이 AnTaewoo SSH 키를 쓴다) → `[완료]` 보고. 바로 1로 돌아가 다음 Task. **멈추는 곳은 PC와 §6의 미결 항목뿐.**
8. **PC** — 자동 항목을 실행하고 `[PC 결과]` 보고(§0.2)를 출력한다. 사람 항목이 환경 문제(자격 증명 없음, 권한 없음 등)로 불가능하면 §6에 원인과 필요한 조치를 적고 **사용자에게 묻고 멈춘다.** 사용자가 "진행"을 지시하면 보드에 `pass (조건부)`로 기록하고 이월 항목을 §6에 남긴 뒤 다음 Plan을 시작한다.
9. **세션** — 한 세션 = 한 Plan이 기본. 사용자가 "계속"이라 하면 다음 Plan을 이어서 한다. 세션 시작 시 `/clear`.

항상 수정 허용: `ROADMAP.md`, `uv.lock`, `docs/pc/**`, `docs/progress.md`. 그 외는 Task의 `owned_paths`만.

### 0.2 보고 프로토콜 (프로세스 **안**에서, 체크포인트마다)

보고는 툴 호출 사이에 **일반 텍스트**로 출력한다. 요약이지 로그가 아니다. 규칙:

- 체크포인트마다 아래 블록 하나. **보고 없이 다음 단계로 넘어가지 않는다.**
- 블록 하나는 **10줄 이내**. 툴 출력·스택트레이스·파일 전문을 붙이지 않는다 (필요하면 파일 경로만).
- 같은 내용을 `docs/progress.md`에 **한 줄**로 append 한다: `| <시각> | <ID> | <체크포인트> | <핵심 1줄> | <커밋> |` (사람이 나중에 훑는 용도).
- 설계·ROADMAP과 다르게 만든 것이 있으면 `[Green]`의 "조정" 줄에 반드시 적고, 근거를 §6 `(기록)` 행으로 남긴다.
- 사용자 질문이 필요한 것은 `[막힘]`으로만 낸다. 그 외 보고에서는 질문하지 않는다.

```
[P1.4 착수]
- 목표: <1줄>
- 만들 파일: <owned_paths 중 실제로 손댈 것>
- 참조 결정: D-06, D-08
- 리스크/불확실: <1줄, 없으면 "없음">

[P1.4 Red]
- 테스트: tests/events/test_bus.py — <케이스 수>개 (a)~(f)
- 실패 확인: <실패 이유 1줄, 예: ModuleNotFoundError bus>
- 커밋: <hash>

[P1.4 Green]
- 구현: <3줄 이내 — 무엇을 어떻게>
- make test: <N passed>
- 조정: <설계/ROADMAP과 다른 점, 없으면 "없음">
- 커밋: <hash>

[P1.4 Gate]
| 명령 | 결과 |
| make check | pass (N tests) |
| <추가 gate> | pass / fail(원인 1줄) |
- 시도: 1/3

[P1.4 완료]
- 커밋: <hash>  보드: done
- 다음: P1.5 (depends_on 충족)

[P1.4 막힘]
- 원인: <1줄>
- 시도한 것: <2줄 이내>
- 필요한 결정: A) … B) … (추천: B)  ← 사용자 답 필요

[PC-1 결과]
- 자동: <항목별 pass/fail 1줄씩>
- 사람: <해야 할 일 1줄씩, 불가능하면 이유>
- 판정: pass / pending / fail
```

### 0.3 금지

- `owned_paths` 밖 수정. 필요하면 §6에 "scope_expand: <파일> <이유>" 적고 **`[막힘]`으로 묻는다.** (단, Task `owned_paths`에 `pyproject.toml`이 있으면 의존성 추가는 허용.)
- §4에 없는 결정. 옵션을 §6에 적고 `[막힘]`으로 묻는다.
- `control_plane/events/schema.py`의 필드·이름 삭제/변경. PC-1 이후 추가만.
- `DRY_RUN` 기본값을 코드에서 바꾸기.
- 실제 GitHub API 호출. MVP 1 전 구간에서 네트워크로 나가는 GitHub 호출은 0 (테스트는 respx `assert_all_mocked=True`).
- Gate 실패 상태에서 `main` 커밋. **파이프(`| tail`) 뒤의 종료 코드는 make의 것이 아니다** — 게이트 판정은 `make check; echo $?` 또는 `make check || exit 1`로 한다.

상태: `todo` | `running` | `blocked` | `done`. PC: `pending` | `pass` | `pass (조건부)` | `fail`.

---

## 1. Goal (이 로드맵의 범위)

**MVP 1 — Repo → Goal → Orchestrator → Issue → Coding Agent → PR** (설계 §15). "Issue/PR"은 DRY_RUN 로그와 로컬 git으로 검증하고, 실 GitHub 연결은 PC-5 통과 후 **후속 세션**(§8)에서 한다.

완료 = PC-5 pass.

## 2. Plan 구조

```
P0 스캐폴딩 ─PC-0─► P1 이벤트+Store ─PC-1─► P2 GitHub Adapter(mock) ─PC-2─► P3 Orchestrator ─PC-3─►
P4 Coding Agent+Worker ─PC-4─► P5 API+e2e ─PC-5 = MVP 1 (dev)─► [후속] 실 GitHub 검증
```

## 3. Task 형식

```
### <ID> <제목>
- depends_on / owned_paths(glob, 이 밖은 금지) / red(테스트) / green(구현) / gate(명령) / design(§) / notes
```

---

## 4. 결정 로그 (전부 확정)

| ID | 결정 | 적용 |
|---|---|---|
| D-01 | 개발 절차에 GitHub 미사용. `main` 직접 커밋, 사람 검토는 PC. CLAUDE.md의 "main push 금지"는 **플랫폼이 관리하는 대상 repo**에 대한 제품 규칙(P4.1 git 툴) | 전체 |
| D-02 | LLM 추상화는 `agents/llm/` **패키지**: `base.py`(ModelProvider), `anthropic.py`, `fake.py`. CLAUDE.md의 `agents/llm.py`는 오기 | P3.1 |
| D-03 | `structlog`, `pre-commit`, `docker-compose(postgres16/redis7/minio)`, `.env.example`, `GET /health`는 P0 범위 | P0.3 |
| D-04 | 이벤트 `payload`는 `dict[str, Any]`, P1 발행 타입은 TypedDict로 형태 문서화. 동결 후 강타입화는 "추가" | P1.1 |
| D-05 | ULID = `python-ulid`. 단위 테스트 DB = aiosqlite, Postgres 전용은 `tests/integration/`. sqlite는 tz를 버리므로 `UTCDateTime` TypeDecorator로 UTC를 보장한다 (서명 안정성) | P1.2 |
| D-06 | `bus.publish` = **outbox**: `events` 테이블에 `published_at NULL`로 insert → relay가 XADD 후 `published_at` 채움. at-least-once, projection 멱등으로 흡수 | P1.4 |
| D-07 | projection이 불허 전이 이벤트를 받으면 **nack 후 재시도 N=3**(consumer group pending + XCLAIM 재전달, `Delivery.attempt`). 소진 시 `events.projection_error` 기록 후 ack (스트림이 막히지 않게) | P1.4, P1.5 |
| D-08 | §6.1 전이 테이블은 `control_plane/store/transitions.py` 한 곳. projection과 API 양쪽이 이걸 호출 | P1.2, P1.5 |
| D-09 | GitHub GraphQL Discussions는 `docs.github.com`을 **web fetch로 확인**한 뒤 구현. 확인된 형태: `repository(owner,name){id discussionCategories{nodes{id name slug}} discussions(first,after,categoryId,orderBy){pageInfo nodes{id number title body url category{id name}}}}`, `createDiscussion(input:{repositoryId,categoryId,title,body}){discussion{id number title url}}`, `addDiscussionComment(input:{discussionId,body}){comment{id url}}`. 출처 URL을 docstring에 남긴다 | P2.3 |
| D-10 | DRY_RUN 계층은 `github_adapter/dry_run.py` 하나. `GitHubClient` Protocol을 `DryRunGitHubClient`가 같은 시그니처로 구현, 팩토리 `get_github_client(settings)`가 교체. 테스트는 respx `assert_all_mocked=True, assert_all_called=False` | P2.5 |
| D-11 | `orchestrator/context.py`의 repo 입력은 **로컬 경로**만. URL은 `NotImplementedError("GitHub tree API: MVP1 이후")` | P3.2 |
| D-12 | LangGraph 체크포인터 = Postgres (`langgraph-checkpoint-postgres`, `AsyncPostgresSaver.from_conn_string`은 async context manager라 runner가 연다). 단위 테스트는 `MemorySaver` | P3.4 |
| D-13 | Plan 승인 interrupt 재개는 P5.2에서 서명된 가짜 웹훅(`issue_comment` + `/approve`)을 테스트가 직접 POST | P3.4, P5.2 |
| D-14 | 개발용 git "원격"은 **로컬 bare repo**(`tests/fixtures/make_remote.sh`가 생성). Coding Agent의 push는 여기로 | P4.1, P4.4 |
| D-15 | 워커 기동 = **subprocess로 `docker` CLI** (`--format json` 파싱). 테스트는 `FakeLauncher` | P4.5 |
| D-16 | needs_decision(의존성 파일 변경)은 MVP 1에서 Issue 코멘트 "승인 필요" + `task.blocked`. Decision 엔티티는 MVP 3 | P4.3 |
| D-17 | 워커 컨테이너·Postgres 통합 테스트는 `tests/integration/`, Docker 없으면 `pytest.skip`. `make test`는 항상 제외 | P1.3, P4.4 |
| D-18 | e2e(PC-5)는 **실 Anthropic + DRY_RUN GitHub + 로컬 bare remote**. 실 GitHub은 §8 | P5.4 |
| D-19 | 스키마 동결 전에 `goal.blocked`(goal), `project.created`, `project.updated`(control) 포함. `emergency_stop`은 `<domain>.<name>` 규칙에 맞춰 `control.emergency_stop` | P1.1 |
| D-20 | Task 상태 이벤트 매핑(설계 §6.1 우선): `task.created`(+`issue_number` 있으면 draft→ready) / `task.assigned`→assigned / `task.started`→running / `task.completed`→**in_review** / `task.failed`→ready(attempt<max) 또는 blocked / `task.blocked`→blocked / `pr.merged`→done. 발행 주체: `task.created`=emit(P3.5), `task.assigned`=Scheduler(P4.5), `task.started/completed/failed/blocked`·`pr.opened`=Coding Agent(P4.3). `task.ready` 이벤트는 만들지 않는다 | P1.5, P3.5, P4.3, P4.5 |
| D-21 | Anthropic SDK 1.x는 `httpx2` 기반 → respx가 못 가로챈다. `/v1/messages` mock은 `httpx2.MockTransport`를 `AnthropicProvider(transport_handler=...)`로 주입. `schema` 강제는 tool_use 대신 **structured outputs**(`messages.parse(output_format=Model)`) — 최신 모델은 `tool_choice: any/tool`이 400. 기본 모델 `claude-opus-5` | P3.1 |
| D-22 | Issue 마커 존재 확인은 search API(인덱스 지연) 대신 `GET /repos/{repo}/issues?labels=ai:task&state=all` 목록 스캔 | P2.2 |
| D-23 | `agents/`·`worker/`는 `control_plane.config` import 금지(AST 가드). `get_provider(settings)`는 `anthropic_api_key`만 가진 구조적 Protocol로 받고, 키가 비면 `fake=True`일 때만 FakeProvider, 아니면 `ProviderConfigError` | P0.1, P3.1 |
| D-24 | DB 갱신 가드(P1.5 (f))는 AST 기반. 허용 예외: `events/outbox.py`의 `update(Event)`(published_at 부기)와 `bus.py`의 `insert(Event)`(append). 수신자가 `session`류 이름일 때만 검사 | P1.5 |

---

## 5. 상태 보드

### P0 — 스캐폴딩 (`docs/prompts.md` P0)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P0.1 | 골격 + 툴체인 + `Settings(dry_run=True)` + import 가드 | — | done | 16f90dd |
| P0.2 | 설계 문서 확인 (`docs/design.md` 존재·§ 번호 참조 가능) | P0.1 | running | |
| P0.3 | 개발 인프라: compose, `.env.example`, Makefile 확장, pre-commit, structlog, `/health` | P0.1 | todo | |
| **PC-0** | 스택이 뜬다 | P0.3 | pending | |

### P1 — 이벤트 스키마 + State Store (`docs/prompts.md` P1)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P1.1 | `events/schema.py` — Event, EventType(§4.2 + D-19), 해시 체인 | PC-0 | todo | |
| P1.2 | `store/models.py` + `store/enums.py` + `store/transitions.py` | PC-0 | todo | |
| P1.3 | Alembic 초기 마이그레이션 + pg append-only 트리거 + `store/session.py` | P1.2 | todo | |
| P1.4 | `events/bus.py` + `events/outbox.py` — publish(outbox) / subscribe / replay | P1.1, P1.3 | todo | |
| P1.5 | `events/projection.py` — 유일한 DB 갱신 지점 | P1.4 | todo | |
| **PC-1** | 이벤트 한 바퀴 + 스키마 동결 | P1.5 | pending | |

### P2 — GitHub Adapter, mock only (`docs/prompts.md` P2)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P2.1 | `auth.py` — App JWT → installation token 발급/캐시/갱신 | PC-1 | todo | |
| P2.2 | `client.py` + `protocol.py` + `markers.py` — REST 멱등 메서드 7개 + `ensure_labels` | P2.1 | todo | |
| P2.3 | `discussions.py` — GraphQL create/list/comment | P2.1 | todo | |
| P2.4 | `webhooks.py` — HMAC 검증 + 6종 이벤트 → 내부 Event 변환 + 슬래시 명령 훅 | P1.5 | todo | |
| P2.5 | `dry_run.py` + `__init__.py` — DryRun client 2개 + 팩토리 | P2.2, P2.3 | todo | |
| **PC-2** | 어댑터 전 메서드 mock 통과, 실 네트워크 0 | P2.4, P2.5 | pending | |

### P3 — Orchestrator (`docs/prompts.md` P3)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P3.1 | `agents/llm/` — ModelProvider, Anthropic 어댑터, FakeProvider | PC-2 | todo | |
| P3.2 | `orchestrator/context.py` — RepoSummary (로컬 경로) + `sample_repo` 픽스처 | PC-2 | todo | |
| P3.3 | `orchestrator/prompts/*.md` + `drafts.py` (TaskDraft, 검증·재시도) | P3.1 | todo | |
| P3.4 | `orchestrator/graph.py` + `state.py` — 5노드 그래프, interrupt, 체크포인터 | P3.2, P3.3 | todo | |
| P3.5 | `orchestrator/emit.py` — 위상 정렬, 사이클, owned_paths 직렬화, Issue(dry) 생성 | P3.4 | todo | |
| **PC-3** | Fake로 그래프 완주 + 실 LLM dry-run 눈검사 | P3.5 | pending | |

### P4 — Coding Agent + Worker (`docs/prompts.md` P4)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P4.1 | `agents/tools/` — base / fs / shell / git / github, 차단 규칙 | PC-3 | todo | |
| P4.2 | `agents/base.py` + `agents/context.py` — AgentInput/Output, BaseAgent, 컨텍스트 조립 §5.3 | P4.1 | todo | |
| P4.3 | `agents/coding.py` — 그래프, owned_paths diff 검사, needs_decision | P4.2 | todo | |
| P4.4 | `worker/` — Dockerfile, entrypoint, 45분 타임아웃 | P4.3 | todo | |
| P4.5 | `scheduler/` — task.created 구독, ready 판정, 워커 기동 | P4.4 | todo | |
| **PC-4** | 로컬 bare remote에 브랜치 3개 push | P4.5 | pending | |

### P5 — API + e2e (`docs/prompts.md` P5)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P5.1 | `api/` — 라우터 6개 + Idempotency-Key | PC-4 | todo | |
| P5.2 | Goal → Orchestrator 백그라운드, `/approve` 웹훅 → resume, 권한 검사 | P5.1 | todo | |
| P5.3 | `WS /projects/{id}/stream` | P5.1 | todo | |
| P5.4 | `scripts/e2e_dry_run.py` | P5.2 | todo | |
| P5.5 | `docs/runbook.md` | P5.4 | todo | |
| **PC-5 = MVP 1 (dev)** | e2e dry-run 완주 + 품질 서명 | P5.3, P5.5 | pending | |

---

## 6. Blocked / 결정 요청 / 기록 로그

| 일시 | Task | 사유 | 옵션 / 필요한 조치 |
|---|---|---|---|
| | | | |

---

## 7. Task 상세

### P0.1 골격 + 툴체인 + Settings + import 가드
- depends_on: —
- owned_paths: `pyproject.toml`, `Makefile`, `.gitignore`, `control_plane/**/__init__.py`, `agents/**/__init__.py`, `github_adapter/__init__.py`, `worker/__init__.py`, `control_plane/config.py`, `.ai-platform/**`, `tests/__init__.py`, `tests/test_scaffold.py`
- red: `tests/test_scaffold.py` — (a) §15.1 패키지 전부 import 가능 + 한 줄 docstring (`control_plane.{api,orchestrator,scheduler,events,store}`, `agents`, `agents.tools`, `agents.llm`, `github_adapter`, `worker`) (b) `Settings(_env_file=None).dry_run is True`, `HITL_` 프리픽스, 비밀값은 `SecretStr`이고 repr에 안 나옴 (c) AST: `agents/`, `worker/` 아래 `control_plane.config` import 없음 (d) ruff `banned-api`에 `anthropic`, `per-file-ignores`에 `agents/llm/anthropic.py`=TID251 (e) `.ai-platform/autonomy.yaml`, `CONTEXT.md` 샘플 존재
- green: uv `pyproject.toml`(deps: fastapi, uvicorn[standard], langgraph, langgraph-checkpoint-postgres, langchain-core, sqlalchemy[asyncio], asyncpg, alembic, redis, pygithub, httpx, pydantic, pydantic-settings, python-ulid, structlog, pyjwt[crypto], anthropic>=1.0; dev: pytest, pytest-asyncio, respx, ruff, mypy, pre-commit, pyyaml, types-pyyaml, aiosqlite, fakeredis), ruff(line 100, `exclude=["docs",".venv"]`, isort `known-third-party=["alembic"]`), mypy(`control_plane.*` strict), pytest(`testpaths=["tests"]`, `norecursedirs=["fixtures",".venv","node_modules",".git"]`, `asyncio_mode=auto`, `integration` 마커), Makefile `install/lint/typecheck/test/test-integration/check`
- gate: `make check`
- design: §14, §15.1
- notes: D-02, D-23. 의존성을 **여기서 전부** 넣어 이후 Task에서 scope_expand가 안 생기게 한다. ruff E501은 한글을 폭 2로 센다 — 독스트링을 짧게.

### P0.2 설계 문서 확인
- depends_on: P0.1
- owned_paths: `docs/design.md` (읽기만)
- red: 없음
- green: `docs/design.md`에 §4.1, §4.2, §5.2, §6.1, §7.2, §7.3, §10.1, §15.1이 존재하는지 확인만. 없으면 `[막힘]`
- gate: —
- notes: 문서는 이미 있다. 이 Task는 참조 가능성 검증용 1분짜리.

### P0.3 개발 인프라
- depends_on: P0.1
- owned_paths: `docker-compose.yml`, `.env.example`, `Makefile`, `.pre-commit-config.yaml`, `control_plane/logging.py`, `control_plane/api/app.py`, `control_plane/config.py`, `tests/test_infra.py`
- red: `tests/test_infra.py` — (a) `.env.example`의 키 집합 == `Settings.model_fields` 키 집합(대문자, `HITL_` 프리픽스) (b) `docker-compose.yml` 파싱: 서비스 `postgres`, `redis`, `minio` 존재, 각각 `healthcheck`와 named volume (c) `GET /health` → 200 `{"status":"ok"}` (d) `configure_logging()` 후 structlog가 JSON 렌더러(`HITL_LOG_FORMAT=json`) / 콘솔 렌더러
- green: compose(postgres:16, redis:7, minio, healthcheck, volumes, 호스트 포트는 `${POSTGRES_HOST_PORT:-5432}` 식으로 덮어쓰기 가능), `.env.example`(값 비움, 주석), Makefile에 `run-api / run-worker / migrate / docker-up(`up -d --wait`) / docker-down`, `.pre-commit-config.yaml`(local hooks: `uv run ruff check --fix`, `uv run ruff format`, `uv run mypy …`), `control_plane/logging.py`, `app.py`에 `create_app()` + `/health` + `app()` factory
- gate: `make check` && `uv run pre-commit run --all-files` && `docker compose config -q`
- design: §14
- notes: D-03. `Settings`에 `log_format`, `minio_*` 추가는 이 Task 소유. `/health`는 liveness만. structlog 호출에서 `event=` 키워드는 예약어 — `gh_event` 등으로.

### PC-0 스택이 뜬다
- 자동: `make docker-up` → 3개 서비스 `healthy` (`docker compose ps --format json`) → `make run-api &` → `curl -sf localhost:8000/health` → `make docker-down`. 호스트 포트가 점유돼 있으면 환경변수로 덮어쓴다(§9).
- 자동: `make check`, `pre-commit run --all-files`
- 사람: 없음. 자동으로 `git remote -v`가 `git@github.com:AnTaewoo/foreman.git`이고 `git var GIT_AUTHOR_IDENT`가 `AnTaewoo <…noreply…>`인지 `[PC-0 결과]`에 적는다
- pass: 자동 전부

---

### P1.1 이벤트 스키마
- depends_on: PC-0
- owned_paths: `control_plane/events/schema.py`, `tests/events/**`
- red: (a) `EventType` 멤버 전부 `<domain>.<name>`, §4.2 도메인 7개 + D-19 추가분이 **하나도 빠짐없이** 존재 — 테스트에 표를 리터럴로 박아 양방향 비교 (b) `Event` JSON 라운드트립, `id`가 ULID, `ts`는 UTC aware (c) `sign(prev_signature, event)` 결정적 — payload 키 순서 무관, `signature` 필드 제외 (d) `verify_chain([...])` 정상 True, 변조/순서 바꿈/미서명 → False (e) `correlation_id`, `causation_id` 없으면 ValidationError, `Actor.type`은 agent|human|system|github (f) P1 발행 타입 10개(goal.created, task.created, task.assigned, task.started, task.completed, task.failed, run.started, run.tool_called, run.finished, pr.opened)의 payload TypedDict가 `PAYLOAD_TYPES`에 등록
- green: pydantic v2 frozen `Event`, `Actor`, `Subject`, canonical JSON = `json.dumps(model_dump(mode="json", exclude={"signature"}), sort_keys=True, separators=(",",":"), ensure_ascii=False)` SHA-256(prev ‖ "\n" ‖ body). `ts` validator: naive→UTC, aware→UTC 정규화
- gate: `make check`
- design: §4.1 Event, §4.2
- notes: D-04, D-05, D-19. **PC-1 이후 이 파일은 추가만.**

### P1.2 Store 모델 + 전이 테이블
- depends_on: PC-0
- owned_paths: `control_plane/store/models.py`, `control_plane/store/enums.py`, `control_plane/store/transitions.py`, `tests/store/test_models.py`, `tests/store/test_transitions.py`
- red: `test_transitions.py` — (a) §6.1의 **모든** 화살표가 `ALLOWED`에 있고 표에 없는 전이는 없음(리터럴 비교; `ready→ready` 자기 전이 포함, `in_review→done`만) (b) `assert_transition`이 불허 시 `InvalidTransition`(메시지에 두 상태 이름) (c) `any → cancelled` (d) `blocked`에서 나가는 전이는 `ready`, `cancelled`뿐 (e) `GOAL_ALLOWED`(draft→planning→awaiting_plan_approval→active→done, awaiting→planning, active/blocked 왕복, any→cancelled), `DECISION_ALLOWED`(§6.2). `test_models.py` — aiosqlite로 (f) 8개 테이블 (g) Project→Goal→Epic→Task→Run 왕복 (h) `Task.status`에 Enum 밖 문자열 → 예외 (i) `events` 행 저장 후 `ts`가 tz-aware UTC로 돌아옴
- green: SQLAlchemy 2.x async `DeclarativeBase`, §4.1 필드 전부, JSON은 `JSON().with_variant(JSONB(), "postgresql")`, Enum은 `Enum(cls, values_callable, create_constraint=True, validate_strings=True)`, `UTCDateTime(TypeDecorator)`, `events`에 `published_at / projected_at / projection_error` nullable 부기 컬럼. `enums.py`: TaskStatus, GoalStatus, EpicStatus, RunOutcome, DecisionStatus, DecisionType, AgentStatus, TaskKind, Role, RiskTier. `transitions.py`: PEP 695 제네릭 `assert_transition[S: StrEnum](src, dst, table=None)`
- gate: `make check`
- design: §4.1, §6.1, §6.2
- notes: D-05, D-08.

### P1.3 Alembic
- depends_on: P1.2
- owned_paths: `alembic.ini`, `alembic/**`, `control_plane/store/session.py`, `tests/store/test_migrations.py`, `tests/integration/**`
- red: (a) aiosqlite로 `upgrade head` → 8 테이블 → `downgrade base` → 0 (b) `compare_metadata` diff == [] (c) `create_engine(settings)` / `create_session_factory` / `get_session()` async context manager (d) integration: Postgres에서 `events` UPDATE(부기 컬럼 외)/DELETE 시 트리거 거부, `published_at` UPDATE는 허용. raw INSERT는 `ts`를 명시할 것
- green: `env.py` async(이미 실행 중인 루프 안이면 스레드로 격리), `alembic.ini`에 `path_separator = os`, revision 0001(Postgres 공유 enum `role`/`risk_tier`는 `_pg_enums(create)`로 한 번만 생성, `postgresql.ENUM(create_type=False)` variant), revision 0002 트리거(`dialect != postgresql`이면 no-op)
- gate: `make check` && (`docker compose up -d --wait postgres && make test-integration` — Docker 있으면)
- design: §4.1 append-only, §14
- notes: D-05, D-17. autogenerate는 `HITL_DATABASE_URL=sqlite+aiosqlite:///tmp.db`로.

### P1.4 Event Bus + Outbox
- depends_on: P1.1, P1.3
- owned_paths: `control_plane/events/bus.py`, `control_plane/events/outbox.py`, `tests/events/test_bus.py`
- red: fakeredis(`fakeredis.aioredis.FakeRedis(decode_responses=True)`) + aiosqlite로 (a) `publish(session, event)` → `events` 행 insert, `signature`는 같은 project의 직전 이벤트에 체인(세션 넘어서도) (b) insert 직후 `published_at IS NULL`, `OutboxRelay.relay_once()` 후 XADD + `published_at` (c) `_mark_published`를 monkeypatch로 죽이면 XADD만 되고 재실행 시 한 번 더 XADD(at-least-once) (d) `subscribe(group, handler, consumer=, project_id=, block_ms=, reclaim_idle_ms=)` — consumer group 생성, 정상 반환 시 XACK, 예외 시 ack 안 함 → XCLAIM 재전달, `Delivery.attempt` 1,2,3 증가 (e) `replay(project_id, since=None)` XRANGE 순서, event.id 중복 제거 (f) `stream_key("P1") == "events:P1"`
- green: `EventBus(redis)`: publish(insert + flush, asyncio.Lock으로 체인 직렬화), subscribe(`project_id=None`이면 `SCAN events:*`), replay. `OutboxRelay(session_factory, redis, poll_interval, batch)`: `relay_once`, `run`, `start/stop`. redis-py 결과는 `cast`(decode_responses 전제)
- gate: `make check`
- design: §3.2 Event Bus, §1.3-2
- notes: D-06, D-07. `Handler = Callable[[Delivery], Awaitable[None]]`.

### P1.5 Projection
- depends_on: P1.4
- owned_paths: `control_plane/events/projection.py`, `tests/events/test_projection.py`, `tests/test_projection_guard.py`
- red: (a) `project.created → goal.created → task.created(issue_number) → task.assigned → run.started → task.started → run.tool_called → pr.opened → run.finished → task.completed → pr.merged` 순서 → Task `ready→assigned→running→in_review→done`, Run outcome/tokens/tool_calls 반영, `events.projected_at` 채워짐 (b) 같은 event id 두 번 → 동일 (c) `task.completed`가 `task.started`보다 먼저 → `apply`는 `InvalidTransition`; `handle(Delivery(attempt=1))` → `ProjectionRetry`; `attempt=3` → 정상 반환 + `events.projection_error` 기록 (d) `decision.*`, `policy.*`, `budget.*` 등 MVP 1 미사용 타입은 `noop` 핸들러가 등록(함수 이름 `noop`) (e) 모든 EventType이 `HANDLERS`에 있고, 빠지면 `UnhandledEvent`. `test_projection_guard.py` — (f) AST: `control_plane/` 아래 `projection.py` 외 파일에서 `session.add/add_all/merge/delete(`, `session.execute(update|delete(...))` 없음 (D-24 예외 적용, 수신자 이름이 `session`류일 때만)
- green: `HANDLERS: dict[EventType, Handler]`, `@on(...)` 데코레이터, 모든 전이는 `assert_transition`, `Projection(session_factory, max_attempts=3).apply(event, force=False)` / `.handle(delivery)`, `apply(force=True)`는 replay 재구축용
- gate: `make check`
- design: §3.2 State Store, §6.1
- notes: D-07, D-08, D-20, D-24. `task.failed`는 payload.attempt로 `attempt_count` 갱신 후 ready/blocked 분기. `goal.plan_proposed`는 draft→planning→awaiting을 한 번에.

### PC-1 이벤트 한 바퀴 + 스키마 동결
- 자동: `make docker-up` → `uv run alembic upgrade head` → `scripts/pc1_roundtrip.py`(이 PC가 소유, `scripts/**`): `project.created` + `goal.created` + `task.created`×3 + Task별 `assigned→started→completed→pr.merged` 발행 → relay → projection consumer → Postgres `tasks` 3행 `done` → `verify_chain` True → `TRUNCATE tasks, runs, epics, goals CASCADE` → `replay` + `apply(force=True)` → 동일 → `make docker-down`
- 자동: `make check`, `make test-integration`
- 사람: `git tag event-schema-v1`, `docs/pc/PC-1.md`에 결과 붙여넣기 (에이전트가 대신 해도 됨 — 로컬 태그)
- pass: 자동 전부 + 태그 존재

---

### P2.1 GitHub App 인증
- depends_on: PC-1
- owned_paths: `github_adapter/auth.py`, `tests/github_adapter/**`
- red: respx로 (a) App JWT RS256, `iss`=app id, `exp-iat == 600` (b) `POST /app/installations/{id}/access_tokens` → 토큰 캐시(두 번 호출에 POST 1회), 헤더 `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28` (c) 주입 가능한 `now()`로 만료 5분 전 갱신, 그 전엔 네트워크 0 (d) `InstallationAuth(httpx.Auth)`: 401 → 재발급 1회 후 재시도, 두 번째 401은 그대로 반환 (e) `conftest.py`: `respx.mock(base_url="https://api.github.com", assert_all_mocked=True, assert_all_called=False)` autouse — **미매칭 요청 = 테스트 실패**; RSA 키 픽스처는 `cryptography`로 생성
- green: `app_jwt()`, `InstallationTokenProvider(app_id, private_key_pem, installation_id, client, now)`, `InstallationAuth`
- gate: `make check`
- design: §7.1, §12
- notes: 여기서 만든 conftest가 P2 전체를 덮는다.

### P2.2 REST client
- depends_on: P2.1
- owned_paths: `github_adapter/client.py`, `github_adapter/protocol.py`, `github_adapter/markers.py`, `tests/github_adapter/test_client.py`
- red: respx로 메서드마다 **정상 1 + 멱등 1**: (a) `ensure_labels(repo)` §7.2 라벨 전부(ai:task, role:*, tier:T0~T3, status:ready/running/blocked/awaiting-decision, kind:*(언더스코어→하이픈), human:override), 있는 건 POST 0 (b) `create_task_issue(repo, TaskIssue)` 본문 상단 `<!-- ai-platform:meta task=<id> -->`, 라벨 ai:task+role+kind+tier(+epic:<n>), milestone; 마커가 있으면(D-22 목록 스캔) 그 번호, POST 0 (c) `update_issue_status_label` 기존 `status:*` 제거 후 PUT, 같으면 PUT 0 (d) `comment(repo, n, body, key=None)` — key 마커 코멘트가 있으면 재작성 안 함 (e) `create_branch` 있으면 skip (f) `open_pr(head, base, title, body, draft, meta)` §7.3 메타 블록, 같은 head→base 열린 PR 있으면 반환 (g) `create_milestone` 같은 제목 반환 (h) 4xx/5xx → `GitHubError`
- green: `protocol.py`에 `@runtime_checkable GitHubClient(Protocol)` + pydantic I/O 모델(TaskIssue, EpicMilestone, PrMeta, IssueRef, PullRef, CommentRef, BranchRef, MilestoneRef). `markers.py`(issue/pr/comment 마커 생성·파싱). `client.py`는 **httpx 직접**, PyGithub 미사용
- gate: `make check`
- design: §7.2, §7.3
- notes: D-10, D-22.

### P2.3 GraphQL Discussions
- depends_on: P2.1
- owned_paths: `github_adapter/discussions.py`, `tests/github_adapter/test_discussions.py`
- red: respx `POST /graphql`로 (a) `create_discussion(repo, category, title, body)` — repositoryId/categoryId 조회 쿼리 1 + mutation 1; 같은 제목+카테고리가 조회 결과에 있으면 반환(mutation 0); 없는 카테고리 → `LookupError` (b) `list_discussions(repo, category)` — 카테고리 조회 1(repo별 캐시) + 페이지 N(`after` 커서) (c) `errors` 배열 → `GitHubGraphQLError` (d) `add_discussion_comment(discussion_id, body)`
- green: D-09에 따라 문서를 web fetch로 재확인하고 출처 URL을 docstring에. 문서 확인 실패 시 `[막힘]`
- gate: `make check`
- design: §7.3, §8.4

### P2.4 Webhooks
- depends_on: P1.5
- owned_paths: `github_adapter/webhooks.py`, `tests/github_adapter/test_webhooks.py`, `tests/fixtures/webhooks/*.json`
- red: FastAPI TestClient로 (a) `X-Hub-Signature-256` 불일치/누락 → 401 (b) 6종 픽스처: issues.* → 무시(204), issue_comment.created `/approve|/reject <r>|/changes <t>` → `on_slash_command(SlashCommand)` 훅 호출·이벤트 없음·202, pull_request.opened→`pr.opened`(task_id는 PR 메타 블록), closed(merged)→`pr.merged`, closed(unmerged)→`pr.closed`, pull_request_review.submitted→`pr.review_submitted`, check_suite.completed→`pr.checks_passed|failed`(PR 없으면 무시), push→로그만 204 (c) 미지원 타입 → 204 (d) 같은 `X-GitHub-Delivery` 두 번 → 두 번째 200 `{"status":"duplicate"}`, publish 0 (e) `resolve_project(repo)`가 None → 204
- green: `WebhookHandler(secret, publish, resolve_project, on_slash_command, cache)` + `build_webhook_router(handler)`; `hmac.compare_digest`; `DeliveryCache`(TTL, max)
- gate: `make check`
- design: §7.1, §7.4, §13
- notes: D-13. 실 GitHub의 Plan Discussion은 `discussion_comment` 웹훅을 보낸다 — X.1에서 추가.

### P2.5 DryRun client + 팩토리
- depends_on: P2.2, P2.3
- owned_paths: `github_adapter/dry_run.py`, `github_adapter/__init__.py`, `tests/github_adapter/test_dry_run.py`
- red: (a) `DryRunGitHubClient`가 `GitHubClient` 만족(runtime + mypy) (b) 모든 쓰기 메서드가 structlog `would <method> ...` + 결정적 번호(Issue/PR 같은 번호 공간) (c) 같은 task 두 번 → 같은 번호; PR(head,base), milestone(title), comment(key)도 멱등 (d) respx 활성 상태에서 네트워크 0 (e) `snapshot()`으로 상태 검사 (f) `DryRunDiscussionsClient`(카테고리 Plans/Proposals/Reports) (g) `get_github_client(settings, installation_id=None)` / `get_discussions_client`: dry면 Dry, 아니면 실 client(installation_id 필수)
- green: 메모리 dict 상태, 팩토리는 `__init__.py`
- gate: `make check`
- design: CLAUDE.md DRY_RUN 규칙
- notes: D-10.

### PC-2 어댑터 mock 통과
- 자동: `make check`; `uv run pytest tests/github_adapter -q` 전부 통과; `grep -rn "api.github.com" --include=*.py . | grep -v tests | grep -v .venv` 결과가 `client.py`, `discussions.py`의 base URL 상수뿐
- 자동: `uv run python -c "from github_adapter import get_github_client; from control_plane.config import Settings; print(type(get_github_client(Settings())).__name__)"` → `DryRunGitHubClient`
- 사람: `docs/pc/PC-2.md` (에이전트 작성 가능). 실 GitHub 검증은 하지 않는다 (§8)
- pass: 자동 전부

---

### P3.1 ModelProvider
- depends_on: PC-2
- owned_paths: `agents/llm/**`, `tests/agents/test_llm.py`, `tests/conftest.py`
- red: (a) `ModelProvider.complete(messages, *, system=None, schema=None, model=None, max_tokens=4096) -> Completion` (b) `Completion(text, parsed, tokens_in, tokens_out, model)` (c) `FakeProvider(script=[...])` 순서 반환·`calls` 기록·소진 시 `ScriptExhausted`; `schema` 주면 항목(str JSON/dict/모델)을 파싱, 실패면 `parsed=None` (d) `AnthropicProvider(api_key, transport_handler=)`: `httpx2.MockTransport`로 `/v1/messages` 가로채기 — 평문, `schema` 시 요청 body에 `output_config.format.type == "json_schema"`이고 `tools` 없음, `stop_reason=refusal` → `ProviderRefusal` (e) AST: `agents/llm/anthropic.py` 외 `anthropic` import 없음 (f) `tests/conftest.py`에 `fake_provider` 픽스처 (g) `get_provider(settings, fake=False)` D-23
- green: `base.py`, `fake.py`, `anthropic.py`(`AsyncAnthropic`, `messages.parse(output_format=schema)`, 기본 `claude-opus-5`, thinking 생략), `__init__.py`
- gate: `make check`
- design: §14, §1.3-4
- notes: D-02, D-21, D-23. 코드를 쓰기 전에 `claude-api` 스킬을 로드해 SDK 최신 사용법을 확인한다.

### P3.2 RepoSummary + sample_repo
- depends_on: PC-2
- owned_paths: `control_plane/orchestrator/context.py`, `tests/orchestrator/test_context.py`, `tests/fixtures/sample_repo/**`
- red: `sample_repo` 픽스처(작은 Python 프로젝트: `pyproject.toml`(`pythonpath=["src"]`), `src/app/{__init__,main,models}.py`, `tests/test_main.py`, README, `docs/ARCHITECTURE.md`, `.ai-platform/CONTEXT.md`, 깊이 3 파일과 `.venv`/`node_modules`/`dot_git_stub` 미끼; 자체 pytest 통과, ~100줄)로 (a) `build_summary(path)` → `RepoSummary(root, language, framework, test_runner, tree, readme_head, config_files, docs)` — framework는 **선언된 의존성**으로만 판정(description 문구 무시) (b) 깊이 3은 tree에 없음 (c) 본문은 README·설정·`.ai-platform/*`만, `src/**` 없음 (d) 제외 디렉토리 (e) URL → `NotImplementedError("GitHub tree API")` (f) `render_summary()` 마크다운 `## Repository`로 시작
- green: 로컬 경로 워커, tomllib로 deps 파싱
- gate: `make check`
- design: §5.2, §5.3
- notes: D-11. 픽스처의 `.git`은 진짜 git이 아니라 `dot_git_stub` 디렉토리로 둔다. 이 픽스처는 P4·P5도 쓴다.

### P3.3 프롬프트 + TaskDraft
- depends_on: P3.1
- owned_paths: `control_plane/orchestrator/prompts/**`, `control_plane/orchestrator/drafts.py`, `tests/orchestrator/test_drafts.py`
- red: (a) `TaskDraft(title, spec, kind, role_required, depends_on(제목 참조), owned_paths(비어 있으면 ValidationError), estimated_tier)` (b) `PlanDraft(understanding, acceptance_criteria[], epics[EpicDraft], task_graph, decisions_expected, budget_estimate).to_markdown()`이 §5.2 6섹션 (c) `analyze.md`, `plan.md`, `decompose.md` 상단 `<!-- -->` 주석에 근거 3줄, `render_prompt(name, **vars)`가 주석 제거 + 치환(빠진 변수 KeyError; JSON 예시는 `{{ }}`) (d) `decompose_with_retry(provider, repo_summary=, plan=, goal=)`: 1회차 파싱/검증 실패 → 이전 응답 + 오류를 붙여 재요청 → 2회차 성공; 2회 실패 → `DecomposeError` (e) `DecomposeResult` 검증: 제목 유일, `depends_on`이 존재하는 제목만, 자기 참조 금지
- green: `plan.md`는 마크다운 출력(사람이 읽는 Discussion 본문), `decompose.md`는 JSON 스키마 인라인 + "30분~2시간" + owned_paths 필수
- gate: `make check`
- design: §5.2 Plan 형식, §4.1 Task

### P3.4 그래프
- depends_on: P3.2, P3.3
- owned_paths: `control_plane/orchestrator/graph.py`, `control_plane/orchestrator/state.py`, `tests/orchestrator/test_graph.py`
- red: FakeProvider + `MemorySaver` + DryRun GitHub으로 (a) `analyze_repo → draft_plan → wait_plan_approval`에서 **interrupt**(`__interrupt__` in output, `aget_state().next == ("wait_plan_approval",)`), 상태에 `plan`(§5.2 6섹션 검사, 빠지면 1회 재요청, 또 빠지면 `PlanError`)과 `plan_discussion_number`(dry), 이벤트 `goal.plan_proposed` (b) `Command(resume={"approved": True, "by": …})` → `decompose → emit_issues → END`, `goal.activated`, causation 체인 (c) `resume={"approved": False, "reason": …}` → END, `goal.cancelled`, decompose 호출 없음 (d) 같은 체크포인터로 그래프를 다시 빌드해 resume → `analyze_repo`/`draft_plan` 재실행 없음(provider.calls 수로 확인) (e) `goal.created`는 발행하지 않음(API가 함) (f) `get_checkpointer(settings)`: sqlite URL이면 MemorySaver; `postgres_conn_string("postgresql+asyncpg://…") == "postgresql://…"`, `open_postgres_checkpointer(settings)`는 `AsyncPostgresSaver.from_conn_string`
- green: `OrchestratorState(TypedDict, total=False)`(JSON 직렬화 가능한 값만), `initial_state(...)`, `OrchestratorDeps(provider, github, discussions, publish, emit, model)`, `build_graph(deps, checkpointer=)`. **부작용(Discussion 생성, 이벤트)은 `draft_plan`에**, `wait_plan_approval`은 interrupt만(resume 시 노드가 처음부터 재실행됨). `emit_issues`는 주입된 `deps.emit(state)` 호출(기본은 dry 로그)
- gate: `make check`
- design: §15.1, §3.3
- notes: D-12, D-13. 노드가 상태 스키마에 없는 키를 돌려주면 LangGraph가 거부한다 — P3.5 결과는 `issues`/`error`/`last_event_id`만.

### P3.5 emit_issues
- depends_on: P3.4
- owned_paths: `control_plane/orchestrator/emit.py`, `tests/orchestrator/test_emit.py`
- red: (a) TaskDraft 4개(A→B, A→C, B,C→D) → 위상 정렬(준비된 노드는 입력 순서 유지) 순으로 `task.created` 4건(payload: epic_id, epic_title, title, spec, kind, role_required, depends_on=**task id**, owned_paths, risk_tier, issue_number, issue_url; causation 체인) + dry Issue 4개 + milestone/labels (b) 사이클(A→B→A) → `task.*` 0건, `goal.blocked` 1건(reason에 "cycle"), 결과 `{"issues": [], "error": …}` (c) owned_paths 겹치는 B, C → C.depends_on에 B(반대 방향 의존이 이미 있으면 유지) — `paths_overlap(a, b)`는 고정 접두 경로 포함 관계로 보수적 판정 (d) owned_paths 빈 Task → ValidationError(TaskDraft) (e) 같은 state로 재실행 → `issues` 동일, Issue·이벤트 중복 없음(state["issues"]의 task_id 재사용)
- green: `toposort`, `paths_overlap`, `serialize_overlaps`, `emit(state, *, github, publish) -> {"issues", "last_event_id"} | {"issues": [], "error", "last_event_id"}`
- gate: `make check`
- design: §10.1, §5.2 emit
- notes: D-19, D-20.

### PC-3 그래프 완주 + 눈검사
- 자동: `make check`; `uv run python scripts/pc3_plan_dryrun.py --fake tests/fixtures/sample_repo "goal"` → interrupt → auto-approve → Issue 4개 dry, exit 0
- 자동(실 LLM): `HITL_DRY_RUN=true HITL_ANTHROPIC_API_KEY=... uv run python scripts/pc3_plan_dryrun.py tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests"` → Plan 마크다운 + TaskDraft JSON + `would create_task_issue` 로그 + 토큰 수. **키가 없으면 `[PC-3 결과]`에 pending으로 적고 사용자에게 묻는다** (§0.1-8)
- 사람: (1) Plan §5.2 6섹션 (2) Task ≥ 3, spec만 보고 구현 가능 (3) owned_paths 겹침 없음/직렬화. 2/3 미만이면 P3.3 프롬프트 튜닝 후 재검사. `docs/pc/PC-3.md`
- pass: 자동 전부 + 사람 서명 (조건부 pass 시 실 LLM 검사는 PC-5로 이월)

---

### P4.1 Agent 툴
- depends_on: PC-3
- owned_paths: `agents/tools/**`, `tests/agents/conftest.py`, `tests/agents/tools/**`, `tests/fixtures/make_remote.sh`
- red: **거부 > 허용.** `tests/agents/conftest.py`(P4 공용): `remote`(make_remote.sh → tmp bare), `worktree`(sample_repo 사본 + git init + `.env`/`id_rsa`/`*.pem` 미끼 + origin push), `spy`, `ctx`. `fs.py` — (a) 절대경로 밖/`../` 탈출 → `ToolDenied("outside")` (b) `.env`, `.env.*`, `*.pem`, `id_rsa*`, `.git/config` read → 거부("secret") (c) owned_paths 밖 write → 거부("owned_paths"); secrets는 owned여도 write 거부 (d) 허용 read/write(중간 디렉토리 생성)/list(`.git` 제외) 정상, 없는 파일 → FileNotFoundError. `shell.py` — (e) `ALLOWED_PREFIXES == {pytest, ruff, mypy, npm test, npm run test, make, uv run pytest}`, `pytest -q`가 worktree에서 실제 통과 (f) `rm`, `curl`, `python -c`, `pytest; rm`, `&&`, `|`, `$(…)`, 백틱, `>`, `npm install`, `sudo make`, 앞뒤 공백 → 거부; 셸 없이 `create_subprocess_exec` (g) 타임아웃(1초 테스트) → `ToolTimeout`. `git.py` — (h) `branch(name)` `ai/<epic>/<n>-<slug>` 정규식 아니면 거부, 있으면 checkout (i) `push`가 `main`/`master`/default면 거부 (j) `commit(msg, issue_number=)` 트레일러 `Task #<n> / Run <id>`, 변경 없으면 None (k) bare remote에 push 성공, `changed_files()`, `diff()`. `github.py` — (l) 공개 메서드는 `open_pr`, `comment`뿐; base가 default 아니면 거부. 공통 — (m) 모든 호출이 `run.tool_called`(subject=run), 거부 시 `payload.denied=true, reason`; causation 체인; 비밀값이 payload에 없음
- green: `base.py`(`ToolContext(worktree, owned_paths, run_id, task_id, project_id, goal_id, publish, default_branch, agent_id)`, `resolve/is_owned/record`, `guarded()` 헬퍼, `Tool` Protocol), 파일 하나 = 툴 하나
- gate: `make check`
- design: §5.2, §10.3, §12
- notes: D-01, D-14. `git.push`는 대상 브랜치가 비동기로 정해지므로 `guarded` 대신 직접 `record`.

### P4.2 BaseAgent
- depends_on: P4.1
- owned_paths: `agents/base.py`, `agents/context.py`, `tests/agents/test_base.py`
- red: (a) `AgentInput(task: TaskRef, project_context: ProjectContext, memory, budget: RunBudget, run_id, agent_id)` / `AgentOutput(outcome: done|needs_decision|blocked|failed, artifacts, decision_request, new_tasks, notes_for_memory, summary, tokens_in, tokens_out, cost_usd)` §5.1 (b) `assemble_context(input, token_budget=, system=, related_files=)` 순서 system → policy(빈 문자열) → CONTEXT.md → Role 노트 → Task spec → 관련 요약 → 관련 파일; 예산 초과 시 **뒤에서부터** 비움(예산을 아주 작게 주면 system만 남음 — 테스트 예산은 system 토큰 수 기준으로 계산) (c) CONTEXT.md 없으면 `context.missing_context_md` warning + 계속 (d) `BaseAgent.run(input)`이 `run.started` → `execute` → `run.finished(outcome, tokens_in/out, cost_usd, duration_s, error)`; 예외 → `outcome=failed` (e) `agents/`에 `control_plane.config` import 없음
- green: 토큰 추정 `len/4`(`agents/llm/base.estimate_tokens`)
- gate: `make check`
- design: §5.1, §5.3
- notes: D-23.

### P4.3 Coding Agent
- depends_on: P4.2
- owned_paths: `agents/coding.py`, `tests/agents/test_coding.py`, `tests/fixtures/coding_scripts/*.json`
- red: FakeProvider 스크립트(JSON 파일: 순서대로 plan 텍스트, `EditPlan{files:[{path,content}], message}` dict, …, summary 텍스트) + `worktree` + `remote` 픽스처로 (a) **pass**: `load_context`(CONTEXT.md가 **첫 툴 호출**) → 브랜치 `ai/<epic_slug>/<issue>-<slug(title)>` → `task.started` → plan → edit → commit → `pytest -q` pass → push(bare) → `open_pr`(dry, draft, §7.3 meta) + `pr.opened` → summarize(comment key `summary:<run>`) + `task.completed` → `outcome=done`, artifacts branch/pr/comment (b) **fail→pass**: 1회차 실패 → 2회차 edit 프롬프트에 테스트 출력 포함 → pass; WIP 커밋도 브랜치에 남음 (c) **3회 실패** → `outcome=failed`, `task.failed(attempt=3)`, WIP push됨, PR 없음 (d) **owned_paths 밖 파일** → 쓰기 전에 거부 → `outcome=failed`, `task.failed(reason=scope_violation, files=[…])`, 커밋 0 (e) **의존성 파일**(`is_dependency_file`: pyproject.toml, requirements*.txt, uv.lock, poetry.lock, Pipfile*, package.json, *-lock, go.mod/sum, Cargo.*) diff → `outcome=needs_decision`, Issue 코멘트 "승인 필요"(key `needs-decision:<run>`), `task.blocked(reason=needs_decision)`, `decision_request.type == "dependency"` (f) 이벤트 순서 `run.started` … `run.finished`
- green: LangGraph `load_context → plan_changes → edit(EditPlan 구조화 출력, owned 사전 검사) → check_scope → commit → run_tests → {pass: push → open_pr → summarize, fail∧attempt<max: edit, fail∧attempt≥max: fail(WIP push)}`; `CodingAgent(publish, provider, github, repo, model, test_command, shell_timeout, token_budget)`, `last_state` 노출
- gate: `make check`
- design: §5.2 Coding Agent, §15.1 루프, §9.2, §10.1
- notes: D-16, D-20. 그래프 결과는 dict — `cast(CodingState, raw)`.

### P4.4 Worker
- depends_on: P4.3
- owned_paths: `worker/**`, `docker-compose.yml`, `Makefile`, `tests/worker/**`, `tests/integration/test_worker.py`
- red: (a) `python -m worker <task_id>` / `worker/entrypoint.py`: 환경변수 `WORKER_REPO_URL, WORKER_BRANCH, WORKER_TASK_JSON(TaskRef+ProjectContext 직렬화), WORKER_REDIS_URL, WORKER_TOKEN(옵션, 빈 값), WORKER_TIMEOUT_MIN=45`, 인자 외 설정 없음 — AST 가드가 `control_plane.config` import 잡음 (b) worktree 준비(clone → branch) → `CodingAgent.run` → `run.finished` → exit code(0 done / 1 failed / 2 needs_decision / 3 timeout) (c) 타임아웃(테스트 1초) → `outcome=timeout`, WIP 커밋+push (d) 워커의 publish는 Redis XADD 직접(`events:{project_id}`) — DB 없음; `--publish-file <path>`로 이벤트를 파일에 적는 테스트 모드 (e) integration: `docker build` → sample_repo + bare remote 볼륨 마운트 → 컨테이너가 브랜치 push → exit 0. Docker 없으면 skip
- green: `Dockerfile`(python:3.12-slim + git + node, non-root `worker`), compose에 `worker` 서비스(빌드만), `worker/publish.py`(Redis XADD, `stream_fields` 재사용)
- gate: `make check` && `make test-integration`(Docker 있으면)
- design: §10.3, §15.1
- notes: D-14, D-17. 워커가 XADD한 이벤트는 outbox를 거치지 않으므로 DB `events`에는 projection consumer가 append 한다(P4.5에서 `ingest` 핸들러) — 이 결정은 P4.4 착수 시 `[착수]`에 명시.

### P4.5 Scheduler
- depends_on: P4.4
- owned_paths: `control_plane/scheduler/**`, `tests/scheduler/**`
- red: (a) `task.created` 구독 → `depends_on` 전부 `done`(projection 상태 조회)이고 `status == ready`인 Task만 배정 (b) 위상 정렬, 사이클 → `SchedulerError` (c) owned_paths 겹치는 Task 동시 배정 금지 (`emit.paths_overlap` 재사용) (d) `max_workers` 초과 시 대기 (e) 배정 시 `task.assigned` 발행 후 `WorkerLauncher.launch(task)` — `DockerCliLauncher`(D-15, `docker run … --format json`)와 `FakeLauncher`; 테스트는 Fake (f) `run.finished` 수신 → 슬롯 반환 (g) 워커가 직접 XADD한 이벤트를 DB `events`에 append 하는 `ingest`(멱등, event.id 기준)
- green: `queue.py`, `graph.py`, `launcher.py`, `scheduler.py` 루프
- gate: `make check`
- design: §3.2 Scheduler, §10.1
- notes: D-15, D-20.

### PC-4 브랜치 3개 push
- 자동: `make check`, `make test-integration`
- 자동: `scripts/pc4_run_tasks.py` — PC-3 fake 결과 형태의 Task 3개 → Scheduler + `FakeLauncher`(프로세스 내 `CodingAgent` 실행, FakeProvider 결정적 스크립트) → `tests/fixtures/remote.git`(또는 tmp bare)에 `ai/*` 브랜치 3개, 커밋 트레일러 → `would open_pr` 3건 → `pr.opened` 3건 → `verify_chain` True
- 사람: `git -C <remote> log --all --oneline` 확인, `docs/pc/PC-4.md`
- pass: 자동 전부

---

### P5.1 API
- depends_on: PC-4
- owned_paths: `control_plane/api/**`, `tests/api/**`
- red: httpx AsyncClient로 (a) `POST /projects {name, repo_path|repo_full_name}` → 201 + `project.created`(D-19) (b) `GET /projects/{id}` (c) `POST /projects/{id}/goals` → 202 + `goal.created` (d) `GET /projects/{id}/goals/{gid}` 진행률(Task 상태 카운트) (e) `GET /tasks?status=&epic=` (f) `GET /events?since=&type=` 커서 (g) `Idempotency-Key` 같은 키 재요청 → 동일 응답, publish 1회; 다른 본문 같은 키 → 422 (h) `/health` 유지
- green: 라우터 분리, `deps.py`(session, bus, settings, projection), Idempotency 미들웨어(메모리 dict + TTL)
- gate: `make check`
- design: §13

### P5.2 Goal 실행 + 승인 재개
- depends_on: P5.1
- owned_paths: `control_plane/api/goals.py`, `control_plane/api/approvals.py`, `control_plane/orchestrator/runner.py`, `tests/api/test_goal_flow.py`
- red: (a) `POST /goals` → 백그라운드 `run_orchestrator(goal_id)` → interrupt에서 멈춤, Goal `awaiting_plan_approval` (b) 서명된 `issue_comment` 웹훅(`/approve`, 작성자 = project.members owner) → `Command(resume)` → Task 생성 (c) viewer → 403, resume 안 됨 (d) `/reject reason` → Goal `cancelled` (e) 같은 delivery 두 번 → resume 1회
- green: `runner.py`(thread_id=goal_id, `open_postgres_checkpointer` 또는 MemorySaver), `approvals.py`가 P2.4의 `SlashCommand` 훅을 받아 resume
- gate: `make check`
- design: §3.3, §7.4, §13
- notes: D-13.

### P5.3 WebSocket
- depends_on: P5.1
- owned_paths: `control_plane/api/stream.py`, `tests/api/test_stream.py`
- red: (a) `WS /projects/{id}/stream` 연결 후 publish+relay → JSON 수신 (b) `?since=<event_id>` 접속 시 replay 후 live (c) 다른 project 이벤트 안 옴
- green: connection당 consumer group, 단순 큐
- gate: `make check`
- design: §13 WS

### P5.4 e2e dry-run 스크립트
- depends_on: P5.2
- owned_paths: `scripts/e2e_dry_run.py`, `tests/test_e2e_script.py`
- red: (a) `--fake` → FakeProvider로 전체 흐름 완주(네트워크 0) (b) 출력 섹션: RepoSummary / Plan / (auto-approve) / TaskDrafts / `would create issue` ×N / Task별 Coding Agent 결과 / bare remote 브랜치 목록 / 토큰 합계 (c) `--no-coding`이면 Plan+Issue까지만
- green: 실 provider는 `HITL_ANTHROPIC_API_KEY`. GitHub 항상 Dry. remote는 bare
- gate: `make check` && `uv run python scripts/e2e_dry_run.py --fake tests/fixtures/sample_repo "goal" | tail -5`
- design: §3.3
- notes: D-18.

### P5.5 Runbook
- depends_on: P5.4
- owned_paths: `docs/runbook.md`, `scripts/check_runbook.sh`
- red: 없음 (문서). gate로 대체
- green: 기동 순서(docker-up → migrate → run-api → run-worker), `.env` 키별 설명, e2e 실행법, 흔한 에러 6개(포트 충돌→`*_HOST_PORT`, PEM 개행, respx 미매칭, httpx2 vs respx, aiosqlite vs pg, Docker 없을 때 skip / `newgrp docker`)
- gate: `scripts/check_runbook.sh`가 코드블록 추출·순서 실행 전부 성공

### PC-5 = MVP 1 (dev) 완료
- 자동: `make check`, `make test-integration`, `scripts/e2e_dry_run.py --fake` 완주
- 자동(실 LLM): `scripts/e2e_dry_run.py tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests"` → Task ≥ 3, bare remote 브랜치 ≥ 3, `would open_pr` ≥ 3. (PC-3 조건부 pass였다면 Plan 3항목 검사도 여기서)
- 사람: (1) 브랜치 3개 diff 중 **2개 이상이 사람 수정 없이 머지 가능**한가 (2) 분해 품질 약점 vs 에이전트 자체 평가 (3) 토큰 비용 → MVP 2 예산. `docs/pc/PC-5.md` + `docs/postmortem/mvp1.md`
- pass: 자동 전부 + 사람 (1)

---

## 8. 후속 (이 로드맵 밖, PC-5 이후)

| ID | 내용 | 비고 |
|---|---|---|
| X.1 | **실 GitHub 연결** — GitHub App 체크리스트, `.env` 실값, `sample_repo`를 테스트 repo에 push, `HITL_DRY_RUN=false`로 Goal 1개, `scripts/cleanup_repo.py`, `discussion_comment` 웹훅 매핑 추가 | 설계 §15 MVP 1 DoD의 진짜 판정 |
| X.2 | 프롬프트 튜닝 (Task 크기 30분~2시간) | PC-3/PC-5 결과 기반 |
| X.3 | 설계 변경 절차 예: `awaiting_rebase` | `docs/design.md` diff 먼저 |
| X.4 | MVP 2 진입 — CLAUDE.md "현재 단계" 갱신, Review Agent부터 | 이 파일에 P6~ 추가 |

## 9. 환경 메모 (1차 실행에서 확인, 저장소 밖 사실)

- Docker 소켓은 `root:docker`. 새 셸에 그룹이 반영 안 되면 `echo "<cmd>" | newgrp docker`로 실행 (`sg` 없음, sudo는 비밀번호 필요).
- 호스트 9000/9001은 다른 프로세스가 점유 → `MINIO_HOST_PORT=9100 MINIO_CONSOLE_HOST_PORT=9101 make docker-up`. 5432/6379는 비어 있음.
- 실 LLM 자격 증명(`HITL_ANTHROPIC_API_KEY`/`ANTHROPIC_API_KEY`/`ant auth`)은 사용자가 준다. 없으면 PC-3/PC-5 실 LLM 항목은 `[막힘]`으로 묻는다.
- ruff E501은 한글을 폭 2로 센다. 파이프 뒤 종료 코드는 make의 것이 아니다(§0.3).
- `agents/`·`worker/`에서 `control_plane.events.schema`·`control_plane.store.enums` import는 허용(가드는 `control_plane.config`만 막는다).
