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

완료 = **PC-6 pass + PC-5 pass** (D-40: P6 운영 조립이 먼저, PC-5의 Anthropic 판정은 그 뒤). PC-6는 2026-09-14 pass(조건부). 실 GitHub 검증은 P7/PC-7(X.1).

## 2. Plan 구조

```
P0 스캐폴딩 ─PC-0─► P1 이벤트+Store ─PC-1─► P2 GitHub Adapter(mock) ─PC-2─► P3 Orchestrator ─PC-3─►
P4 Coding Agent+Worker ─PC-4─► P5 API+e2e ─(PC-5 pending, D-40)─► P6 운영 조립 ─PC-6─► PC-5(Anthropic) = MVP 1 (dev) ─► [후속] 실 GitHub 검증
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
| D-07 | (**D-30으로 개정**) projection이 불허 전이 이벤트를 받으면 **nack 후 재시도 N=3**(consumer group pending + XCLAIM 재전달, `Delivery.attempt`). 소진 시 `events.projection_error` 기록 후 ack (스트림이 막히지 않게) | P1.4, P1.5 |
| D-08 | §6.1 전이 테이블은 `control_plane/store/transitions.py` 한 곳. projection과 API 양쪽이 이걸 호출 | P1.2, P1.5 |
| D-09 | GitHub GraphQL Discussions는 `docs.github.com`을 **web fetch로 확인**한 뒤 구현. 확인된 형태: `repository(owner,name){id discussionCategories{nodes{id name slug}} discussions(first,after,categoryId,orderBy){pageInfo nodes{id number title body url category{id name}}}}`, `createDiscussion(input:{repositoryId,categoryId,title,body}){discussion{id number title url}}`, `addDiscussionComment(input:{discussionId,body}){comment{id url}}`. 출처 URL을 docstring에 남긴다 | P2.3 |
| D-10 | DRY_RUN 계층은 `github_adapter/dry_run.py` 하나. `GitHubClient` Protocol을 `DryRunGitHubClient`가 같은 시그니처로 구현, 팩토리 `get_github_client(settings)`가 교체. 테스트는 respx `assert_all_mocked=True, assert_all_called=False` | P2.5 |
| D-11 | `orchestrator/context.py`의 repo 입력은 **로컬 경로**만. URL은 `NotImplementedError("GitHub tree API: MVP1 이후")` | P3.2 |
| D-12 | LangGraph 체크포인터 = Postgres (`langgraph-checkpoint-postgres`, `AsyncPostgresSaver.from_conn_string`은 async context manager라 runner가 연다). 단위 테스트는 `MemorySaver` | P3.4 |
| D-13 | Plan 승인 interrupt 재개는 P5.2에서 서명된 가짜 웹훅(`issue_comment` + `/approve`)을 테스트가 직접 POST | P3.4, P5.2 |
| D-14 | 개발용 git "원격"은 **로컬 bare repo**(`tests/fixtures/make_remote.sh`가 생성). Coding Agent의 push는 여기로 | P4.1, P4.4 |
| D-15 | 워커 기동 = **subprocess로 `docker` CLI** (`--format json` 파싱). 테스트는 `FakeLauncher` | P4.5 |
| D-16 | needs_decision(의존성 파일 변경)은 MVP 1에서 Issue 코멘트 "승인 필요" + `task.blocked`. Decision 엔티티는 MVP 3 | P4.3 |
| D-17 | 워커 컨테이너·Postgres 통합 테스트는 `tests/integration/`, Docker 없으면 `pytest.skip`. `make test`는 항상 제외. (Redis는 D-32: 단위 테스트도 진짜 Redis, skip 없음) | P1.3, P4.4 |
| D-18 | e2e(PC-5)는 **실 Anthropic + DRY_RUN GitHub + 로컬 bare remote**. 실 GitHub은 §8 | P5.4 |
| D-19 | 스키마 동결 전에 `goal.blocked`(goal), `project.created`, `project.updated`(control) 포함. `emergency_stop`은 `<domain>.<name>` 규칙에 맞춰 `control.emergency_stop` | P1.1 |
| D-20 | Task 상태 이벤트 매핑(설계 §6.1 우선): `task.created`(+`issue_number` 있으면 draft→ready) / `task.assigned`→assigned / `task.started`→running / `task.completed`→**in_review** / `task.failed`→ready(attempt<max) 또는 blocked / `task.blocked`→blocked / `pr.merged`→done. 발행 주체: `task.created`=emit(P3.5), `task.assigned`=Scheduler(P4.5), `task.started/completed/failed/blocked`·`pr.opened`=Coding Agent(P4.3). `task.ready` 이벤트는 만들지 않는다 | P1.5, P3.5, P4.3, P4.5 |
| D-21 | Anthropic SDK 1.x는 `httpx2` 기반 → respx가 못 가로챈다. `/v1/messages` mock은 `httpx2.MockTransport`를 `AnthropicProvider(transport_handler=...)`로 주입. `schema` 강제는 tool_use 대신 **structured outputs**(`messages.parse(output_format=Model)`) — 최신 모델은 `tool_choice: any/tool`이 400. 기본 모델 `claude-opus-5` | P3.1 |
| D-22 | Issue 마커 존재 확인은 search API(인덱스 지연) 대신 `GET /repos/{repo}/issues?labels=ai:task&state=all` 목록 스캔 | P2.2 |
| D-23 | `agents/`·`worker/`는 `control_plane.config` import 금지(AST 가드). `get_provider(settings)`는 `anthropic_api_key`만 가진 구조적 Protocol로 받고, 키가 비면 `fake=True`일 때만 FakeProvider, 아니면 `ProviderConfigError` | P0.1, P3.1 |
| D-24 | DB 갱신 가드(P1.5 (f))는 AST 기반. 허용 예외: `events/outbox.py`의 `update(Event)`(published_at·stream_id 부기)와 `events/chain.py`의 `insert(Event)`/`insert(ToolCall)`(append). 수신자가 `session`류 이름일 때만 검사 | P1.5 |
| D-25 | `correlation_id` 필수: Goal 스코프면 goal_id, Goal 밖(project/policy/budget/agent/control)은 project_id. `causation_id: str \| None`, None=루트(사람·API 명령). 필드 누락은 ValidationError | P1.1, P5.1 |
| D-26 | 서명은 **저장 시점**. `Event.signature: str \| None`, 발행자는 None. `control_plane/events/chain.py`의 `append_signed(session, event)`가 publish·ingest 양쪽의 **유일한** `events` append 경로(AST 가드). 함수 안에서 Postgres면 `pg_advisory_xact_lock(hashtext(project_id))`, sqlite면 모듈 전역 `asyncio.Lock`. 서명은 저장소 무결성이지 발행자 인증이 아님 | P1.1, P1.4, P4.5 |
| D-27 | 추가 타입: `epic.created {goal_id, title, order, milestone_number}`, `epic.activated`, `epic.completed`, `task.cancelled {reason, by, cascade_from}`. emit 순서 `epic.created`×N → `task.created`×M. projection: epic created→pending, activated→active(`active→active` 멱등), completed→done; task.cancelled any→cancelled. 발행자: `epic.activated`=Scheduler(Epic status 조회 + 프로세스 내 memo), `epic.completed`/`goal.completed`=MVP 4(MVP 1은 핸들러만), `task.cancelled`=API | P1.1, P1.5, P3.5, P4.5, P5.1 |
| D-28 | §6.1에 `assigned ──fail──► ready(attempt<max) / blocked` 추가(워커 기동 실패). `run.finished.payload.outcome`=RunOutcome(`success\|failed\|timeout\|cancelled\|escalated`), `agent_outcome`(`done\|needs_decision\|blocked\|failed\|timeout`) 병기. 매핑 done→success, failed→failed, needs_decision/blocked→escalated, timeout→timeout, kill→cancelled. 워커 타임아웃 시 `task.failed {reason:"timeout", attempt}`도 발행 | P1.2, P1.5, P4.2, P4.4, P4.5 |
| D-29 | `events` 테이블: `seq`(append 순번, autoincrement; 커서·replay·verify 순서), `canonical_json TEXT NOT NULL`(서명·검증 대상 텍스트), `payload JSON`(쿼리용 사본), `stream_id TEXT NULL`(부기). `sign(prev, canonical_json: str)`. JSONB 왕복으로 payload 표기가 바뀌어도 체인은 안 깨진다 — Postgres 왕복 통합 테스트 필수(P1.3 (d)) | P1.1, P1.3, P1.4, P5.1 |
| D-30 | D-07 개정: `InvalidTransition` → 버리지 않고 지연 재처리 스트림 `events:<project_id>:retry`(`{event_id, not_before, attempt}`; 5s/30s/5m/30m/2h, 5회) → 소진 시 `projection_error` + error 로그. DB/네트워크 예외는 attempt 미소모, backoff 무한 재시도(스트림 정지). `UnhandledEvent`는 즉시 `projection_error`. 교차 발행자 경쟁인 `pr.merged`는 Task가 in_review가 아니면 `tasks.pr_merged_at`만 기록(전이 없음), `task.completed`가 그 플래그를 보면 `running→in_review→done` 한 번에 | P1.4, P1.5 |
| D-31 | `run.tool_called`는 **감사 체인 밖**: `append_signed`가 이 타입은 `tool_calls` 테이블(id, run_id, project_id, seq, tool, args_digest, duration_ms, ts; 서명·락 없음)에 쓰고 스트림에는 그대로 흘림(Run Viewer·WS). `events`에는 안 들어감. 거부된 호출은 새 타입 `run.tool_denied {tool, reason, args_digest}`로 체인에. 기각 대안: 배치(`run.tools_batch`, 실시간성·타임아웃 유실), 체인 유지(호출당 락, 감사 로그 오염) | P1.1, P1.3, P1.4, P1.5, P4.1 |
| D-32 | **테스트 Redis는 진짜 Redis** (사용자 결정 2026-09-13). fakeredis 제거. `tests/events/conftest.py`의 `redis` 픽스처가 `FOREMAN_TEST_REDIS_URL`(기본 `redis://localhost:6379/15`, 테스트 전용 DB)에 붙고 테스트마다 FLUSHDB. 연결 불가면 skip이 아니라 **fail** — `make test`는 Redis 컨테이너를 요구한다(`docker compose up -d --wait redis`). Postgres 통합 테스트는 D-17대로 skip 유지 | P1.4 이후 전부 |
| D-33 | **LLM provider 선택** (사용자 결정 2026-09-13, 사용자는 D-29로 불렀으나 D-29는 canonical_json이라 D-33으로 기록). `agents/llm/ollama.py` `OllamaCompatProvider` — OpenAI 호환 엔드포인트(`{base_url}/chat/completions`, json_schema response_format, 코드펜스 관용 파싱, 실패 시 `parsed=None`→drafts 재시도 경로). `Settings.llm_provider ∈ {anthropic, openai_compat, fake}` + `llm_base_url`/`llm_model`/`llm_api_key`. 개발·PC-3 1차 검증은 로컬 Ollama(`qwen2.5-coder:7b`, 비-thinking 모델 — thinking 모델은 content가 빈다), **PC-5 최종 판정만 Anthropic**. Anthropic 크레딧이 채워지면 `.env`의 `HITL_LLM_PROVIDER=anthropic`으로 바꾸기만 하면 된다 | P3.1, PC-3, PC-5 |
| D-34 | **PC-4는 로컬 Ollama로 실제 코드 생성** (사용자 결정 2026-09-13 "fake 쓰지마"). P4.x 단위 테스트(Red/Green)는 ROADMAP대로 FakeProvider 결정적 스크립트를 유지하고, PC-4 자동 항목 `scripts/pc4_run_tasks.py`는 `Settings.llm_provider`(openai_compat, qwen2.5-coder:7b)로 Coding Agent가 실제로 편집·테스트·push 한다. Fake 스크립트 경로는 `--fake` 옵션으로만 남긴다 | P4.3, PC-4 |
| D-35 | **PC-4 판정은 7B 로컬 모델 기준 "간단한 수준"** (사용자 결정 2026-09-13). `scripts/pc4_run_tasks.py`의 Task 3개는 새 파일 위주에 import 문·기대값·테스트 구성을 spec에 명시한 것으로 바꾸고 3/3 통과로 `pass (조건부)`. 기존 파일을 크게 고치는 Task(UserStore.update/delete)는 7B에서 2/3 편차라 PC-5(Anthropic)에서 판정한다 — 서비스는 Claude API 위에서 움직인다. PC-4에서 발견한 플랫폼 결함 6건(§6 (기록) PC-4)은 모델과 무관하므로 그대로 수정 유지 | PC-4, PC-5 |
| D-36 | **Dry 자동 머지** (사용자 결정 2026-09-13, 리뷰 A7). `dry_run=true`면 상주 프로세스의 `DryMerger`가 `pr.opened`를 받아 `pr.merged {task_id, pr_number, merged_by: "dry-run"}`(actor `system:dry-merge`, causation=pr.opened)를 발행해 사람 머지를 흉내 낸다. 상태 머신·§6.1·`pick_ready`는 그대로. 실 모드에서는 등록되지 않는다. e2e 스크립트의 자체 자동 머지는 이 컴포넌트로 대체 | P6.3, P5.4 |
| D-37 | **PR 생성은 control plane** (사용자 결정 2026-09-13, 리뷰 A4). 워커(CodingAgent)는 push → `run.artifact_produced {kind: "branch", ref}` → `task.completed {run_id, branch, summary}`까지만 하고 GitHub 쓰기를 하지 않는다. 상주 프로세스의 `PrOpener`가 `task.completed`를 받아 `open_pr`(draft, §7.3 메타) → `pr.opened {task_id, run_id, pr_number, head, base}` + Issue 요약 코멘트. Dry/실 선택(`get_github_client`)은 control plane에서만. 워커에 토큰을 주지 않는 §12 원칙 유지 | P6.7 |
| D-38 | **repo 확보는 control plane `RepoCache`** (리뷰 A8). `project.repo`가 존재하는 로컬 경로면 그대로 쓰고, `owner/name`·URL이면 `HITL_REPO_ROOT/<owner>/<name>`에 `git clone`(있으면 `fetch`). Orchestrator 분석 경로와 Scheduler `LaunchSpec.repo_url`이 같은 경로를 쓴다(A3: 프로젝트 행에서 읽음). **docker 런처는 `HITL_REPO_ROOT`를 컨테이너에 `-v <root>:<root>`로 마운트**해 워커가 그 경로를 clone·push 대상으로 쓴다 — non-bare clone에 `ai/*` 브랜치 push는 허용되므로 Dry에서 동작한다(`main`은 툴이 거부). 실 GitHub URL clone 인증(App 토큰)과 워커 push 토큰은 X.1 | P6.6, P6.1 |
| D-39 | **비용 = 토큰 × 설정 단가** (리뷰 C6). `HITL_LLM_PRICE_IN_PER_MTOK` / `HITL_LLM_PRICE_OUT_PER_MTOK`(USD per 1M tokens, 기본 0 → `cost_usd` 0). 코드에 단가표를 박지 않는다(변동·검증 불가). 워커는 `WORKER_LLM_PRICE_IN/OUT`. PC-5 사람 (3)은 토큰 수 + 설정 단가로 판정 | P6.5 |
| D-40 | **PC-5 판정은 PC-6 이후, Anthropic으로** (검토 2026-09-13). PC-5는 Anthropic 크레딧 부족으로 pending인데 P6는 플랫폼을 돌리기 위한 전제이므로 §0.1의 "PC pass 전 다음 Plan 금지"를 이 한 번 예외로 한다: P6.1은 P5.5에 의존하고, PC-5의 실 LLM 항목·사람 항목은 PC-6 통과 뒤 `HITL_LLM_PROVIDER=anthropic`으로 한 번에 판정한다. MVP 1(dev) 완료 = PC-6 pass + PC-5 pass | P6.1, PC-5, PC-6 |
| D-41 | **워커는 실 GitHub에도 토큰을 받지 않는다** (제안 2026-09-14, X.1). 워커는 지금처럼 `RepoCache` 로컬 clone(마운트)에만 push 하고, control plane의 `PrOpener`가 PR을 열기 전에 그 clone에서 `git push origin <branch>`를 App installation 토큰(`x-access-token`)으로 수행한다. 토큰은 control plane 메모리에만 있고 로그·이벤트·워커 env에 안 나간다(§12 유지). 대안(기각 제안): `WORKER_TOKEN`으로 1시간 토큰을 워커에 전달 — 워커가 신뢰 경계를 넘는다 | P7.2 |
| D-42 | **실 GitHub 검증은 사용자 소유 테스트 repo + App 1개** (X.1). `HITL_DRY_RUN=false`는 `.env`에서만 켜고 기본값은 그대로 true. 실 호출 전 `scripts/github_app_check.py`(읽기 전용)가 권한·설치·웹훅 구독을 확인하고, 끝나면 `scripts/cleanup_repo.py`가 `ai-platform:` 마커가 있는 Issue/PR/Discussion을 닫고 `ai/*` 브랜치를 지운다(마커 없는 것은 절대 건드리지 않음) | P7.1, P7.4 |
| D-43 | **워커 컨테이너는 호스트 uid로 실행** (사용자 결정 2026-09-15, F-5b). `DockerCliLauncher`가 `--user <uid>:<gid>`(control plane 프로세스의 것)와 `HOME=/tmp/worker-home`으로 띄워 마운트된 repo에 push 할 수 있게 한다. Dockerfile의 uid 10001은 기본값으로만 남는다. 대안(기각): repo를 777로 chmod — 호스트 파일 권한을 깨뜨림 | P8.2 |
| D-44 | **죽은 워커는 control plane이 정리** (F-5/F-5c). (1) `BaseAgent.run`은 `execute`가 예외로 끝나거나 outcome이 failed인데 `task.failed`를 아직 안 냈으면 `task.failed{reason: "error", attempt}`를 `run.finished` 앞에 발행 (2) `Runtime` reaper 루프: in_flight run마다 `launcher.is_alive(worker_id)`(docker inspect / in-process Task)와 `timeout_min + 5분`을 확인해 죽었으면 `task.failed{reason: "worker_died"|"timeout", attempt}`(actor system:scheduler) — projection이 ready/blocked로 옮기고 Scheduler 슬롯을 반환 (3) 기동 시 DB의 `assigned/running` Task 중 in_flight에 없는 것(이전 프로세스의 잔재)은 같은 경로로 `worker_died` 처리 | P8.3 |
| D-45 | **같은 repo의 프로젝트는 하나** (F-6). `POST /projects`가 `projects.repo_full_name`(또는 아직 projection 전이면 `events`의 `project.created.payload.repo`)이 같으면 409. 웹훅 라우팅이 유일해진다 | P8.4 |
| D-46 | **쓰기 직후 읽기는 events 테이블로 보강** (F-7). `POST /goals`·`GET /projects/{id}`가 projection 행이 없으면 `events`의 `project.created`로 존재를 확인한다(DB 갱신은 여전히 projection만, 읽기 폴백일 뿐). Goal 진행률 등 나머지는 그대로 projection | P8.4 |
| D-47 | **ingest한 워커 이벤트는 relay가 다시 XADD 하지 않는다** (F-11). `Scheduler.ingest`가 `append_signed` 직후 `stream_id=<워커 메시지 id>, published_at=now`로 표시. 스트림엔 미서명 1건만 남고 서명본은 DB에. WS `?since`·replay는 DB 기준이라 영향 없음 | P8.5 |
| D-48 | **Dry 모드는 원격 clone을 하지 않는다** (F-1) + **run-api 기본 no-reload** (F-4). `RepoCache`는 `dry_run`이거나 토큰이 없으면 로컬 경로가 아닌 repo에 `RepoUnavailable`(네트워크 0). `make run-api`는 `API_RELOAD=1`일 때만 `--reload --reload-dir control_plane`. `repos/`는 `.gitignore` | P8.1, P8.6 |
| D-49 | **projection 오류 분류** (F-3/F-2). `IntegrityError`(FK 등)는 transient가 아니라 `OrderingError`로 → D-30 재시도 5회 후 포기(무한 XAUTOCLAIM 재전달 금지). 프로젝트 행이 없는 이벤트(`project.created` 제외)도 같은 경로. 재시도 큐는 재기동 간 영속(설계) — 상한 5회는 그대로 | P8.1 |
| D-50 | **웹훅 시크릿은 fail-closed** (F-8). 비어 있으면 `WebhookHandler`가 503 `webhook secret not configured`를 돌려주고 `app()`이 경고 로그 | P8.4 |
| D-51 | **Plan 승인은 API로도** (외부 점검 #6, 2026-09-16). `POST /projects/{id}/goals/{gid}/approve` · `/reject {reason}` — `X-User-Id`가 `project.members`의 owner|approver여야 하고(403), 대기 중이 아니면 409. 웹훅(`/approve` 코멘트)과 같은 `GoalRunner.resume` 경로. Dry·로컬에서는 터널 없이 이것으로 승인한다; 실 GitHub에서는 둘 다 가능. **`POST /projects`는 `repo`를 검증**: 존재하는 로컬 경로(절대·상대)·`owner/name`·git URL만(400) | P8.4 |
| D-52 | **데모 콘솔 + 데모 모드** (원티드 챔피언십 제출 규정 2026-09-16: 심사자가 설치·API 키 없이 브라우저로 핵심 기능 체험). `GET /`의 정적 1페이지(`control_plane/api/static/`)는 Mission Control(§11.1, MVP 5)이 **아니라** MVP 1 데모 전용이며 승인은 D-51 API 경로 그대로. `HITL_DEMO_MODE=true`면 익명 방문자는 Goal 생성·승인·거절만, `POST /projects`·cancel은 `X-Admin-Token`(401). 레이트리밋: IP별 POST/분, 프로젝트별 동시 실행 Goal 1, 시간당 N. 호스팅은 사용자 서버 `foreman.antaewoo.com`(nginx → 127.0.0.1:8000, systemd), 실 GitHub 공개 repo `AnTaewoo/foreman_test`, LLM 로컬 Ollama. 기각: Next.js Mission Control 선행(기간 내 불가, MVP 순서 위반) | P9 |
| D-53 | **Plan 본문을 이벤트에** — `goal.plan_proposed.payload.plan_markdown: NotRequired[str]`(additive, D-27/D-51 패턴) → projection → `goals.plan_markdown`(alembic 0003) → `GET …/goals/{gid}.plan_markdown`. 기각: LangGraph 체크포인터 읽기(API 프로세스 내부 상태, "읽기는 projection" 원칙 위반, 거절·완료 후 조회 불안정). 해시 체인이 Plan 본문까지 감사한다(수 KB, 수용) | P9.1 |
| D-54 | **프로젝트 삭제 = 보관** (사용자 요청 2026-09-17). 이벤트는 append-only(트리거)라 행을 지우지 않는다. `DELETE /projects/{id}` → 미완 Goal마다 `goal.cancelled`+`task.cancelled` 캐스케이드 → `project.updated{archived: true, by}`(additive payload) → projection `projects.archived_at`(0004). 목록에서 제외(`?include_archived=true`로 조회), 새 Goal 409, D-45 repo 유일성은 보관된 프로젝트를 세지 않는다(같은 repo 재연결 가능). GitHub Issue/PR/Discussion은 그대로. 데모 모드면 관리 토큰. 복원은 API 없이 `archived: false` 이벤트로 가능(핸들러만) | P9 |
| D-55 | **Task 완료 정의를 인수 테스트로 바꾸는 설계 변경은 제출(2026-09-20) 이후 P10으로** (사용자 결정 2026-09-17, §6 결정 요청의 옵션 A). 제출까지는 현행(산문 spec + 분해 검증 규칙) 유지. 발표 자료의 "추후 개선점"에 넣는다: AC → `tests/test_goal_<id>.py`를 승인 대상에, Task="이 테스트 통과 + 파일들", owned_paths·depends_on은 테스트 import에서 도출, 에이전트는 인수 테스트 수정 불가, Gate = Task 테스트 + 전체 스위트, 머지 단위를 Epic으로. 근거·리스크는 §6 2026-09-17 행 | P10 |
| D-56 | **분해 산출물을 이벤트로 보존** — `goal.decomposed {tasks, changes, raw_tail}`(EventType 추가, 45개; additive, projection noop). 근거: 가장 많이 실패하는 단계(분해)가 가장 관측이 안 됐다(4차 #3 '원인 확인 불가'). 콘솔 이벤트 로그와 `GET /events`에서 모델 원문 꼬리를 본다 | P9 |
| D-57 | **LLM 프로파일을 Goal마다 선택** (사용자 요청 2026-09-17: OpenAI gpt 모델로 시험). 프로파일 `ollama`(HITL_LLM_*, 항상)·`openai`(HITL_OPENAI_API_KEY/MODEL/BASE_URL, OpenAI 호환 provider)·`anthropic`(HITL_ANTHROPIC_*); 키 있는 것만 선택 가능. `GET /llm` 목록(키 제외), `POST /goals {llm}` → `goal.created.llm`(additive) → `goals.llm_profile`(0005). Orchestrator는 `ProfileRouter`(contextvar로 Goal의 프로파일에 위임), 워커는 `LaunchSpec.llm_profile`로 프로파일별 `WORKER_LLM_*` env. 기본은 `HITL_LLM_PROVIDER`에서 유도. `.env` 값은 사용자가 넣는다 | P9 |

---

## 5. 상태 보드

### P0 — 스캐폴딩 (`docs/prompts.md` P0)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P0.1 | 골격 + 툴체인 + `Settings(dry_run=True)` + import 가드 | — | done | 16f90dd |
| P0.2 | 설계 문서 확인 (`docs/design.md` 존재·§ 번호 참조 가능) | P0.1 | done | 5fcebdd |
| P0.3 | 개발 인프라: compose, `.env.example`, Makefile 확장, pre-commit, structlog, `/health` | P0.1 | done | 61ae633 |
| **PC-0** | 스택이 뜬다 | P0.3 | pass | docs/pc/PC-0.md |

### P1 — 이벤트 스키마 + State Store (`docs/prompts.md` P1)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P1.1 | `events/schema.py` — Event, EventType(§4.2 + D-19), 해시 체인 | PC-0 | done | b490bfa |
| P1.2 | `store/models.py` + `store/enums.py` + `store/transitions.py` | PC-0 | done | b1979cd |
| P1.3 | Alembic 초기 마이그레이션 + pg append-only 트리거 + `store/session.py` | P1.2 | done | 02c7dc4 |
| P1.4 | `events/bus.py` + `events/outbox.py` — publish(outbox) / subscribe / replay | P1.1, P1.3 | done | 4c244bd |
| P1.5 | `events/projection.py` — 유일한 DB 갱신 지점 | P1.4 | done | c32ff1f |
| **PC-1** | 이벤트 한 바퀴 + 스키마 동결 | P1.5 | pass | docs/pc/PC-1.md, tag event-schema-v1 |

### P2 — GitHub Adapter, mock only (`docs/prompts.md` P2)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P2.1 | `auth.py` — App JWT → installation token 발급/캐시/갱신 | PC-1 | done | 187bc62 |
| P2.2 | `client.py` + `protocol.py` + `markers.py` — REST 멱등 메서드 7개 + `ensure_labels` | P2.1 | done | 9e15cd3 |
| P2.3 | `discussions.py` — GraphQL create/list/comment | P2.1 | done | b8bc6e7 |
| P2.4 | `webhooks.py` — HMAC 검증 + 6종 이벤트 → 내부 Event 변환 + 슬래시 명령 훅 | P1.5 | done | 726252f |
| P2.5 | `dry_run.py` + `__init__.py` — DryRun client 2개 + 팩토리 | P2.2, P2.3 | done | 2069ef5 |
| **PC-2** | 어댑터 전 메서드 mock 통과, 실 네트워크 0 | P2.4, P2.5 | pass | docs/pc/PC-2.md |

### P3 — Orchestrator (`docs/prompts.md` P3)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P3.1 | `agents/llm/` — ModelProvider, Anthropic 어댑터, FakeProvider | PC-2 | done | 4815b4a |
| P3.2 | `orchestrator/context.py` — RepoSummary (로컬 경로) + `sample_repo` 픽스처 | PC-2 | done | 034f9ff |
| P3.3 | `orchestrator/prompts/*.md` + `drafts.py` (TaskDraft, 검증·재시도) | P3.1 | done | 0ba957a |
| P3.4 | `orchestrator/graph.py` + `state.py` — 5노드 그래프, interrupt, 체크포인터 | P3.2, P3.3 | done | cc4e86b |
| P3.5 | `orchestrator/emit.py` — 위상 정렬, 사이클, owned_paths 직렬화, Issue(dry) 생성 | P3.4 | done | 2057464 |
| **PC-3** | Fake로 그래프 완주 + 실 LLM dry-run 눈검사 | P3.5 | pass | docs/pc/PC-3.md (사용자 서명 2026-09-13, 2번 조건부) |

### P4 — Coding Agent + Worker (`docs/prompts.md` P4)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P4.1 | `agents/tools/` — base / fs / shell / git / github, 차단 규칙 | PC-3 | done | b39eee5 |
| P4.2 | `agents/base.py` + `agents/context.py` — AgentInput/Output, BaseAgent, 컨텍스트 조립 §5.3 | P4.1 | done | 6be7531 |
| P4.3 | `agents/coding.py` — 그래프, owned_paths diff 검사, needs_decision | P4.2 | done | 9d8dd0c |
| P4.4 | `worker/` — Dockerfile, entrypoint, 45분 타임아웃 | P4.3 | done | 1c9a026 |
| P4.5 | `scheduler/` — task.created 구독, ready 판정, 워커 기동 | P4.4 | done | 2ad0f2e |
| **PC-4** | 로컬 bare remote에 브랜치 3개 push | P4.5 | pass (조건부, D-35) | |

### P5 — API + e2e (`docs/prompts.md` P5)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P5.1 | `api/` — 라우터 6개 + Idempotency-Key | PC-4 | done | 83b8ae0 |
| P5.2 | Goal → Orchestrator 백그라운드, `/approve` 웹훅 → resume, 권한 검사 | P5.1 | done | 6742383 |
| P5.3 | `WS /projects/{id}/stream` | P5.1 | done | 5681505 |
| P5.4 | `scripts/e2e_dry_run.py` | P5.2 | done | e3322f6 |
| P5.5 | `docs/runbook.md` | P5.4 | done | aa90ad5 |
| **PC-5 = MVP 1 (dev)** | e2e dry-run 완주 + 품질 서명 | P5.3, P5.5 | pending — §6 블로커(Anthropic 크레딧), D-40: PC-6 이후 판정 | 5ad94a7 |

### P6 — 운영 조립 + as-built 리뷰 반영 (§7 P6, `docs/review/as-built-2026-09-13.md`)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P6.1 | `runtime.py` — 상주 control plane(relay+projection+scheduler+retry), 프로젝트별 repo, launcher 설정 | P5.5 | done | 8309e7e |
| P6.2 | `runner.py` — 승인 대기 복원(startup) | P6.1 | done | 725422e |
| P6.3 | `dry_merge.py` — Dry 자동 머지 (D-36) | P6.1 | done | c6c9e88 |
| P6.4 | `webhooks.py`·`approvals.py` — discussion_comment + Discussion 번호 매칭 | P6.2 | done | bb757c8 |
| P6.5 | `llm/pricing.py` — cost_usd (D-39) | P6.1 | done | abadc51 |
| P6.6 | `repo_cache.py` — repo 확보 (D-38) | P6.1 | done | d70a254 |
| P6.7 | `pr_opener.py` — PR 생성 control plane으로 (D-37) | P6.3, P6.6 | done | d5b1b10 |
| P6.8 | ROADMAP·runbook 정리 (리뷰 C1~C5) | P6.4, P6.5, P6.7 | done | 5f98754 |
| **PC-6** | 상주 프로세스 + API로 Goal→브랜치→done 완주 | P6.8 | pass (조건부, 사용자 2026-09-14: 파이프라인 완주 기준; 'Task 전부 done'은 모델 한계) | ea92dfa |

### P7 — X.1 실 GitHub 연결 (§7 P7)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P7.1 | `scripts/github_app_check.py` — App 인증·설치·권한·웹훅 구독 읽기 전용 점검 + `ping` 웹훅 | PC-6 | done | d215282 |
| P7.2 | `PrOpener`가 토큰으로 브랜치 push 후 PR (D-41), `RepoCache` 토큰 clone/fetch | P7.1 | done | f39fa6c |
| P7.3 | 웹훅 공개 경로(smee/cloudflared) + `discussion_comment` 실 매핑 확인, runbook | P7.1 | done | ad11188 |
| P7.4 | `scripts/seed_test_repo.py` + `scripts/cleanup_repo.py` (D-42) | P7.2 | done | 7175123 |
| **PC-7** | 실 repo에서 Goal 1개: Plan Discussion → 사람 `/approve` → Issue·PR 실제 생성 → 사람 머지 → done, cleanup | P7.3, P7.4 | pass (자동 전부 + PR #7 사람 머지 → done; 사용자 서명·cleanup 대기) | 2b0b107 |

### P8 — 상주 운영 결함 수정 (외부 점검 F-1~F-12, §7 P8)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P8.1 | projection 오류 분류 + Dry 원격 clone 금지 (F-1, F-2, F-3) | PC-7 | done | 5f21ec0 |
| P8.2 | Docker 런처: 프로젝트 repo 개별 마운트 + 호스트 uid (F-5a, F-5b) | P8.1 | done | fd4060d |
| P8.3 | 죽은 워커 정리: task.failed 보장 + reaper (F-5c, F-5) | P8.2 | done | 1a4e5dc |
| P8.4 | API: repo 유일 409, events 폴백, 시크릿 fail-closed (F-6, F-7, F-8) | P8.1 | done | 18416d6 |
| P8.5 | ingest 중복 XADD 제거 (F-11), tool_calls 확인 (F-12) | P8.1 | done | 1897264 |
| P8.6 | run-api no-reload, repos/ gitignore, 문서·환경 불일치 + 온보딩 점검 (F-4, F-10, 온보딩 #1~#8) | P8.3, P8.4, P8.5 | done | 06d97a7 |
| **PC-8** | 외부 점검 절차 재실행: `docker` 런처로 REPO_ROOT 밖 로컬 repo 프로젝트 → Task done, 죽은 워커 복구, 스트림 중복 0 | P8.6 | pass (자동 5/5, 사용자 서명 대기) | docs/pc/PC-8.md |

### P9 — 공개 데모 호스팅 (제출 규정 2026-09-16, §7 P9)

| ID | 제목 | depends_on | status | commit |
|---|---|---|---|---|
| P9.1 | Plan 본문·Goal 목록·Task 링크 API (D-53) | PC-8 | done | fe7097c |
| P9.2 | 데모 콘솔 정적 1페이지 `GET /` (D-52) | P9.1 | done | a930777 |
| P9.3 | 데모 모드 가드 + 레이트리밋 (D-52) | P9.1 | done | 840283c |
| P9.4 | 배포 파일(systemd) + docs/deploy.md | P9.3 | done | fa6c25e |
| P9.5 | 데모 LLM 선정 (gemma4:e4b vs qwen2.5-coder:14b, e2e; 12b는 Ollama 업그레이드 필요) | PC-8 | done (14b 유지) | b878098 |
| P9.6 | 데모 콘텐츠: demo_seed + showcase Goal | P9.3, P9.5 | running (스크립트 fa6c25e, 실행은 배포 후) | |
| P9.7 | Goal·Epic 완료 판정 — Scheduler가 `epic.completed`→`goal.completed` 발행 (§6 2026-09-19) | P9.3 | done | f84116a |
| P9.8 | 공개 App: installation 토큰 풀 + repo별 설치 탐지 | P9.7 | done | d58cb0d |
| P9.9 | 프로젝트별 installation으로 실행 경로(runner·runtime·repo_cache·pr_opener) | P9.8 | done | ab33c73 |
| P9.10 | 연결 API: 설치가 곧 권한, `installation_id` 기록, 점검 경고 강등(빈 repo는 400) | P9.9 | done | b77c3ee |
| P9.11 | Plans 카테고리 없으면 Plan을 Issue로 + 콘솔 2단계(App 설치 → repo 연결) | P9.10 | done (백엔드 e24e42c) | 534bac6 |
| P9.12 | 콘솔 Approve는 승인자일 때만 + demo_up docker 그룹 (§6 2026-09-19 P9.12) | P9.11 | done | e825651 |
| P9.13 | 분해: 모르는 kind/role 매핑 + 비코드 Task 제거 (§6 2026-09-19 P9.13) | P9.12 | done | (this) |
| P9.14 | 콘솔 첫 화면 문구: 부제 제거, 사용 방법 6단계(초보자용), Goal 설명 한 줄, LLM 선택에서 anthropic 숨김, 예시 Goal 2개(README 포함) (§6 2026-09-20 P9.14) | P9.13 | done | be71201 |
| P9.15 | 기본 LLM 프로파일 = openai (키 있을 때), `HITL_LLM_DEFAULT_PROFILE`로 고정 가능 (§6 2026-09-20 P9.15) | P9.14 | done | 7eaee78 |
| P9.16 | 콘솔 문구: 사용 방법 제목 "사용방법 6단계", 데모 모드가 아니면 계정 안내줄 제거 (§6 2026-09-20 P9.16) | P9.15 | done | c1a3ca8 |
| P9.17 | 이벤트 로그의 "프로젝트 전체 보기" 체크박스 제거 (§6 2026-09-20 P9.17) | P9.16 | done | 344eee6 |
| P9.18 | 콘솔: 프로젝트 삭제를 "보관"이라 부르지 않기 + 삭제가 바로 반영(projection 대기) (§6 2026-09-20 P9.18) | P9.17 | done | 64787b2 |
| P9.19 | 사람이 보는 메시지에서 archived/보관 제거 — 409 detail과 취소 사유를 deleted로 (§6 2026-09-20 P9.19) | P9.18 | done | 2b43fdf |
| P9.20 | 콘솔 repo 연결에서 "점검" 단계 제거 — 입력하고 바로 연결 (§6 2026-09-20 P9.20) | P9.19 | done | 147428a |
| **PC-9** | 심사자 워크스루(시크릿 창): showcase 링크, Goal 생성→승인→Issue→PR→머지→done 실시간, 429/401, 재시작 복원, ping | P9.6 | pending (배포 대기 — 사용자 sudo 단계) | docs/pc/PC-9.md |

---

## 6. Blocked / 결정 요청 / 기록 로그

| 일시 | Task | 사유 | 옵션 / 필요한 조치 |
|---|---|---|---|
| 2026-09-20 | (기록) P9.20 연결에서 점검 제거 | 사용자 지시 | "github 연결 부분에서 점검 빼자". 중복이라 안전하다 — `POST /projects`(P9.10)가 이미 서버에서 같은 `run_check`를 돌려 installation을 탐지하고(미설치 400 + 설치 링크), 필수 항목 실패는 400 "repo check failed — …"로, 정식 대소문자는 `report.canonical`로 보정한다. 따라서 콘솔은 repo를 적고 **연결** 하나만 누르면 되고, 실패 이유는 `#connect-error`에 그대로 나온다. 지운 것: `#connect-check-btn`, `<ul id="connect-check">`, `renderCheck()`와 그 핸들러, 죽은 CSS(`ul.check`, `.check li.ok/bad/warn`), 사용 방법 3단계·① ② 안내의 "점검" 문구. `GET /projects/check` API는 남긴다(테스트·디버깅용, 콘솔만 안 쓴다) |
| 2026-09-20 | (기록) P9.19 삭제 용어 통일 | 사용자 결정 | "다시 연결하는 걸 안 하다 보니까 그냥 삭제로 둬. 그대신 github는 삭제 안 된다는 문구는 계속 남겨" — P9.18의 남은 질문(영어 메시지)에 대한 답. 사람이 읽는 문자열 3개를 deleted로: `project is archived`(goals.py 409), `project is already archived`(projects.py 409), `goal.cancelled.reason = "project archived"` → `project deleted`. 이벤트 **필드**는 그대로다 — `project.updated{archived: true}`와 `ProjectOut.archived_at`, `include_archived` 쿼리, DB 컬럼은 유지(스키마 동결 규칙, 값만 바뀐 것은 reason 문자열뿐). README에서 "같은 repo를 다시 연결할 수 있습니다"는 뺀다(쓰지 않는 경로). GitHub Issue/PR/Discussion·이벤트 기록이 남는다는 안내는 콘솔 2곳(위험 구역 설명·confirm)과 README에 그대로 둔다 |
| 2026-09-20 | (기록) P9.18 삭제 문구·즉시 반영 | 사용자 지시 | "삭제 로직을 보관이라 표현하지 말고 그냥 삭제라고 말해", "삭제시 바로 적용되도록 해 — 현재는 새로고침을 해야 적용됨". (1) 콘솔 문구에서 보관 → 삭제(버튼·설명·confirm·진행 라벨). 무엇이 남는지(이벤트 기록·GitHub Issue/PR/Discussion)는 그대로 알린다 — 동작은 D-54 그대로(archived_at, 같은 repo 재연결 가능). `DELETE /projects/{id}`의 영어 메시지("project is already archived")와 설계 문서의 D-54 용어는 그대로 둔다(내부 용어, 기존 테스트 계약). (2) 새로고침이 필요했던 원인: DELETE는 outbox까지고 projection은 비동기(D-46) → 곧바로 `location.reload()`하면 목록에 그대로 보인다. `untilDeleted()`가 `GET /projects`에서 그 id가 빠질 때까지 300ms × 최대 15회 기다린 뒤 reload |
| 2026-09-20 | (기록) P9.17 이벤트 로그 필터 제거 | 사용자 지시 | "이벤트 로그 내 프로젝트 전체보기 지워". `#events-all` 체크박스와 `renderEvents`의 `all` 분기, `onchange` 핸들러를 모두 지운다(잔여 참조 0) → 이벤트 로그는 **선택한 Goal의 이벤트만** 보여준다. 2026-09-18 콘솔 단순화("보이는 게 많을수록 프런트 오류도 는다")와 같은 방향 |
| 2026-09-20 | (기록) P9.16 콘솔 문구 2건 | 사용자 지시 | 라이브 Goal을 돌리며 나온 지시: (1) 사용 방법 상자 제목을 "사용 방법 — 처음이라면 이 순서대로 (6단계)" → **"사용방법 6단계"** (본문 6단계는 그대로 — 사용자가 옵션 중 "제목만"을 골랐다) (2) `#demo-note`의 "이 콘솔의 승인·생성은 계정 "judge"로 기록됩니다" 줄 삭제 — 데모 모드일 때의 한도 안내는 남긴다(데모 모드는 현재 off라 화면에는 아무것도 안 나온다). `<p id="demo-note">`는 데모 모드용으로 유지 |
| 2026-09-20 | (기록) P9.15 기본 LLM 프로파일 | 사용자 결정 | P9.14 보고 뒤 사용자 지시 "LLM 기본값도 openai로 바꿔줘". `default_profile()`은 D-33 `llm_provider`에서만 유도해서 **openai를 가리킬 값이 아예 없었다**(anthropic|openai_compat뿐, openai_compat → ollama) → `.env`만으로는 불가능, 코드 변경이 필요. 결정: (1) openai 키가 있으면 기본 프로파일 = `openai` (2) `llm_default_profile`(`HITL_LLM_DEFAULT_PROFILE`, 기본 빈 값=자동)로 고정 가능 — 쓸 수 없는 값이면 무시하고 자동 규칙으로 (되돌리기를 코드 수정 없이 하려고. `.env`는 사용자 것이라 건드리지 않는다) (3) 키가 없으면 기존 규칙 그대로(openai_compat → ollama, anthropic 키 있으면 anthropic, 없으면 ollama). 영향: 콘솔 기본 선택, `llm` 없이 만든 Goal, `runtime.profile_env`의 워커 env. 워커 이미지는 재빌드 불필요(`worker/entrypoint.py`는 `WORKER_LLM_*` env만 읽고 `default_profile`을 부르지 않는다) — control plane 재시작만 필요 |
| 2026-09-20 | (기록) P9.14 콘솔 첫 화면 문구 | 사용자 지시 | 심사자(처음 오는 사람)가 읽고 그대로 따라 하도록 첫 화면 문구를 고친다. 지시 6가지: (1) h1의 부제 "HITL Multi-Agent Dev Platform" 제거(데모 배지는 유지) (2) "사용 방법"을 3단계 → **6단계**로, repo 준비·App 설치·연결까지 포함해 쉬운 말로 (3) 맨 위 설명(`p.lead`) 한 줄로 간결하게 (4) LLM 선택에서 `anthropic` 제거 (5) "새 Goal" placeholder에서 "(영어 권장)" 삭제 (6) 예시 Goal을 2개로 줄이고 둘 다 README.md 작성 문구 포함. (4)는 콘솔 선택지에서만 숨긴다 — `agents/llm`의 PROFILES·`/llm` 응답·`POST /goals` 검증은 그대로(D-57 유지, 서버 설정으로 다시 쓸 수 있게). (6)의 README Task는 P9.13(모르는 kind → feature/coding 매핑) 덕에 코드 Task로 살아남는다 |
| 2026-09-19 | (기록) P9.13 분해 kind 정규화 + 비코드 Task 제거 | 사용자 결정 | 라이브 Goal …A05F06("README 작성"): Plan은 Task 1개인데 분해가 research(모든 파일 소유) → README 작성 → `kind:"review"` 최종 검토 3개로 쪼갰고, 스키마에 없는 "review"로 2회 모두 검증 실패 → goal.blocked. 결정: (1) 검증 전에 모르는 kind/role을 매핑(검토·확인류 → research/review, 그 밖 → feature/coding, 대소문자 무시)하고 `changes`에 기록 (2) MVP 1에는 Coding Agent뿐 → role이 research/review/architect인 Task는 버리고(kind만 research인 coding Task는 파일을 바꾸므로 유지) 의존을 재배선(`strip_dependency_tasks`와 같은 방식). 전부 비코드면 버리지 않고 coding/feature로 바꾼다(빈 Goal 방지). 프롬프트는 바꾸지 않는다(프롬프트는 세트로만 조정) |
| 2026-09-19 | (기록) P9.12 2차 계정 리허설 | 버그 수정 | (1) 외부 repo(AnTaewoo2/Calculator_foreman)에서 콘솔 Approve가 403 — `judge`는 멤버가 아니다(결정 3대로). 사용자 결정: 콘솔 사용자가 owner/approver일 때만 버튼, 아니면 "GitHub에서 승인" 안내 + Plan 링크. `ProjectOut.approvers` 추가, API 403 검사는 방어선으로 유지 (2) 같은 프로젝트 Task #3이 `launch_failed` 3회 → blocked: 내 재배포(`demo_up.sh`)가 docker 그룹 없는 셸에서 떠 control plane이 docker.sock 권한 없음. `demo_up.sh`가 그룹이 없으면 `newgrp docker`로 자신을 다시 실행하고 기동 후 `docker version`을 확인. 막힌 Task는 `task.retried`로 되살림 |
| 2026-09-19 | (기록) 운영진 공지 대응 — 공개 App + repo만 입력 (P9.7–P9.11) | 사용자 결정 | 운영진 공지(링크로 바로, 설치·환경 구축·키 발급 없이) + 사용자 방향 "App `foreman_antaewoo`를 public으로, 심사자는 자기 repo에 설치하고 웹에서 repo만 입력". App ID·키·웹훅 secret은 App 단위라 서버 env 그대로, installation id만 repo별 → `GET /repos/{o}/{r}/installation`으로 자동 탐지. **D-42 확장**: App 1개, installation 여러 개. 결정: (1) 완료 판정은 아래 결정 요청의 옵션 (a) — 단 기준은 "Goal의 Task가 전부 done/cancelled이고 done ≥ 1"(Task 없는 Epic이 Goal 완료를 막지 않게), done Task가 있는 Epic마다 `epic.completed`를 먼저 (2) 서버 installation이 아닌 repo의 `POST /projects`는 **설치가 곧 권한 증명** — admin token 없이 허용, 설치 확인 필수. 삭제·취소 등 나머지 AdminDep 유지 (3) 외부 설치 프로젝트 members는 installation 계정 login=owner만(콘솔 `judge` 없음) → 승인·머지는 GitHub에서 본인 아이디로 (4) Plans 카테고리 없으면 Plan을 Issue로 게시, Issue 댓글 `/approve` (5) 빈 repo는 400 "커밋(README) 먼저" — 서버 초기 커밋은 **취소**(P9.10: Contents API가 빈 repo에서 첫 커밋을 만드는지 GitHub 문서에 없음, CLAUDE.md "외부 API 추측 금지". GitHub repo 생성 화면의 "Add a README file"로 충분). 기각: 심사자가 App을 만들어 env 4개 입력(서버를 직접 띄워야 함), manifest flow·Workspace(일정), 콘솔 머지 버튼·샌드박스 3개(외부 repo는 본인이 머지). 회원 기능 없음(신원 = 웹훅 서명된 GitHub 댓글 작성자). 사용자 작업: App → Make public |
| 2026-09-18 | (결정 요청 → 2026-09-19 옵션 (a)로 확정, 위 행) Goal·Epic 완료 발행자 (인계서 #1, D-27 공백) | 라이브 3건(Foreman_test1·2·3, openai)에서 Task 전부 done인데 Goal·Epic이 `active`로 남는다. `goal.completed`/`epic.completed`는 스키마·projection 핸들러만 있고 발행자가 없다(D-27: MVP 4). 콘솔이 끝나지 않은 것으로 보이고 데모 모드 '동시 Goal 1개' 한도에도 계속 잡힌다. **옵션 (a, 권장)** MVP 1 최소 판정 — Scheduler(이미 `epic.activated` 발행자)가 Epic의 Task가 전부 done/cancelled(그리고 done ≥ 1)이면 `epic.completed`, Epic이 전부 done이면 `goal.completed`. §9.4의 2~5단계(Test/Review Agent, Release Decision)는 MVP 4에서 이 판정 앞단에 넣는다. 스키마 변경 없음, 상태 변경은 projection만. **옵션 (b)** D-27 그대로 보류, 콘솔만 'n/n done'을 완료로 표시(데모 한도는 계속 잡힘). 결정 전 구현하지 않는다 |
| 2026-09-18 | (결정 요청) Plan 반려 = Goal 취소 (인계서 #2) | `POST …/reject {reason}` → `goal.cancelled`. 사유를 반영한 재계획이 없어 의존성 하나를 고치려면 Goal을 새로 만들어야 하고 취소된 Plan Discussion이 repo에 남는다. projection은 이미 plan revision 2(awaiting→planning→awaiting)를 지원한다. **옵션 (a)** reject는 취소로 두고 '수정 요청'(`/changes`, 콘솔 버튼) 경로 추가 — reason을 `draft_plan` 입력에 넣어 같은 Discussion에 revision n+1. 프롬프트 세트 변경이라 e2e로 판단(프롬프트 규칙). **옵션 (b, 제출 전 권장)** 현행 유지, 콘솔 버튼 문구를 '반려(Goal 취소)'로 명확화하고 (a)는 P10 |
| 2026-09-18 | (기록) P9 콘솔 단순화 | 구현 조정 | 사용자 방향: "심플하게 — 세부 정보는 접거나 뺀다"(보이는 게 많을수록 프런트 오류도 는다). 정적 파일 3개만 변경(API 변경 없음). (1) Plan은 `<details id="plan-box">` — 승인 대기(`awaiting_plan_approval`)일 때만 펼치고, `goal.id:status`가 바뀔 때만 open을 건드려 사용자의 접기/펼치기를 존중 (2) 이벤트 로그는 `<details id="events-box">`(기본 접힘) — 접혀 있으면 `renderEvents`가 그리지 않고 `ontoggle`에서 그린다, WS 상태 점은 summary에 남긴다 (3) '위험 구역'은 `<details id="settings">`('프로젝트 설정', 기본 접힘) 안으로 — 프로젝트가 없으면 통째로 숨김 (4) Task 표 6열 → 4열(`#`=Issue 링크 · Task · 상태 · PR): 시도 횟수·Issue 열·소유 파일 줄 제거(Issue/PR에서 본다), Task가 없으면 표(`tasks-box`) 숨김 (5) 제거: `goal-meta`(생성 시각·LLM) 줄, `llm-hint`·`LLM_HINT`(프로파일별 안내), '영어 권장' 안내 줄(placeholder로 흡수), Goal 목록의 시각·LLM 표기(진행 `done/total`과 배지만), 죽은 CSS(`.hint`, `#goal-meta`, `.events-head`). Red bd3e8fc(`tests/api/test_demo_ui.py`: `events-box`/`plan-box`/`settings` 존재, `goal-meta`/`llm-hint`/`<th>시도</th>`/`<th>Issue</th>` 부재; 같은 커밋의 E501 주석 한 줄을 이번에 줄바꿈). 검증: `node --check demo.js`, 제거한 id의 잔여 참조 0, `make check` 544 passed. 브라우저 육안 확인은 하지 않았다(사용자 콘솔 확인 몫) |
| 2026-09-18 | (기록) P9 콘솔 UI 리뷰 반영 | 구현 조정 | 다른 세션의 콘솔 UI/UX 리뷰(수정 11·추가 9·축소 5)를 정적 파일 3개에만 반영(API 변경 없음). '프로젝트 삭제' → 좌측 하단 '위험 구역'의 '프로젝트 보관'(빨간 계열, 데모 모드면 관리 토큰을 그때 묻는다) · 오류는 폼 아래 인라인(`role=alert`) + 닫기 버튼 있는 토스트(`role=status`, 오류는 자동으로 닫히지 않음), 메시지는 `detail`만 · 모든 쓰기 버튼은 진행 중 라벨+disabled(`busy`) · '로컬 LLM 1~3분' 하드코딩 제거, 프로파일별 힌트 · Reject는 사유 입력을 펼친 뒤 '반려 확정 (Goal 취소)' · 프로젝트 전환은 새로고침 없이(`switchProject`, 옛 WS는 재연결하지 않음), `?project=&goal=` 유지 · 이벤트는 기본 선택 Goal만(`correlation_id`), '프로젝트 전체 보기' 토글, Task는 `#번호 제목`으로, `task.failed`는 펼치면 테스트 출력 전체 · Task 행은 ▸ 버튼(펼침 유지) · 상태 배지 한국어 라벨(`STATUS_LABEL`, 원문은 title) · Goal 목록은 button(키보드), `:focus-visible`, label, muted 대비 상향 · 모바일: 선택 시 `scrollIntoView`, 표 가로 스크롤, nav wrap · 진행 단계(`#steps`)와 '누구 차례' 문구, 머지 대기 PR 버튼, 경과 시간, 취소·완료 숨기기 필터, WS 상태 점 + 30초 끊김 배너, 탭 제목 '(승인 대기 n)', 다크 모드(토큰화), favicon · 사용 방법은 `<details>`(첫 방문만 펼침), 예시 Goal 접기, '데모' 표기는 데모 모드일 때만. 발견: `hidden` 속성이 `display:flex`에 덮여 승인/반려 상자가 늘 보였다 → `[hidden]{display:none!important}`. headless Chrome 스크린샷(1360·520px, 다크)으로 확인. 리뷰의 무인증 공개 경고는 UX 밖 — 사용자 결정(데모 모드 재활성화 또는 nginx basic auth) |
| 2026-09-18 | (기록) 인계서 #3·#4 + 스트림 경쟁 | 버그 수정 | 다른 세션의 라이브 테스트 인계서(`~/foreman_issues_2026-09-18.md`). **#3** webhook `resolve_project`가 repo 이름만으로 scalar 조회 → 보관된(옛) 프로젝트로 가서 머지 후 다음 Task가 배정되지 않음(5차 라이브) → 보관되지 않은 것 중 `created_at desc` 1건, 보관된 것뿐이면 None(204). **#4** 사전 점검이 대소문자를 구분 → `run_check`가 `GET /repos`의 정식 `full_name`으로 정규화해 나머지를 점검하고 `CheckReport.canonical`/`RepoCheckOut.canonical`로 돌려준다(콘솔은 그 이름으로 연결). `repo_taken`(D-45)·`resolve_project` 비교는 `owner/name`이면 대소문자 무시(`repo_key`), 로컬 경로는 그대로. `POST /projects`는 GitHub를 호출하지 않는다(종전대로). **게이트 멈춤** — WS 스트림이 `accept()` 뒤에 `$` group을 만들어 그 사이 이벤트를 놓쳤다(부하 때 `test_stream`이 `receive_json`에서 무한 대기, 게이트 600초 초과 2회) → group 생성을 accept 앞으로. **#5·#6**(종료 코드 0, links.db/.gitignore, README 자리표시자, 계획 밖 테스트 파일)은 D-55(P10) 입력으로 기록만 — 프롬프트는 개별로 고치지 않는다 |
| 2026-09-17 | (기록) P9 OpenAI 프로브 | 버그 수정 | 다른 세션의 실호출 프로브 진단(gpt-5.6-luna): (1) `max_tokens` 400 → 프로파일이 토큰 파라미터 이름을 정한다(`openai`=`max_completion_tokens`, ollama/anthropic 종전대로; `OllamaCompatProvider(token_param=, max_tokens=)`, 워커 `WORKER_LLM_TOKEN_PARAM`/`_MAX_TOKENS`) (3) 추론 토큰 예산 → `HITL_OPENAI_MAX_TOKENS` 기본 16384(호출자의 기본 예산만 대체) (4) 파라미터 오류가 Goal을 죽임 → 원격 프로파일은 Goal 생성 전 1콜 프로브(`probe_profile`, 프로세스당 프로파일 1회 캐시, 실패 400, ollama 제외) (2) temperature는 보내지 않음 — 추가 시 openai 프로파일에선 생략(주석). goal VBY6SB는 cancelled 그대로(재개 경로 없음, 새 Goal로) |
| 2026-09-17 | (기록) P9 LLM 프로파일 | 구현 조정 | D-57 구현. 콘솔 Goal 폼에 LLM 선택(마지막 선택 기억), 목록에 프로파일 표시. 워커 이미지: `worker/entrypoint.py`는 변경 없음(기존 WORKER_LLM_* 계약)이지만 `agents/llm/ollama.py`가 바뀌어 재빌드. OpenAI 원격 base_url은 `host_url` 치환 대상이 아니다(localhost만). 실 DB `make migrate`(0005) |
| 2026-09-17 | (기록) P9 5차 전 검토 | 구현 조정 | 검토(다른 세션) 4건: **#1 중** 언급 추론이 방향이 없어 소유자 전부 규칙과 합쳐 3-사이클 → `CycleError` → Goal blocked(재현됨) → 증거를 import 문(`from x import`/`import x`)만 인정, 간선 추가 전 도달성 검사로 사이클이면 버리고 기록(`_add_edge`; 공유 파일 순서 의존도 같은 가드) **#2 경** 기다림 출처가 다섯(모델 depends_on, drafts 2, emit.serialize_overlaps, 스케줄러 겹침) — drafts가 먼저 걸어 `goal.decomposed.changes`에 남기고 emit은 같은 규칙의 안전망으로 유지(제거는 P10) **#3 경** `raw_tail` 1500자로는 부족 → `goal.decomposed.parsed`(정규화 전 Task의 title/kind/owned_paths/depends_on, additive) **#4 경** 디렉토리 이름 후보(`tests/test_utils.py`)가 두 Task에 붙어 공유 파일 생성 → 다른 Task가 가진 후보는 제외. P10 이월: '최소 3 Task' 프롬프트 vs 등급 A('모듈당 하나, 3개 이하') 긴장, scope_expand 회복 경로, emit 직렬화 중복 제거. §16.2 열린 질문 3은 검토자 권고대로 **(a) Epic 통합 브랜치**를 추천안으로 올림(결정은 사용자) |
| 2026-09-17 | (기록) P9 구조 진단 반영 | 구현 조정 | 4차 뒤 구조 분석(다른 세션): 반복은 D-55 옵션 A의 예정된 결과이며 결함 4개 — (1) 산문 정규식 경계 + 쓰기 전 거부(§10.1의 scope_expand 회복 경로 미구현) (2) depends_on·spec 언급·owned_paths 겹침 세 출처의 틈 (3) '잘게 쪼개라' 프롬프트 vs '간선마다 사람 머지' 실행 모델의 모순(§16.2 열린 질문 3: Epic 통합 브랜치/체크 통과 시 자동 머지) (4) 분해 산출물 미보존. 조치: (2)는 4차 수정으로 규칙 단일화(설치 판정 제목 정규식 삭제·`_TEST_TITLE_RE`는 유지, 소유자 전부 의존, 공유 파일 순서 의존); (1)의 "쓰기 전 거부"와 §10.1 scope_expand 회복 경로는 **미구현(P10)**; (4)는 D-56 `goal.decomposed`. 콘솔 예시 Goal을 등급 A(공유 파일 없음, 모듈당 Task 하나)로 교체. (3)은 §16.2 결정 필요 — 옵션: (a) Epic 통합 브랜치 `ai/epic/<n>`에 Task PR을 쌓고 사람은 Epic PR 하나만 머지 (b) 체크 통과 + T0/T1이면 자동 머지 정책 (c) 현행. D-55(P10)와 같이 결정 | 사용자 |
| 2026-09-17 | (기록) P9 4차 라이브 | 구현 조정 | foreman_calculator 4차: 3차 수정 검증(Decisions Expected none, 설치 Task 없음, `src/` 레이아웃 통과), PR #15 도달. 남은 실패는 전부 분해 규칙의 예외 조건. 규칙을 조건 없이 단순화: **#1·#5** import 의존 추론에서 '소유자 유일' 조건 삭제 → 소유자 전부에 의존; 같은 소스 파일을 나눠 가진 Task는 목록 순서로 의존(`serialize_shared_files`) — 뒤 Task가 앞 Task 머지 뒤에 분기 **#2** 설치 Task 제거는 제목 정규식이 아니라 '의존성 파일만 소유'일 때만(진짜 'Set Up Flask Application'을 지우던 회귀 제거) **#4** `Task T1:`·`T1:`·`T-1` 접두를 참조 해석·테스트 제목·정규화 모두에서 인식, 해석된 depends_on 중복 제거 **#3** 분해 성공 시에도 `decompose.result`(Task 목록·정규화 기록·원문 꼬리 1500자) 로그 — 라이브에서 병합이 안 먹은 원인은 다음 실행 로그로 확인 |
| 2026-09-17 | (기록) P9 3차 라이브 | 구현 조정 | foreman_calculator(README-only) 3차: 분해가 Plan의 "Decisions Expected"를 "Install Flask via pip" Task(owned `requirements.txt`)로 내려 41초 만에 needs_decision → blocked, 직렬 의존 5개 전부 대기. **#1** 워커 환경 계약(`ENVIRONMENT_CONTRACT`: Python 3.12, pytest·flask 설치됨, `pytest -q`, 패키지 설치·manifest 변경 불가)을 plan/decompose 프롬프트 `{environment}`로 주입; 분해 정규화에 `strip_dependency_tasks` — 의존성 파일(`requirements*`, `pyproject`, 락 파일 등)을 owned_paths에서 제거하고 설치/셋업 제목 Task는 버리며 의존을 재배선 **#2** "승인 필요" 코멘트가 워커 Dry client에서 나가 GitHub에 없던 것 → PrOpener가 `task.blocked{needs_decision}`에 Issue 코멘트(`needs-decision:<run>`) **#3** 의존이 여럿인 테스트 전용 Task → test_<stem>과 맞는 모듈을 소유한 의존 Task로 병합(없으면 마지막) **#4** 전부 직렬·디렉토리 owned_paths는 프롬프트 품질(미처리). 워커 이미지는 변경 없음(control plane만) |
| 2026-09-17 | (결정됨 → D-55, 옵션 A) Task 완료 정의를 산문 spec에서 실행 가능한 인수 테스트로 | 세 번의 라이브 실패가 거의 전부 Task 경계(owned_paths 밖 쓰기, import 의존 누락, 테스트 전용 Task, 설치 Task, 자리표시자, 옛 base)에서 났고, 분해 검증에 규칙을 덧붙이는 현재 방식(§6 P9 버그 #1~#3차)은 근본 대체가 아니다. 옵션 (A) 제출(09-20)까지는 현행 유지 + 규칙 보강, 제출 후 P10으로 설계 변경 (B) 지금 부분 도입: Plan 단계에서 AC를 `tests/test_goal_<id>.py`로 생성해 승인 대상에 포함(분해·코딩은 그대로) (C) 전면: Task = "이 테스트를 통과시켜라 + 파일들"(owned_paths·depends_on을 테스트의 import에서 도출, 코딩 에이전트는 인수 테스트 수정 불가, Gate = Task 테스트 + 전체 스위트). 영향: 설계 §4.1 Task·§5.2 Plan·§8 승인 흐름 수정 필요(D-55 후보). 리스크: 14b가 쓰는 인수 테스트 자체의 오류(PC-4 기대값 오류 전례) → 스텁 골격 + 사람 승인 필수; UI·모호한 Goal은 테스트로 표현 어려움(경계 밖) | 사용자 |
| 2026-09-17 | (기록) P9 프로젝트 삭제 | 구현 조정 | D-54 구현: `ProjectUpdatedPayload`(archived·by, 전부 NotRequired) 추가·`PAYLOAD_TYPES` 등록(additive), projection `_project_updated`(noop 목록에서 제외), `projects.archived_at`(alembic 0004), `cancel_goal_cascade` 헬퍼로 취소 로직 공유, `GET /projects?include_archived`, `ProjectOut.archived_at`, 콘솔 "프로젝트 삭제" 버튼(확인창). 실 DB `make migrate`(0004) 후 재시작 |
| 2026-09-16 | (기록) P9 버그 #9~#10 + 2차 관찰 | 구현 조정 | 2차 라이브(foreman_test README-only, PR #8·#9 사람 머지 뒤) 리포트. **#9 치명** `RepoCache.fetch`가 `refs/remotes/origin/*`만 갱신해 서버 clone의 로컬 `main`이 첫 커밋에 머물고 워커가 그 경로를 clone → 옛 base에서 분기 → 후속 PR(#10·#11) 전부 충돌. fetch 뒤 `origin/HEAD`(없으면 main/master)로 `checkout -B <default> origin/<default>` — 워킹트리도 전진(Orchestrator가 파일을 읽는다). **#10 보안** clone URL(installation 토큰)이 `.git/config` origin에 남고 그 디렉토리가 워커 컨테이너에 마운트 → 워커가 토큰을 읽을 수 있었다(설계 §12 위반). clone·fetch 뒤 `remote set-url origin <토큰 없는 공개 URL>`; fetch/push는 URL 명시(P7.2 원칙). 서버의 기존 clone 2개는 수동으로 정리·전진. 관찰 (a) 자리표시자 owned_paths(`<path-to-…>`) → 1회 재요청 뒤 제거 (b) app Task가 import 하는 calculator Task에 의존 없음 → spec/제목의 모듈 언급(`x.py`·`from x`·`import x`)으로 의존 추론(역방향 없을 때) (c) 테스트 Task가 구현 파일도 나열해 합치기 규칙을 빠져나감 → kind test·제목 'Write/Add tests'면 합침 (e) `pr.opened` 중복(PrOpener+웹훅) → 웹훅 publish 전 `tasks.pr_number` 확인 (f) pytest exit 5(테스트 0개)를 재시도 프롬프트에 설명. (d) 14b가 EditPlan 대신 원시 코드를 내는 것은 미처리(파서 폴백은 위험). fake e2e 픽스처의 두 번째 Task는 kind test→feature(합침 규칙 때문). 워커 이미지 재빌드 |
| 2026-09-16 | (기록) P9 버그 #2~#8 | 구현 조정 | 다른 세션의 foreman_demo 라이브 테스트 리포트(Goal 2개 모두 진행 불가) 처리. **#3 치명** 빈 repo(pyproject/conftest 없음)에서 `pytest -q`가 루트 모듈 import 실패 → shell 툴이 `PYTHONPATH=worktree`를 주고 `python -m pytest` 접두 허용. **#1 치명** 커밋 0개 repo가 점검 8/8 통과 → `content` 항목(기본 브랜치 존재) 추가, 콘솔 점검 9항목. **#2 치명** depends_on 제목 불일치로 Goal 즉사 → `_resolve_ref`에 정규화 매칭(소문자·구두점·`T-n` 접두 무시 → 유일 접두/포함), `DecomposeError`에 모델 원문 꼬리(`raw:`) 포함 + `decompose.failed` 로그. **#4 중** 편집 반복 3회가 `attempt=3`으로 발행돼 run 1회에 blocked → `task.failed.attempt`는 run 번호(Scheduler 기준), 반복 횟수는 `edit_rounds`(additive) — Task는 run 3회 × 편집 3회까지. **#5 중** 실패 진단 정보 부재 → `task.failed`에 `test_output`(꼬리 2000자)·`branch` 추가(additive), PrOpener가 `tests_failed`면 WIP 브랜치를 GitHub에 push 하고 Issue에 `failure:<run>` 코멘트(테스트 출력), 콘솔 이벤트 로그에 표시. **#6 경** 합쳐져 Task 0개가 된 Epic이 마일스톤으로 생성 → 빈 Epic 제거. **#8 경** 콘솔에 'Goal 취소' 버튼(`POST …/cancel`). **#7 경(미처리)** Plan의 Epic 합계와 Task Graph 불일치, spec에 테스트 이름·기대값 누락 — 14b 프롬프트 이행 문제, X.2 세트 튜닝으로 이월 |
| 2026-09-16 | (기록) P9 버그 #1 | 구현 조정 | 사용자 첫 Goal(foreman_demo, "Add a web calculator…")이 Task 3개 전부 blocked. 이벤트: 9/9 run이 `scope_violation` — 분해가 `main.py`, `server/logic/calculator.py`처럼 소스만 owned_paths로 주고 모델은 매번 `tests/test_server.py` 등을 쓰려 해 툴이 거부. X.2 원칙대로 프롬프트 문구가 아니라 **분해 검증 코드**로 고침(`drafts.py`): (1) 코드 Task가 테스트 경로를 하나도 안 가지면 오류를 붙여 1회 재요청 (2) 그래도 없으면 실패시키지 않고 **결정적으로 보강** — 소유 파일 stem·디렉토리 이름으로 `tests/test_<x>.py` 후보(모델이 실제로 고른 이름: server/routes/logic) (3) 테스트만 소유하고 구현 Task 하나에 의존하는 Task는 그 구현 Task로 합침(패턴 2, depends_on 재배선·spec 보존) (4) 코딩 컨텍스트의 owned_paths 문구에 "tests는 여기 나열된 tests/ 경로에만" 추가, decompose.md에 같은 규칙. 보강된 경로끼리 겹치면 Scheduler가 직렬화한다(실패 아님). 테스트 픽스처 `DECOMPOSE_JSON`의 테스트 전용 Task는 유효한 분해로 교체. 기존 blocked Goal은 그대로(MVP 1엔 재개 경로 없음) — 새 Goal로 재시험 |
| 2026-09-16 | (기록) P9 repo 연결 | 구현 조정 | 사용자 지적: 콘솔에 GitHub 연결 창이 없어 foreman_test에 고정. (1) 콘솔에 "GitHub repo 연결" 폼(`POST /projects`; members = repo owner(owner) + 콘솔 사용자(approver), 데모 모드면 토큰 입력) (2) `GET /projects/check?repo=owner/name` — `github_app_check`와 같은 읽기 전용 점검을 연결 전에 콘솔에서 본다(dry 모드면 D-48 안내). 처음엔 installation 토큰 client를 넘겨 `GET /app` JWT가 덮여 401 → 인증 없는 client로 (3) 점검에 `discussions` 항목 추가(GraphQL `hasDiscussionsEnabled` + 카테고리 `Plans`) — 없으면 Plan 게시가 실패하므로 미리 잡는다 (4) 점검 결과 `foreman_test`는 삭제됨(404), 사용자가 `foreman_demo`를 새로 만듦(8/8 ok, 웹훅 URL도 foreman.antaewoo.com) → DB를 비워(프로젝트 0) 사용자가 콘솔에서 직접 연결하게 둠 |
| 2026-09-16 | (기록) P9 env 원복 | 사용자 결정 | "기존 .env 설정대로": 에이전트가 `.env`에 덧붙인 P9 블록(`HITL_DEMO_MODE`, `HITL_ADMIN_TOKEN`, `HITL_SCHEDULER_MAX_WORKERS`) 삭제. 데모 모드는 기본 꺼짐·선택(`.env`에 넣을 때만), `demo_up.sh`·systemd 유닛은 `.env` + `HITL_DRY_RUN=false`(PC-7 규칙: 실 GitHub는 환경변수로만)만. `demo_seed.py`는 토큰 없이도 동작. **사고**: 19:51:23 KST에 데모 DB `hitl`이 DROP/CREATE 됐다(`base/<oid>/PG_VERSION` 생성 시각, `alembic_version`·앱 테이블·enum 전부 없음, 체크포인트 테이블만 재생성) → 19:57 재시작 시 API·control plane 기동 실패(`relation goals does not exist`), 20:0x `make migrate` + 프로젝트 재시드로 복구. 테스트 스위트는 그룹별 재현으로 무혐의(모든 그룹 뒤 테이블 3/3 유지). 실행 주체 미상 — 사용자에게 확인. 부수 발견: `tests/test_e2e_script.py` 뒤에 `tests/test_repo_cache.py`를 같은 세션에서 돌리면 structlog 스트림이 닫혀 `ValueError: I/O operation on closed file`(순서 의존, 전체 `make check`에서는 안 남) |
| 2026-09-16 | (기록) P9 초기화 | 구현 조정 | 사용자 요청으로 데모를 처음 상태로: `demo_down` → `cleanup_repo --apply`(Issue 6·PR 2·브랜치 2) → DB `hitl` drop/create + Redis 0 FLUSHDB + migrate → clone 캐시 삭제 → `demo_up` → `demo_seed --no-showcase`(프로젝트만, Goal은 사용자가 만든다). 발견·수정: (1) **Plan Discussion 제목이 멱등 키**라 같은 제목의 Goal(같은 예시 버튼)이 이전 Goal의 Discussion을 재사용했다 → 제목에 Goal 짧은 id(`Plan #<rev> (Goal #<id6>): <title>`), Goal마다 유일 (2) uvicorn은 콘솔 탭의 WebSocket이 열려 있으면 SIGTERM 뒤 graceful shutdown에서 끝없이 기다린다(옛 프로세스가 새 프로세스와 함께 남음) → `demo_down.sh`가 5초 뒤 SIGKILL. 남은 Discussions(#2·#3)는 API로 못 지운다(D-42) |
| 2026-09-16 | (기록) P9 배포 | 구현 조정 | 실 배포(foreman.antaewoo.com, nginx → 192.168.0.17:8000)에서 발견·수정: (1) API는 `0.0.0.0:8000` 바인드, `--forwarded-allow-ips 127.0.0.1,192.168.0.0/24` (2) `demo_seed`가 프로젝트 직후 Goal을 만들면 runner가 `projects` 행을 못 봄 → runner도 `project.created` 이벤트 폴백(D-46 확장) (3) `HITL_REPO_ROOT` 기본 `./repos`(상대)가 clone cwd 기준으로 `repos/<owner>/repos/…`에 생김 → `RepoCache`가 root를 resolve (4) runner의 예상 못한 예외가 Goal을 draft에 남겨 데모 한도(진행 중 1개)를 영원히 막음 → `goal.cancelled{reason: runner_error: …}` 발행 (5) sudo 없이 띄우는 `deploy/demo_up.sh`/`demo_down.sh`(systemd 유닛과 같은 env). 취소된 showcase Goal은 콘솔 상단 고정 제외. 27GB Ollama 모델(`sysverify-12431`)은 `ollama stop`으로 내림(심사 기간 재로드 금지 요청) (6) **reaper 경쟁**(D-44 보완): 워커가 `run.finished`를 XADD 하고 `--rm`으로 사라지면, 그 ingest는 consumer 체인(PrOpener의 push·PR 생성 수 초) 뒤에 오는데 reaper(5s)가 먼저 컨테이너 부재를 보고 `task.failed{worker_died}`를 냈다(in_review→ready 불허 → D-30 재시도 큐, 무해하지만 오염). → `REAP_DEAD_GRACE_S=60`: 부재를 처음 본 시각을 `InFlight.dead_since`에 적고 60초 뒤에도 in_flight면 reap. PC-8 항목 5의 복구 시간은 3초 → 약 1분 |
| 2026-09-16 | (기록) P9 | 구현 조정 | 공개 데모(D-52/D-53) 하루 실행. (1) `gemma4:12b`는 이 서버 Ollama 0.20.0이 거부(412, 새 버전 필요) → `gemma4:e4b`와 14b 2회씩 비교, 동률이라 14b 유지(`docs/pc/X-2.md` P9.5) (2) 통합 테스트가 기본 `HITL_DATABASE_URL`(개발 DB `hitl`)을 downgrade base로 비웠다(`-o addopts=""` 전체 실행) → `tests/integration/conftest.py` 기본을 전용 `hitl_test`(없으면 CREATE DATABASE)로. 데모 DB 보호 (3) 데모 가드의 '진행 중' Goal은 draft도 포함(생성 직후 planning 전 상태), awaiting은 제외 (4) 레이트리밋 IP는 X-Forwarded-For를 직접 읽지 않고 scope client(uvicorn `--proxy-headers`) (5) `GET /demo`로 콘솔이 승인자 id·한도를 읽는다(토큰 없음) (6) 데모 콘솔은 외부 CDN 없이 순수 HTML/JS, markdown은 자체 최소 렌더러(escape 후) (7) PC-7·PC-8 잔여 프로세스 종료, 8000 포트 비움. nginx 사이트·인증서·DNS·systemd·App 웹훅 URL·repo public은 사용자 sudo 단계(`docs/deploy.md`) |
| 2026-09-16 | (기록) PC-8 | 구현 조정 | fresh clone + docker 런처 실행(`docs/pc/PC-8.md`)에서 발견: (1) `HITL_REPO_ROOT`가 없으면 docker `-v`가 root 소유로 만들어 이후 API 쪽 clone이 막힘 → `build_launcher`가 먼저 `mkdir -p`(owned 밖 최소 수정 `control_plane/runtime.py`) (2) README 예제의 `jq`를 전제에 추가, `GET /projects` 응답 형태 명시. Task 결과는 14b 모델 품질(owned 밖 쓰기 거부·테스트/구현 분리)로 2 done / 2 blocked / 2 대기 — 플랫폼 동작은 설계대로 |
| 2026-09-16 | (기록) P8.6 | scope_expand + 온보딩 점검 | 사용자가 전달한 fresh-clone 온보딩 점검(`docs/review/onboarding-2026-09-16.md`, 8건+자잘 7건)을 P8.6에서 함께 처리. owned 밖 최소 수정: (1) `control_plane/config.py` `env_ignore_empty=True`(#1 — `cp .env.example .env`만으로 기동), `llm_provider`에서 `fake` 제거(설정값이 아니라 테스트 전용 `get_provider(settings, fake=True)`), 사용처 없는 `minio_*` 필드 삭제 (2) `docker-compose.yml` MinIO `profiles: ["storage"]`(#4) (3) `Makefile` `worker-image` 타깃(#2) (4) `agents/llm/__init__.py` `get_provider(fake=)`, 테스트 4개(`fake` 주입 방식) (5) `.python-version` (6) `README.md` 재작성 — 실제 순서(docker-up → migrate → worker-image → run-control-plane → run-api), 데모 git repo 준비, API 승인(D-51), 읽기 지연·`X-User-Id`·목록 API·Ollama 안내; runbook §0~§3·§5 갱신(`--fake` 블록 제거, 호스트 uid·reaper·이미지 태그·MinIO 프로파일). `scripts/e2e_dry_run.py --fake`는 테스트(`tests/test_e2e_script.py`) 전용으로 남김 |
| 2026-09-13 | (기록) P4.4~P4.5 | 구현 조정 | (1) 워커 LLM 설정은 `WORKER_LLM_PROVIDER/BASE_URL/MODEL/API_KEY`(+fake용 `WORKER_FAKE_SCRIPT`) 환경변수 — 명세의 변수 목록에 추가 (2) 워커의 GitHub 쓰기는 항상 DryRun(워커는 secrets 없음, §12) — 실 GitHub PR 생성은 X.1에서 control plane으로 이동 (3) `projection.handle`은 relay를 거치지 않은 미서명 이벤트(seq 없음)를 건너뛰고 Scheduler.ingest가 append_signed+apply 한다 — 같은 이벤트를 두 번 적용하면 불허 전이 (4) 기동 실패는 task.failed→ready→재배정을 attempt=max까지 반복 후 blocked (5) Dockerfile은 `readme = CLAUDE.md` 때문에 CLAUDE.md도 복사 |
| 2026-09-13 | (기록) PC-4 | 구현 조정 | Ollama(qwen2.5-coder:7b) 실 실행에서 발견·수정 (D-34): (1) shell 툴에 `PYTHONDONTWRITEBYTECODE=1` — pytest가 남긴 `__pycache__`를 `git add -A`가 커밋했다 (2) 편집 프롬프트 관련 파일 = owned 파일 + spec에 언급된 경로 + owned 디렉토리의 형제 파일(`coding.related_files`) — 새 파일만 owned면 모델이 기존 API·import 관례를 못 봐 `store.users`, `from src.app…`을 지어냈다 (3) edit 노드가 CONTEXT.md 없는 원본 input으로 컨텍스트를 조립했다 → plan과 같은 컨텍스트 사용 (4) **Scheduler 슬롯 반환 버그**: `task.failed(attempt<max)`로 재배정한 뒤 이전 run의 `run.finished`가 오면 in_flight를 비워 같은 Task를 세 번 배정(워커 3개 동시 실행, `running→assigned` projection_error). `_task_run` memo로 현재 run이 아닌 종료 이벤트는 무시 (5) **편집 응답 형식을 JSON EditPlan → 파일 블록 텍스트**(`=== FILE: path === … === END FILE ===`, `parse_edit_plan`, JSON은 폴백): 7B 모델이 JSON 문자열 안의 코드에서 `"""` docstring·`@dataclass`·빈 줄을 잃어 기존 테스트를 깨뜨렸다(3회 연속). 블록 형식으로 바꾼 뒤 같은 Task가 1회차에 통과. P4.3 green 문구 "EditPlan 구조화 출력"은 내부 모델은 유지, 와이어 형식만 변경 — Anthropic에서도 같은 형식(PC-5에서 확인) (6) 스크립트는 Redis DB 14 사용(테스트 DB 15의 FLUSHDB와 충돌) |
| 2026-09-13 | (기록) P5.2 | scope_expand | owned 밖 최소 수정: (1) `events/schema.py` `ProjectCreatedPayload.members: NotRequired[...]` — additive 키(동결 규칙 내), `events/projection.py`가 `projects.members`에 저장 (2) `api/projects.py` `members` 입력(기본: 요청자 owner) (3) `api/app.py` `create_app(runner=)` + 웹훅 라우터 + lifespan startup/shutdown, `api/deps.py` `AppState.runner`. 구현 조정: 승인 대상 Goal은 runner의 대기 목록에서 Plan Discussion 번호로 고르고(없으면 유일한 대기 Goal), `/changes`는 MVP 1 미지원 기록. 같은 delivery 두 번은 P2.4 계약대로 200 duplicate(명세의 '204' 아님). 웹훅 본문의 `repository.full_name`으로 Project를 찾으므로 `repo`는 full name, 분석용 로컬 경로는 `GoalRunner(repo_path_for=)`(기본 `repos/<name>`) |
| 2026-09-13 | (기록) P5.3 | 구현 조정 | WS 커서는 명세의 `?since=<event_id>`가 아니라 `?since=<seq>` (D-29, `GET /events`와 동일). 연결당 consumer group은 `$`부터 읽고(bus의 `_ensure_group`은 `0`부터라 먼저 생성) 종료 시 XGROUP DESTROY. replay·live 중복은 event id로 제거. 테스트는 Starlette TestClient(별도 스레드 루프) — 앱이 같은 sqlite 파일·Redis DB에 자기 연결을 연다 |
| 2026-09-13 | (기록) P5.4 | 구현 조정 | e2e 스크립트는 Dry PR을 **자동 머지**(`pr.merged`, actor github:e2e)한다 — MVP 1은 사람이 머지하므로(§8) depends_on이 있는 Task는 선행 Task가 `done`이 될 때까지 배정되지 않아, 자동 머지 없이는 두 번째 Task가 영원히 대기한다. 출력 섹션 번호 1~6(RepoSummary/Plan/(auto-approve)/TaskDrafts + would create issue/Coding Agent 결과/bare remote 브랜치/토큰 합계). Redis DB 14, sqlite 임시 파일. 테스트는 스크립트를 파일 경로로 import 해 `run(argv)`를 직접 호출(sys.modules 등록 필요 — PEP 563 dataclass) |
| 2026-09-13 | (기록) X.2 | 구현 조정 | 사용자 원칙: 프롬프트 튜닝은 개별 문구 손질이 아니라 **입력부터 한 흐름**(RepoSummary → Plan → 분해 → 코딩 → 재시도)으로 세트를 바꾸고 e2e로 판정. 변경: (1) `RepoSummary.symbols` — .py 최상위 class/def 이름 색인(본문 없음)을 요약에 추가(입력 개선; `test_bodies_exclude_source`는 심볼 섹션 이전 구간만 검사하도록 조정) (2) analyze/plan/decompose.md 규칙: 심볼 재사용, T-n Task Graph, ≥3 Task, spec에 테스트 기대값·fresh state, 다른 파일이면 의존 금지, optional Task 금지 (3) `decompose_with_retry(min_tasks=)` 코드 검증 → 재요청; `OrchestratorDeps.min_tasks`(runner 3, e2e 3, --fake·단위 테스트 1) (4) Coding Agent 프롬프트를 `agents/prompts/{system,edit,retry}.md`로(근거 주석), 재시도 프롬프트에 테스트 vs 코드 진단 순서. owned 밖 최소 수정: `tests/api/test_goal_flow.py`(min_tasks=1) |
| 2026-09-13 | (기록) P6.1 | 구현 조정 | (1) projection과 scheduler를 별도 consumer group으로 두면 scheduler가 `task.created`를 projection보다 먼저 받아 `ready`를 못 보는 경쟁 → **한 group `control-plane`에서 delivery마다 projection.handle → scheduler.handle 순으로 체인**(P6.3 DryMerger·P6.7 PrOpener도 이 체인 뒤에 붙인다; 명세의 group 이름은 무시) (2) Scheduler가 `projects.repo_full_name`을 clone 대상으로 쓰므로 e2e/pc4 스크립트의 `project.created.repo`를 bare remote 경로로 바꿈(분석 경로는 `initial_state.repo_path`) — P6.6 RepoCache가 full name→경로를 맡기 전까지의 형태 (3) `DockerCliLauncher` 통합 테스트는 remote 디렉토리 자체를 같은 경로로 마운트한다: pytest tmp의 부모가 0o700이라 컨테이너 uid 10001이 지나갈 수 없음 (4) 워커 LLM env는 `host_url()`로 `localhost → host.docker.internal` 변환 |
| 2026-09-13 | (기록) P6.6 | 구현 조정 | (1) 명세 (e)의 `goal.blocked{reason: repo_unavailable}`는 §6.1에 `draft→blocked`가 없어 projection이 거부한다 → **`goal.cancelled{reason: "repo_unavailable: …", by: "system"}`**로 Goal을 종료(어느 상태에서든 허용). 사용자는 repo를 고친 뒤 Goal을 다시 만든다 (2) `RepoCache.ensure`: 존재하지 않는 **절대 경로**나 `owner/name` 꼴이 아닌 문자열은 clone 시도 없이 `RepoUnavailable` (3) Scheduler는 resolver 실패를 `LaunchError`로 감싸 기존 `task.failed{launch_failed}` 경로를 탄다 (4) 런타임 테스트의 bootstrap repo는 tmp 디렉토리(존재하는 경로) |
| 2026-09-13 | (기록) P6.7 | 구현 조정 | (1) `summarize` 노드는 남겨 LLM 요약만 만들고 `task.completed{run_id, branch, summary}`를 발행, `open_pr` 노드 삭제 (2) `agents/tools/github.py`(GitHubTool)는 남기되 CodingAgent가 쓰지 않는다; 워커 entrypoint의 `github=DryRunGitHubClient()` 인자도 그대로(무해) (3) `run.artifact_produced{kind, ref}` TypedDict를 PAYLOAD_TYPES에 추가(이 타입을 발행하던 코드가 없었으므로 additive) (4) PrOpener는 미서명(워커) 전달에서 바로 PR을 열고 서명본은 memo로 무시; `tasks.pr_number == pr.number`면 재발행 없음 (5) pc4의 `would open_pr` 스냅샷 키는 `project.repo`(remote 경로) |
| 2026-09-13 | (기록) PC-6 | 구현 조정 | (1) 1차 실행에서 후속 Task가 blocked: D-36의 Dry 머지가 `pr.merged` **이벤트만** 흉내 내서 워커가 선행 코드 없는 `main`을 clone 했다 → `DryMerger`가 project repo(`RepoCache` 경로, bare 가능)의 `refs/heads/<base>`를 브랜치로 옮긴 뒤(ff, 아니면 임시 worktree `--no-ff` 머지) `pr.merged`를 발행. 충돌이면 발행하지 않고 PR을 `in_review`에 남긴다(사람 몫). git repo가 아니거나 브랜치가 없으면 `skipped`(이벤트만) (2) API 201/202는 outbox 저장까지라 바로 GET 하면 404 — PC-6 스크립트는 projection 반영을 폴링(§17 3 읽기 지연) (3) 같은 repo 경로로 프로젝트를 두 번 만들면 웹훅 `resolve_project`가 첫 행을 고른다 → 스크립트는 실행마다 고유 workdir; repo 경로 유일성은 X.1 과제 (4) API 재시작 시 `runner.restored_waiting`이 실제로 이전 Goal을 복원했다(P6.2 검증) |
| 2026-09-14 | (기록) P7.2 | scope_expand | `github_adapter/__init__.py`에 `make_token_provider(settings)` 추가(owned 밖 최소 수정). `InstallationTokenProvider.token_nowait()`(동기 경로용, Runtime retry 루프가 매 tick `token()`으로 캐시를 신선하게 유지). `PrOpener(pusher=, repo_path_for=)` — dry면 pusher None. push 실패는 `push_failed` 기록 + memo 해제(서명본 전달에서 재시도). `RepoCache.fetch`는 이제 `url_for(repo)`로 명시 fetch(토큰 URL을 remote 설정에 남기지 않기 위해) |
| 2026-09-14 | PC-7 | 진행 | App 인증·설치·권한 실 API에서 `[ok]`. 남은 설정(사용자): `check_suite` 구독, `foreman_test` Discussions 켜고 Plans 카테고리, smee 채널 URL을 App Webhook URL에. 시드 push 완료(`main` @ 324f637). App이 계정 repo 8개 전부에 설치돼 있어 `foreman_test`만으로 좁히기 권장 | 사용자 |
| 2026-09-15 | (기록) PC-7 | 구현 조정 | (1) API 쪽 `GoalRunner`도 토큰으로 clone(D-41 누락분) (2) `check_suite`는 Checks 권한 없으면 구독 불가 → 선택 (3) App 웹훅 **Active** 체크가 꺼져 있으면 배달 자체가 없고 ping 재전송만 된다 — API로 못 읽는 항목이라 runbook 체크리스트 (4) `pr.opened`가 PrOpener+웹훅으로 PR당 2건 — 무해, 웹훅 쪽 dedupe는 MVP 2 (5) 7b 분해가 owned_paths 오타(`tests_maths.py`)를 내 Task 1개 blocked — 툴 거부 정상 동작 |
| 2026-09-13 | **블로커: Anthropic 크레딧** | PC-5 실 LLM 항목·사람 (1)·X.2 최종 판정이 전부 이 하나에 걸려 있다. 로컬 Ollama(7b/14b) 결과는 `docs/pc/PC-5.md`·`docs/pc/X-2.md`에 기록. 크레딧이 생기면 `.env`에 `HITL_LLM_PROVIDER=anthropic` 추가 후 `scripts/e2e_dry_run.py tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests" --dump <dir>` 1회로 둘 다 판정(D-40: PC-6 이후) | 사용자 |
| 2026-09-13 | (기록) P4.1~P4.3 | 구현 조정 | (1) `run.tool_called`는 체인 밖이므로 `ToolContext.record`가 causation 커서를 옮기지 않는다(다음 이벤트의 causation은 마지막 **체인** 이벤트) — P4.1 테스트 단언 수정 (2) 브랜치 생성은 `CodingAgent.run()` 진입 시 plain git(툴 이벤트 아님) → `task.started → run.started → fs.read CONTEXT.md(첫 툴)` 순서 유지 (3) EditPlan은 전체 파일 덮어쓰기(작은 모델 안정성) (4) needs_decision 시 의존성 파일 변경은 커밋하지 않고 worktree에 남긴다 |
| 2026-09-13 | (기록) P3.1+ | scope_expand + D-33 | scope_expand: `control_plane/config.py`·`.env.example`(P0.3 소유) — `llm_provider`/`llm_base_url`/`llm_model`/`llm_api_key` 추가. `tests/agents/test_llm.py`에 (h) 추가 |
| 2026-09-13 | PC-3 | 막힘 해소: Anthropic 크레딧 부족 → D-33으로 로컬 Ollama(`qwen2.5-coder:7b`) 경로 추가해 실 LLM 항목 완주. 사람 검사 3항목 검토안은 `docs/pc/PC-3.md` (3/3, 2번 조건부) | 사용자 서명 필요. Anthropic 비교는 PC-5. 약점은 §8 X.2 |
| 2026-09-13 | (기록) PC-2 추가 | P0~P2 e2e 통합 테스트 | `tests/integration/test_p0_p2_flow.py`(Postgres+Redis, 앱 라우터+Dry GitHub+서명 웹훅+relay+projection+replay) 추가 — `docs/pc/PC-2.md` 추가 점검 절 |
| 2026-09-13 | (기록) P2 | 구현 조정 | (1) `WebhookHandler(resolve_goal=, bot_login=)` 선택 인자 — correlation goal 해석, 앱 봇만 무시(github-actions는 처리) (2) `check_suite` 이벤트는 task_id 없음(PR 본문 부재) (3) P2.3 문서 확인은 공개 SDL 파일로 (reference 페이지는 fetch 시 색인만) — `docs/pc/PC-2.md` |
| 2026-09-13 | (기록) PC-1 | 발견·조치 | run.tool_called이 relay보다 먼저 도착(D-31 직접 XADD) → Run 미존재 시 건너뛰고 run.started/finished가 재계산, 체인 밖 이벤트는 retry 대상 아님. `tool_call_count`는 replay로 복원 안 됨(설계). 상세 `docs/pc/PC-1.md` |
| 2026-09-13 | (기록) P1.5 | scope_expand + 구현 조정 | scope_expand: `control_plane/events/schema.py`·`tests/events/test_schema.py` — `required_payload_keys`가 PEP 563(문자열 어노테이션) 아래에서 `NotRequired`를 못 보던 버그 수정 + 회귀 테스트(스키마 필드 변경 없음). 조정: (1) 엔티티 미존재(선행 이벤트 미도착)는 `OrderingError`로 `InvalidTransition`과 같은 retry 경로 (2) `run.tool_called` projection은 `tool_calls` 행 수 재계산(멱등) (3) `epic.created`/`task.created`/`run.started`는 force replay를 위해 upsert |
| 2026-09-13 | (기록) P1.4 | scope_expand + 구현 조정 | scope_expand: `pyproject.toml`(fakeredis 제거)·`uv.lock` — 사용자 결정 D-32. 조정: (1) sqlite 경로 락은 루프별 `WeakKeyDictionary` + 세션 재진입 통과 + commit/rollback 훅 해제 (2) `run.tool_called`는 publish가 outbox 없이 바로 XADD(D-31, 감사 대상 아님) (3) `subscribe`는 `poll_once` 반복, XAUTOCLAIM attempt는 XPENDING `times_delivered` |
| 2026-09-13 | (기록) P1.2 | 구현 조정 2건 | (1) `any→cancelled`에서 `done`/`cancelled` 출발은 제외(머지된 Task 취소 불가, 자기 전이 무의미) — Goal도 동일 (2) `events.seq`/`tool_calls.seq`는 unique 컬럼이 아니라 **PK**(sqlite autoincrement는 INTEGER PK만). ULID `id`는 unique. D-29 취지 유지 |
| 2026-09-13 | (기록) P1.1 전 | 스키마 동결 전 점검에서 확정한 구현 메모 (D-25~D-31 외) | A4 publish·ingest 모두 `append_signed` 경유(락 포함) / A7 §4.2 표의 행은 그룹, 프리픽스가 도메인 / B4 `task.retried`→blocked→ready 매핑(MVP 1 발행자 없음) / B5 `goal.activated`는 resume 직후 / B6 plan_proposed는 awaiting에서도(revision) / B7 `pr.closed`는 noop, in_review 탈출은 MVP 2 / B10 웹훅 sender가 앱 봇이면 무시(P2.4) / B11 Scheduler read-your-writes memo / C 값 집합: actor.id 규약, subject.pr id=PR 번호, 엔티티 id는 발행자 ULID, `append_signed`가 payload 필수 키 검사 |

---

## 7. Task 상세

### P0.1 골격 + 툴체인 + Settings + import 가드
- depends_on: —
- owned_paths: `pyproject.toml`, `Makefile`, `.gitignore`, `control_plane/**/__init__.py`, `agents/**/__init__.py`, `github_adapter/__init__.py`, `worker/__init__.py`, `control_plane/config.py`, `.ai-platform/**`, `tests/__init__.py`, `tests/test_scaffold.py`
- red: `tests/test_scaffold.py` — (a) §15.1 패키지 전부 import 가능 + 한 줄 docstring (`control_plane.{api,orchestrator,scheduler,events,store}`, `agents`, `agents.tools`, `agents.llm`, `github_adapter`, `worker`) (b) `Settings(_env_file=None).dry_run is True`, `HITL_` 프리픽스, 비밀값은 `SecretStr`이고 repr에 안 나옴 (c) AST: `agents/`, `worker/` 아래 `control_plane.config` import 없음 (d) ruff `banned-api`에 `anthropic`, `per-file-ignores`에 `agents/llm/anthropic.py`=TID251 (e) `.ai-platform/autonomy.yaml`, `CONTEXT.md` 샘플 존재
- green: uv `pyproject.toml`(deps: fastapi, uvicorn[standard], langgraph, langgraph-checkpoint-postgres, langchain-core, sqlalchemy[asyncio], asyncpg, alembic, redis, pygithub, httpx, pydantic, pydantic-settings, python-ulid, structlog, pyjwt[crypto], anthropic>=1.0; dev: pytest, pytest-asyncio, respx, ruff, mypy, pre-commit, pyyaml, types-pyyaml, aiosqlite), ruff(line 100, `exclude=["docs",".venv"]`, isort `known-third-party=["alembic"]`), mypy(`control_plane.*` strict), pytest(`testpaths=["tests"]`, `norecursedirs=["fixtures",".venv","node_modules",".git"]`, `asyncio_mode=auto`, `integration` 마커), Makefile `install/lint/typecheck/test/test-integration/check`
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
- red: (a) `EventType` 멤버 전부 `<domain>.<name>`, 설계 §4.2 표(D-19·D-27·D-31 반영, **44개**)와 **하나도 빠짐없이** 일치 — 테스트에 44개 이름을 리터럴로 박아 양방향 비교 (b) `Event` JSON 라운드트립, `id`가 ULID, `ts`는 UTC aware (c) `canonical_json(event)` 결정적 — payload 키 순서 무관, `signature` 제외, `allow_nan=False`; `sign(prev_signature, canonical: str)` (d) `verify_chain([...])` 정상 True, 변조/순서 바꿈/미서명(`signature=None`) → False (e) `correlation_id`, `causation_id` **필드 누락** → ValidationError; `causation_id=None`(루트)과 `signature=None`(저장 전)은 허용; `Actor.type`은 agent|human|system|github, `Subject.entity`는 project|goal|epic|task|run|decision|pr|agent|policy (f) P1 발행 타입 12개(project.created, goal.created, epic.created, task.created, task.assigned, task.started, task.completed, task.failed, run.started, run.tool_called, run.finished, pr.opened)의 payload TypedDict가 `PAYLOAD_TYPES`에 등록; `run.finished`는 `outcome`(RunOutcome 값)·`agent_outcome` 둘 다; `goal.plan_proposed`에 `revision` (g) payload 값은 JSON 원시형만 — NaN/Infinity/Decimal/datetime/set → ValidationError; `UNCHAINED == {EventType.RUN_TOOL_CALLED}`; `required_payload_keys(type)`가 TypedDict 필수 키 집합을 돌려줌
- green: pydantic v2 frozen `Event`, `Actor`, `Subject`, canonical JSON = `json.dumps(model_dump(mode="json", exclude={"signature"}), sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False)` SHA-256(prev ‖ "\n" ‖ canonical). `ts` validator: naive→UTC, aware→UTC 정규화
- gate: `make check`
- design: §4.1 Event, §4.2
- notes: D-04, D-05, D-19, D-25, D-26, D-27, D-28, D-29, D-31. **PC-1 이후 이 파일은 추가만.**

### P1.2 Store 모델 + 전이 테이블
- depends_on: PC-0
- owned_paths: `control_plane/store/models.py`, `control_plane/store/enums.py`, `control_plane/store/transitions.py`, `tests/store/test_models.py`, `tests/store/test_transitions.py`
- red: `test_transitions.py` — (a) §6.1의 **모든** 화살표가 `ALLOWED`에 있고 표에 없는 전이는 없음(리터럴 비교; `ready→ready` 자기 전이, `assigned→ready`, `assigned→blocked`(D-28), `blocked→ready` 포함, `in_review→done`만) (b) `assert_transition`이 불허 시 `InvalidTransition`(메시지에 두 상태 이름) (c) `any → cancelled` (d) `blocked`에서 나가는 전이는 `ready`, `cancelled`뿐 (e) `GOAL_ALLOWED`(draft→planning→awaiting_plan_approval→active→done, awaiting→planning, active/blocked 왕복, any→cancelled), `EPIC_ALLOWED`(pending→active, active→active 멱등, active→done), `DECISION_ALLOWED`(§6.2). `test_models.py` — aiosqlite로 (f) 9개 테이블(§4.1 8개 + `tool_calls`) (g) Project→Goal→Epic→Task→Run 왕복 (h) `Task.status`에 Enum 밖 문자열 → 예외 (i) `events` 행 저장 후 `ts`가 tz-aware UTC로 돌아옴, `seq`가 insert 순으로 증가 (j) `Task.pr_merged_at` nullable
- green: SQLAlchemy 2.x async `DeclarativeBase`, §4.1 필드 전부, JSON은 `JSON().with_variant(JSONB(), "postgresql")`, Enum은 `Enum(cls, values_callable, create_constraint=True, validate_strings=True)`, `UTCDateTime(TypeDecorator)`, `events`에 `seq`(autoincrement, unique) / `canonical_json`(Text, not null) / `stream_id` / `published_at / projected_at / projection_error` 부기 컬럼(D-29), `tool_calls` 테이블(D-31), `tasks.pr_merged_at`(D-30). `enums.py`: TaskStatus, GoalStatus, EpicStatus, RunOutcome, DecisionStatus, DecisionType, AgentStatus, TaskKind, Role, RiskTier. `transitions.py`: PEP 695 제네릭 `assert_transition[S: StrEnum](src, dst, table=None)`
- gate: `make check`
- design: §4.1, §6.1, §6.2
- notes: D-05, D-08, D-28, D-29, D-30, D-31.

### P1.3 Alembic
- depends_on: P1.2
- owned_paths: `alembic.ini`, `alembic/**`, `control_plane/store/session.py`, `tests/store/test_migrations.py`, `tests/integration/**`
- red: (a) aiosqlite로 `upgrade head` → 9 테이블 → `downgrade base` → 0 (b) `compare_metadata` diff == [] (c) `create_engine(settings)` / `create_session_factory` / `get_session()` async context manager (d) integration: Postgres에서 `events` UPDATE(부기 컬럼 외)/DELETE 시 트리거 거부, `published_at`/`stream_id`/`projected_at`/`projection_error` UPDATE는 허용. raw INSERT는 `ts`를 명시할 것 (e) integration **(D-29 필수)**: payload에 `1e-5`, `0.1+0.2`, `2**53+1`, `-0.0`, 한글·이모지·공백 문자열, `{}`, `[]`, `[[1,[2,[3]]]]`, 키 순서 뒤섞인 dict, 긴 문자열을 담은 이벤트 10건을 `append_signed`로 저장 → 새 세션에서 읽기 → `verify_chain` True **그리고** `json.loads(canonical_json) == payload`(sqlite에서는 절대 안 잡히는 케이스)
- green: `env.py` async(이미 실행 중인 루프 안이면 스레드로 격리), `alembic.ini`에 `path_separator = os`, revision 0001(Postgres 공유 enum `role`/`risk_tier`는 `_pg_enums(create)`로 한 번만 생성, `postgresql.ENUM(create_type=False)` variant), revision 0002 트리거(`dialect != postgresql`이면 no-op)
- gate: `make check` && (`docker compose up -d --wait postgres && make test-integration` — Docker 있으면)
- design: §4.1 append-only, §14
- notes: D-05, D-17, D-29, D-31. autogenerate는 `HITL_DATABASE_URL=sqlite+aiosqlite:///tmp.db`로.

### P1.4 Event Bus + Outbox
- depends_on: P1.1, P1.3
- owned_paths: `control_plane/events/chain.py`, `control_plane/events/bus.py`, `control_plane/events/outbox.py`, `tests/events/test_chain.py`, `tests/events/test_bus.py`
- red: 진짜 Redis(D-32, `tests/events/conftest.py` `redis` 픽스처, DB 15 + FLUSHDB) + aiosqlite로. `test_chain.py` — (a) `append_signed(session, event)` → `signature`가 None이었던 이벤트에 같은 project의 직전 행(`seq` 최대) signature로 서명해 insert, `canonical_json`·`seq` 채워짐(세션 넘어서도 체인) (b) 이미 `signature`가 있으면 `ValueError`(발행자 서명 금지) (c) `run.tool_called`는 `tool_calls`에만 insert, `events`에 없음(D-31) (d) `PAYLOAD_TYPES` 필수 키 빠지면 `PayloadError` (e) 동시 `append_signed` 20개(`asyncio.gather`) → `verify_chain` True(락) (f) AST: `control_plane/` 아래에서 `Event`/`ToolCall`을 `session.add`하는 곳은 `chain.py`뿐. `test_bus.py` — (g) `publish(session, event)`는 `append_signed` 호출 후 flush, insert 직후 `published_at IS NULL`, `OutboxRelay.relay_once()` 후 XADD + `published_at`·`stream_id` (h) `_mark_published`를 monkeypatch로 죽이면 XADD만 되고 재실행 시 한 번 더 XADD(at-least-once) (i) `subscribe(group, handler, consumer=, project_id=, block_ms=, reclaim_idle_ms=)` — consumer group 생성, 정상 반환 시 XACK, 예외 시 ack 안 함 → XCLAIM 재전달, `Delivery.attempt` 1,2,3 증가 (j) `replay(session, project_id, since_seq=None)`는 DB `seq` 순, `run.tool_called` 제외 (k) `stream_key("P1") == "events:P1"`, `retry_stream_key("P1") == "events:P1:retry"` (l) `schedule_retry(redis, project_id, event_id, attempt)` → `not_before` = now + {1:5s, 2:30s, 3:5m, 4:30m, 5:2h}[attempt], `due_retries(redis, project_id, now)`가 기한 지난 것만 반환·제거, attempt>5면 `RetryExhausted`
- green: `chain.py`: `append_signed`(pg면 `pg_advisory_xact_lock(hashtext(:pid))`, 아니면 모듈 `asyncio.Lock`; `UNCHAINED`면 `ToolCall` insert), `verify_chain_db(session, project_id)`. `EventBus(redis)`: publish, subscribe(`project_id=None`이면 `SCAN events:*`), replay, schedule_retry/due_retries. `OutboxRelay(session_factory, redis, poll_interval, batch)`: `relay_once`, `run`, `start/stop`. redis-py 결과는 `cast`(decode_responses 전제)
- gate: `make check`
- design: §3.2 Event Bus, §1.3-2
- notes: D-06, D-26, D-29, D-30, D-31. `Handler = Callable[[Delivery], Awaitable[None]]`.

### P1.5 Projection
- depends_on: P1.4
- owned_paths: `control_plane/events/projection.py`, `tests/events/test_projection.py`, `tests/test_projection_guard.py`
- red: (a) `project.created → goal.created → goal.plan_proposed → goal.activated → epic.created → task.created(issue_number) → task.assigned → epic.activated → task.started → run.started → run.tool_called → pr.opened → task.completed → run.finished → pr.merged` 순서 → Goal `draft→…→active`, Epic `pending→active`, Task `ready→assigned→running→in_review→done`, Run outcome/tokens/tool_calls(`tool_calls` 행 수) 반영, `events.projected_at` 채워짐 (b) 같은 event id 두 번 → 동일 (c) `task.completed`가 `task.started`보다 먼저 → `apply`는 `InvalidTransition`; `handle(Delivery)` → `schedule_retry` 호출(attempt 1..5), 6회째 → 정상 반환 + `events.projection_error` 기록; DB 예외(monkeypatch)면 `schedule_retry` 안 부르고 `ProjectionTransient` (D-30) (d) `pr.merged`가 `running`에 오면 전이 없이 `tasks.pr_merged_at`만, 이어서 `task.completed` → `done` (D-30 b) (e) `task.failed` from `assigned`(reason launch_failed) → ready; attempt≥max → blocked (D-28) (f) `task.retried` blocked→ready; `task.cancelled` any→cancelled(+`cascade_from`); `epic.activated` 두 번 → active 유지; `epic.completed` → done; `run.tool_denied` → Run.denied_count+1 (g) `goal.plan_proposed`가 `awaiting_plan_approval`에서 오면(revision 2) awaiting→planning→awaiting (h) `pr.closed`, `decision.*`, `policy.*`, `budget.*` 등 MVP 1 미사용 타입은 `noop` 핸들러가 등록(함수 이름 `noop`) (i) 모든 EventType이 `HANDLERS`에 있고, 빠지면 `UnhandledEvent`(즉시 `projection_error`). `test_projection_guard.py` — (j) AST: `control_plane/` 아래 `projection.py` 외 파일에서 `session.add/add_all/merge/delete(`, `session.execute(update|delete(...))` 없음 (D-24 예외 적용, 수신자 이름이 `session`류일 때만)
- green: `HANDLERS: dict[EventType, Handler]`, `@on(...)` 데코레이터, 모든 전이는 `assert_transition`, `Projection(session_factory, bus, max_attempts=5).apply(event, force=False)` / `.handle(delivery)`, `apply(force=True)`는 replay 재구축용. `run.finished`의 `outcome`은 RunOutcome으로 저장, `agent_outcome`은 Run.agent_outcome 컬럼
- gate: `make check`
- design: §3.2 State Store, §6.1
- notes: D-08, D-20, D-24, D-27, D-28, D-30, D-31. `task.failed`는 payload.attempt로 `attempt_count` 갱신 후 ready/blocked 분기. `goal.plan_proposed`는 draft(또는 awaiting)→planning→awaiting을 한 번에.

### PC-1 이벤트 한 바퀴 + 스키마 동결
- 자동: `make docker-up` → `uv run alembic upgrade head` → `scripts/pc1_roundtrip.py`(이 PC가 소유, `scripts/**`): `project.created` + `goal.created` + `goal.plan_proposed` + `goal.activated` + `epic.created` + `task.created`×3 + Task별 `assigned→started→run.started→run.tool_called×2→completed→run.finished→pr.merged` 발행(`run.tool_called`는 `tool_calls`에만) → relay → projection consumer → Postgres `tasks` 3행 `done` → `verify_chain_db` True → `TRUNCATE tasks, runs, epics, goals, tool_calls CASCADE` → `replay`(seq 순) + `apply(force=True)` → 동일 → `make docker-down`
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
- red: (a) `ModelProvider.complete(messages, *, system=None, schema=None, model=None, max_tokens=4096) -> Completion` (b) `Completion(text, parsed, tokens_in, tokens_out, model)` (c) `FakeProvider(script=[...])` 순서 반환·`calls` 기록·소진 시 `ScriptExhausted`; `schema` 주면 항목(str JSON/dict/모델)을 파싱, 실패면 `parsed=None` (d) `AnthropicProvider(api_key, transport_handler=)`: `httpx2.MockTransport`로 `/v1/messages` 가로채기 — 평문, `schema` 시 요청 body에 `output_config.format.type == "json_schema"`이고 `tools` 없음, `stop_reason=refusal` → `ProviderRefusal` (e) AST: `agents/llm/anthropic.py` 외 `anthropic` import 없음 (f) `tests/conftest.py`에 `fake_provider` 픽스처 (g) `get_provider(settings, fake=False)` D-23 (h) **D-33** `OllamaCompatProvider(base_url=, model=, transport_handler=)`가 `httpx.MockTransport`로 `/chat/completions` 가로채기 — 평문, `schema` 시 `response_format.type == "json_schema"`, 잘못된 JSON(`{oops`) 반환 시 `parsed=None`이고 `decompose_with_retry`가 재요청 경로로 들어감(2회 호출), HTTP 4xx → `ProviderError`; `get_provider`가 `llm_provider`로 선택(anthropic|openai_compat|fake, 그 외 `ProviderConfigError`)
- green: `base.py`, `fake.py`, `anthropic.py`, `ollama.py`(`AsyncAnthropic`, `messages.parse(output_format=schema)`, 기본 `claude-opus-5`, thinking 생략), `__init__.py`
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
- red: FakeProvider + `MemorySaver` + DryRun GitHub으로 (a) `analyze_repo → draft_plan → wait_plan_approval`에서 **interrupt**(`__interrupt__` in output, `aget_state().next == ("wait_plan_approval",)`), 상태에 `plan`(§5.2 6섹션 검사, 빠지면 1회 재요청, 또 빠지면 `PlanError`)과 `plan_discussion_number`(dry), 이벤트 `goal.plan_proposed` (b) `Command(resume={"approved": True, "by": …})` → **`goal.activated` 발행(decompose 전, B5)** → `decompose → emit_issues → END`, 이벤트 순서 `goal.activated → epic.created… → task.created…`, causation 체인 (c) `resume={"approved": False, "reason": …}` → END, `goal.cancelled`, decompose 호출 없음 (d) 같은 체크포인터로 그래프를 다시 빌드해 resume → `analyze_repo`/`draft_plan` 재실행 없음(provider.calls 수로 확인) (e) `goal.created`는 발행하지 않음(API가 함) (f) `get_checkpointer(settings)`: sqlite URL이면 MemorySaver; `postgres_conn_string("postgresql+asyncpg://…") == "postgresql://…"`, `open_postgres_checkpointer(settings)`는 `AsyncPostgresSaver.from_conn_string`
- green: `OrchestratorState(TypedDict, total=False)`(JSON 직렬화 가능한 값만), `initial_state(...)`, `OrchestratorDeps(provider, github, discussions, publish, emit, model)`, `build_graph(deps, checkpointer=)`. **부작용(Discussion 생성, 이벤트)은 `draft_plan`에**, `wait_plan_approval`은 interrupt만(resume 시 노드가 처음부터 재실행됨). `emit_issues`는 주입된 `deps.emit(state)` 호출(기본은 dry 로그)
- gate: `make check`
- design: §15.1, §3.3
- notes: D-12, D-13. 노드가 상태 스키마에 없는 키를 돌려주면 LangGraph가 거부한다 — P3.5 결과는 `issues`/`error`/`last_event_id`만.

### P3.5 emit_issues
- depends_on: P3.4
- owned_paths: `control_plane/orchestrator/emit.py`, `tests/orchestrator/test_emit.py`
- red: (a) TaskDraft 4개(A→B, A→C, B,C→D, Epic 2개) → `epic.created` 2건(milestone_number 포함) 선행 → 위상 정렬(준비된 노드는 입력 순서 유지) 순으로 `task.created` 4건(payload: epic_id, epic_title, title, spec, kind, role_required, depends_on=**task id**, owned_paths, risk_tier, issue_number, issue_url; causation 체인) + dry Issue 4개 + milestone/labels (b) 사이클(A→B→A) → `epic.*`·`task.*` 0건, `goal.blocked` 1건(reason에 "cycle"), 결과 `{"issues": [], "error": …}` (c) owned_paths 겹치는 B, C → C.depends_on에 B(반대 방향 의존이 이미 있으면 유지) — `paths_overlap(a, b)`는 고정 접두 경로 포함 관계로 보수적 판정 (d) owned_paths 빈 Task → ValidationError(TaskDraft) (e) 같은 state로 재실행 → `issues` 동일, Issue·이벤트 중복 없음(state["issues"]의 task_id 재사용)
- green: `toposort`, `paths_overlap`, `serialize_overlaps`, `emit(state, *, github, publish) -> {"issues", "last_event_id"} | {"issues": [], "error", "last_event_id"}`
- gate: `make check`
- design: §10.1, §5.2 emit
- notes: D-19, D-20, D-27.

### PC-3 그래프 완주 + 눈검사
- 자동: `make check`; `uv run python scripts/pc3_plan_dryrun.py --fake tests/fixtures/sample_repo "goal"` → interrupt → auto-approve → Issue 4개 dry, exit 0
- 자동(실 LLM, D-33): `uv run python scripts/pc3_plan_dryrun.py tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests"` — `Settings.llm_provider`가 고른 provider(1차 검증은 로컬 Ollama `openai_compat`, Anthropic은 PC-5) → Plan 마크다운 + TaskDraft JSON + `would create_task_issue` 로그 + 토큰 수. **provider가 없으면 `[PC-3 결과]`에 pending으로 적고 사용자에게 묻는다** (§0.1-8)
- 사람: (1) Plan §5.2 6섹션 (2) Task ≥ 3, spec만 보고 구현 가능 (3) owned_paths 겹침 없음/직렬화. 2/3 미만이면 P3.3 프롬프트 튜닝 후 재검사. `docs/pc/PC-3.md`
- pass: 자동 전부 + 사람 서명 (조건부 pass 시 실 LLM 검사는 PC-5로 이월)

---

### P4.1 Agent 툴
- depends_on: PC-3
- owned_paths: `agents/tools/**`, `tests/agents/conftest.py`, `tests/agents/tools/**`, `tests/fixtures/make_remote.sh`
- red: **거부 > 허용.** `tests/agents/conftest.py`(P4 공용): `remote`(make_remote.sh → tmp bare), `worktree`(sample_repo 사본 + git init + `.env`/`id_rsa`/`*.pem` 미끼 + origin push), `spy`, `ctx`. `fs.py` — (a) 절대경로 밖/`../` 탈출 → `ToolDenied("outside")` (b) `.env`, `.env.*`, `*.pem`, `id_rsa*`, `.git/config` read → 거부("secret") (c) owned_paths 밖 write → 거부("owned_paths"); secrets는 owned여도 write 거부 (d) 허용 read/write(중간 디렉토리 생성)/list(`.git` 제외) 정상, 없는 파일 → FileNotFoundError. `shell.py` — (e) `ALLOWED_PREFIXES == {pytest, ruff, mypy, npm test, npm run test, make, uv run pytest}`, `pytest -q`가 worktree에서 실제 통과 (f) `rm`, `curl`, `python -c`, `pytest; rm`, `&&`, `|`, `$(…)`, 백틱, `>`, `npm install`, `sudo make`, 앞뒤 공백 → 거부; 셸 없이 `create_subprocess_exec` (g) 타임아웃(1초 테스트) → `ToolTimeout`. `git.py` — (h) `branch(name)` `ai/<epic>/<n>-<slug>` 정규식 아니면 거부, 있으면 checkout (i) `push`가 `main`/`master`/default면 거부 (j) `commit(msg, issue_number=)` 트레일러 `Task #<n> / Run <id>`, 변경 없으면 None (k) bare remote에 push 성공, `changed_files()`, `diff()`. `github.py` — (l) 공개 메서드는 `open_pr`, `comment`뿐; base가 default 아니면 거부. 공통 — (m) 허용된 호출은 `run.tool_called`(subject=run, `args_digest`), 거부는 **`run.tool_denied`**(tool, reason, args_digest; D-31); causation 체인; 비밀값·파일 내용이 payload에 없음(digest만)
- green: `base.py`(`ToolContext(worktree, owned_paths, run_id, task_id, project_id, goal_id, publish, default_branch, agent_id)`, `resolve/is_owned/record`, `guarded()` 헬퍼, `Tool` Protocol), 파일 하나 = 툴 하나
- gate: `make check`
- design: §5.2, §10.3, §12
- notes: D-01, D-14, D-31. `git.push`는 대상 브랜치가 비동기로 정해지므로 `guarded` 대신 직접 `record`.

### P4.2 BaseAgent
- depends_on: P4.1
- owned_paths: `agents/base.py`, `agents/context.py`, `tests/agents/test_base.py`
- red: (a) `AgentInput(task: TaskRef, project_context: ProjectContext, memory, budget: RunBudget, run_id, agent_id)` / `AgentOutput(outcome: done|needs_decision|blocked|failed, artifacts, decision_request, new_tasks, notes_for_memory, summary, tokens_in, tokens_out, cost_usd)` §5.1 (b) `assemble_context(input, token_budget=, system=, related_files=)` 순서 system → policy(빈 문자열) → CONTEXT.md → Role 노트 → Task spec → 관련 요약 → 관련 파일; 예산 초과 시 **뒤에서부터** 비움(예산을 아주 작게 주면 system만 남음 — 테스트 예산은 system 토큰 수 기준으로 계산) (c) CONTEXT.md 없으면 `context.missing_context_md` warning + 계속 (d) `BaseAgent.run(input)`이 `run.started` → `execute` → `run.finished(outcome=RunOutcome, agent_outcome, tokens_in/out, cost_usd, duration_s, error)`; 매핑 done→success, failed→failed, needs_decision/blocked→escalated(D-28); 예외 → `agent_outcome=failed, outcome=failed` (e) `agents/`에 `control_plane.config` import 없음
- green: 토큰 추정 `len/4`(`agents/llm/base.estimate_tokens`)
- gate: `make check`
- design: §5.1, §5.3
- notes: D-23, D-28.

### P4.3 Coding Agent
- depends_on: P4.2
- owned_paths: `agents/coding.py`, `tests/agents/test_coding.py`, `tests/fixtures/coding_scripts/*.json`
- red: FakeProvider 스크립트(JSON 파일: 순서대로 plan 텍스트, `EditPlan{files:[{path,content}], message}` dict, …, summary 텍스트) + `worktree` + `remote` 픽스처로 (a) **pass**: 브랜치 `ai/<epic_slug>/<issue>-<slug(title)>` → **`task.started {run_id}` → `run.started`**(이 순서, C절) → `load_context`(CONTEXT.md가 **첫 툴 호출**) → plan → edit → commit → `pytest -q` pass → push(bare) → `open_pr`(dry, draft, §7.3 meta) + `pr.opened` → summarize(comment key `summary:<run>`) + `task.completed` → `outcome=done`, artifacts branch/pr/comment (b) **fail→pass**: 1회차 실패 → 2회차 edit 프롬프트에 테스트 출력 포함 → pass; WIP 커밋도 브랜치에 남음 (c) **3회 실패** → `outcome=failed`, `task.failed(attempt=3)`, WIP push됨, PR 없음 (d) **owned_paths 밖 파일** → 쓰기 전에 거부(`run.tool_denied`) → `outcome=failed`, `task.failed(reason=scope_violation, files=[…])`, 커밋 0 (e) **의존성 파일**(`is_dependency_file`: pyproject.toml, requirements*.txt, uv.lock, poetry.lock, Pipfile*, package.json, *-lock, go.mod/sum, Cargo.*) diff → `outcome=needs_decision`, Issue 코멘트 "승인 필요"(key `needs-decision:<run>`), `task.blocked(reason=needs_decision)`, `decision_request.type == "dependency"` (f) 이벤트 순서 `task.started → run.started` … `run.finished`(마지막)
- green: LangGraph `load_context → plan_changes → edit(EditPlan 구조화 출력 [PC-4 (5) 이후 파일 블록 텍스트 → `parse_edit_plan`, JSON은 폴백], owned 사전 검사) → check_scope → commit → run_tests → {pass: push → open_pr → summarize, fail∧attempt<max: edit, fail∧attempt≥max: fail(WIP push)}`; `CodingAgent(publish, provider, github, repo, model, test_command, shell_timeout, token_budget)`, `last_state` 노출
- gate: `make check`
- design: §5.2 Coding Agent, §15.1 루프, §9.2, §10.1
- notes: D-16, D-20, D-28, D-31. 그래프 결과는 dict — `cast(CodingState, raw)`.

### P4.4 Worker
- depends_on: P4.3
- owned_paths: `worker/**`, `docker-compose.yml`, `Makefile`, `tests/worker/**`, `tests/integration/test_worker.py`
- red: (a) `python -m worker <task_id>` / `worker/entrypoint.py`: 환경변수 `WORKER_REPO_URL, WORKER_BRANCH, WORKER_TASK_JSON(TaskRef+ProjectContext 직렬화), WORKER_REDIS_URL, WORKER_TOKEN(옵션, 빈 값), WORKER_TIMEOUT_MIN=45`, 인자 외 설정 없음 — AST 가드가 `control_plane.config` import 잡음 (b) worktree 준비(clone → branch) → `CodingAgent.run` → `run.finished` → exit code(0 done / 1 failed / 2 needs_decision / 3 timeout) (c) 타임아웃(테스트 1초) → WIP 커밋+push → `task.failed {reason:"timeout", attempt}` + `run.finished {outcome:"timeout", agent_outcome:"timeout"}`(D-28) (d) 워커의 publish는 Redis XADD 직접(`events:{project_id}`) — DB 없음; `--publish-file <path>`로 이벤트를 파일에 적는 테스트 모드 (e) integration: `docker build` → sample_repo + bare remote 볼륨 마운트 → 컨테이너가 브랜치 push → exit 0. Docker 없으면 skip
- green: `Dockerfile`(python:3.12-slim + git + node, non-root `worker`), compose에 `worker` 서비스(빌드만), `worker/publish.py`(Redis XADD, `stream_fields` 재사용)
- gate: `make check` && `make test-integration`(Docker 있으면)
- design: §10.3, §15.1
- notes: D-14, D-17, D-28. 워커가 XADD한 이벤트는 outbox를 거치지 않으므로 DB `events`에는 Scheduler의 `ingest`가 `append_signed`로 append 한다(D-26) — 이 결정은 P4.4 착수 시 `[착수]`에 명시.

### P4.5 Scheduler
- depends_on: P4.4
- owned_paths: `control_plane/scheduler/**`, `tests/scheduler/**`
- red: (a) `task.created` 구독 → `depends_on` 전부 `done`(projection 상태 조회)이고 `status == ready`인 Task만 배정 (b) 위상 정렬, 사이클 → `SchedulerError` (c) owned_paths 겹치는 Task 동시 배정 금지 (`emit.paths_overlap` 재사용) (d) `max_workers` 초과 시 대기 (e) 배정 시 `task.assigned` 발행 후 `WorkerLauncher.launch(task)` — `DockerCliLauncher`(D-15, `docker run … --format json`) [P6.1 전까지 호출자 없음 → `runtime.py`가 설정으로 조립, `mounts=` D-38]와 `FakeLauncher`; 테스트는 Fake (f) `run.finished` 수신 → 슬롯 반환 (g) 워커가 직접 XADD한 이벤트를 DB에 append 하는 `ingest` — 반드시 `chain.append_signed` 호출(멱등, event.id 기준; `run.tool_called`는 `tool_calls`로) (h) 같은 Epic의 Task 2개를 연속 배정 → `epic.activated` 1건, `task.assigned` 2건(중복 없음) — projection 반영 전이라도 프로세스 내 `in_flight`/`activated_epics` memo로 (B11) (i) 기동 실패(`FakeLauncher(fail=True)`) → `task.failed {reason:"launch_failed"}`
- green: `queue.py`, `graph.py`, `launcher.py`, `scheduler.py` 루프
- gate: `make check`
- design: §3.2 Scheduler, §10.1
- notes: D-15, D-20, D-26, D-27, D-28.

### PC-4 브랜치 3개 push
- 자동: `make check`, `make test-integration`
- 자동: `scripts/pc4_run_tasks.py` — PC-3 fake 결과 형태의 Task 3개 → Scheduler + `FakeLauncher`(프로세스 내 `CodingAgent` 실행, FakeProvider 결정적 스크립트) → `tests/fixtures/remote.git`(또는 tmp bare)에 `ai/*` 브랜치 3개, 커밋 트레일러 → `would open_pr` 3건 → `pr.opened` 3건 → `verify_chain` True
- 사람: `git -C <remote> log --all --oneline` 확인, `docs/pc/PC-4.md`
- pass: 자동 전부

---

### P5.1 API
- depends_on: PC-4
- owned_paths: `control_plane/api/**`, `tests/api/**`
- red: httpx AsyncClient로 (a) `POST /projects {name, repo, default_branch?, members?}` → 201 + `project.created`(D-19) [실제 필드; `repo`는 로컬 경로 또는 owner/name — D-38] (b) `GET /projects/{id}` (c) `POST /projects/{id}/goals` → 202 + `goal.created` (d) `GET /projects/{id}/goals/{gid}` 진행률(Task 상태 카운트) (e) `GET /tasks?status=&epic=` (f) `GET /events?since=<seq>&type=` 커서(D-29; event id 아님) (g) `Idempotency-Key` 같은 키 재요청 → 동일 응답, publish 1회; 다른 본문 같은 키 → 422 (h) `/health` 유지 (i) `PATCH /tasks/{tid} {action:"cancel"}` → `task.cancelled`; `POST /goals/{gid}/cancel` → `goal.cancelled` + 미완 Task마다 `task.cancelled(cascade_from=gid)` (D-27)
- green: 라우터 분리, `deps.py`(session, bus, settings, projection), Idempotency 미들웨어(메모리 dict + TTL)
- gate: `make check`
- design: §13
- notes: D-25(루트 이벤트 causation None), D-27, D-29.

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

### P6 — 운영 조립과 as-built 리뷰 반영 (2026-09-13 추가, ## 7의 하위)

근거: 코드 기준 아키텍처 리뷰(`docs/review/as-built-2026-09-13.md`)의 A1~A8, C6. 원칙은 §0 그대로 — Task마다 Red → Green → Gate(`make check`) → `main` 커밋. 설계 결정은 D-36~D-39.

### P6.1 상주 control plane 프로세스
- depends_on: P5.5 (D-40: PC-5는 PC-6 이후 Anthropic으로 판정)
- owned_paths: `control_plane/runtime.py`, `control_plane/__main__.py`, `control_plane/scheduler/**`, `control_plane/config.py`, `.env.example`, `Makefile`, `tests/runtime/**`, `tests/scheduler/**`, `tests/integration/test_launcher.py`
- red: (a) `Runtime(settings, factory, redis).start()`가 asyncio Task 4개 — outbox relay 루프, projection 컨슈머(group `projection`), scheduler 컨슈머(group `scheduler`), retry 적용(주기 1s, `Projection.apply_retries(project_id)`가 프로젝트 단위이므로 `events:*:retry` 키를 SCAN 해 **모든 project**를 순회) — 를 띄우고 `stop()`으로 전부 내린다. 테스트는 `project.created → goal.created → epic/task.created`를 outbox에 넣고 `Runtime`만 돌려 Task가 `assigned`까지 가는 것을 본다(스크립트의 pump 없이) (b) **프로젝트별 repo**(리뷰 A3): `Scheduler._spec`이 `repo_url/default_branch`를 생성자 상수가 아니라 `projects` 행(`repo_full_name`→`RepoCache` 경로는 P6.6 전까지 그대로 경로, `default_branch`)에서 읽는다 — 프로젝트 2개를 한 Scheduler로 배정하면 `LaunchSpec.repo_url`이 각자 다르다 (c) `Settings.worker_launcher: Literal["docker","inprocess"]`(기본 `docker`), `worker_image`(기본 `foreman-worker:dev`), `scheduler_max_workers`(기본 4) (d) `InProcessLauncher`: control plane 프로세스 안에서 `CodingAgent`를 asyncio Task로 실행(Dry GitHub, provider는 `get_provider(settings)`), `run.finished`가 Redis로 나온다 — Docker 없는 개발 환경용 (e) `python -m control_plane`이 `build_runtime(settings)`를 띄우고 SIGINT/SIGTERM에 `stop()` (f) 통합(Docker): `DockerCliLauncher(mounts=[(repo_root, repo_root)])`가 실제 `foreman-worker:test` 컨테이너를 `-v`로 띄우고 그 컨테이너가 마운트된 로컬 repo를 clone해 `task.started`를 XADD 한다(지금까지 호출자 0이던 경로, D-38)
- green: `runtime.py`(`Runtime`, `build_runtime`), `launcher.py`에 `InProcessLauncher` + `DockerCliLauncher(mounts=)`, `scheduler.py`에 프로젝트 행 조회(세션당 1회 캐시), Makefile `run-control-plane`
- gate: `make check && make test-integration` (Docker 있으면; 없으면 (f)는 skip 마커로 기록)
- notes: D-15, D-38(경로는 P6.6에서 RepoCache로). runbook 기동 순서 갱신은 P6.8.

### P6.2 승인 대기 복원
- depends_on: P6.1
- owned_paths: `control_plane/orchestrator/runner.py`, `tests/api/test_goal_flow.py`
- red: (a) Goal이 `awaiting_plan_approval`에서 대기 중일 때 **같은 checkpointer**로 새 `GoalRunner`를 만들어 `startup()` → `is_waiting(gid)` True, `waiting(pid)[gid]`에 discussion 번호·revision (b) 그 상태에서 `/approve` 웹훅 → resume → `task.created` 발행 (c) 체크포인트 스레드가 없는 Goal(예: MemorySaver가 새로 생김)은 복원하지 않고 `runner.restore_skipped` 경고 (d) 복원된 Goal도 `_run` 없이 `resume`만으로 진행
- green: `startup()`이 projection의 `awaiting_plan_approval` Goal을 읽고 `checkpointer.aget_tuple(cfg)`로 스레드 확인 후 `_waiting`을 채운다
- gate: `make check`
- notes: 리뷰 A6, D-12.

### P6.3 Dry 자동 머지
- depends_on: P6.1
- owned_paths: `control_plane/dry_merge.py`, `control_plane/runtime.py`, `scripts/e2e_dry_run.py`, `tests/runtime/test_dry_merge.py`, `tests/test_e2e_script.py`
- red: (a) `DryMerger(factory, bus, settings).handle(delivery)`: `dry_run=true` + `pr.opened{task_id, pr_number}` → `pr.merged{task_id, pr_number, merged_by:"dry-run"}` 1건(actor `system:dry-merge`, causation=pr.opened id); 같은 PR이 두 번 와도 1건 — 프로세스 내 `(project_id, pr_number)` memo **와** `tasks.pr_merged_at` 둘 다 확인(projection 지연 대비; 새어 나가도 projection이 전이 없이 흡수) (b) `dry_run=false` → 발행 0 (c) `Runtime`은 dry_run일 때만 group `dry-merge` 컨슈머를 띄운다 (d) `scripts/e2e_dry_run.py`의 자체 자동 머지 코드 삭제 → `DryMerger`를 조립해 사용; `--fake` 전체 흐름 PASS 유지, 출력 `(auto-merge)` 줄은 `(dry-merge)`로
- green: `dry_merge.py`
- gate: `make check`
- notes: D-36. 의존 Task는 이제 Dry에서도 `done`을 거쳐 배정된다.

### P6.4 discussion_comment 웹훅
- depends_on: P6.2
- owned_paths: `github_adapter/webhooks.py`, `control_plane/api/approvals.py`, `tests/github_adapter/test_webhooks.py`, `tests/api/test_goal_flow.py`, `tests/fixtures/webhooks/discussion_comment_approve.json`, `tests/fixtures/webhooks/discussion_comment_reject.json`
- red: (a) `discussion_comment.created` 본문 `/approve` → `SlashCommand(source="discussion", number=<discussion.number>, …)`; `issue_comment`는 `source="issue"`, `number=issue.number`(`issue_number` 필드는 유지) (b) `ApprovalService.on_slash`: `source="discussion"`이면 `plan_discussion_number == number`인 대기 Goal만 매칭(없으면 no-op 202); `issue`는 기존 규칙 (c) 서명·중복·봇·권한(403) 규칙 동일 (d) 픽스처 2개(approve/reject), `sender.type`·`repository.full_name` 형식은 GitHub 문서의 discussion_comment payload
- green: `SlashCommand`에 `source`, `number` 추가, `_discussion_comment` 핸들러
- gate: `make check`
- notes: 리뷰 A5. `discussion_comment` payload는 추측하지 않는다(CLAUDE.md): D-09처럼 docs.github.com 웹훅 이벤트 페이지를 fetch로 확인하고 출처 URL·확인일을 핸들러 docstring과 픽스처 머리에 적는다. 실 Discussion 웹훅 구독 설정은 X.1.

### P6.5 비용 계산
- depends_on: P6.1
- owned_paths: `agents/llm/pricing.py`, `agents/base.py`, `control_plane/config.py`, `.env.example`, `control_plane/runtime.py`(단가를 `DockerCliLauncher(worker_env=)`에 전달), `worker/entrypoint.py`, `scripts/e2e_dry_run.py`, `scripts/pc4_run_tasks.py`, `tests/agents/test_base.py`, `tests/agents/test_llm.py`, `tests/worker/**`, `tests/runtime/**`
- red: (a) `estimate_cost(tokens_in, tokens_out, price_in_per_mtok, price_out_per_mtok) -> float`(반올림 6자리) (b) `BaseAgent(prices=Prices(0,0))` 기본, `run.finished.cost_usd` = 계산값 (c) 워커 env `WORKER_LLM_PRICE_IN_PER_MTOK/OUT`, Settings `llm_price_in_per_mtok/llm_price_out_per_mtok`(기본 0) → `LaunchSpec.env`에 전달 (d) e2e/pc4 토큰 합계 줄에 `≈ $x.xx`(단가 0이면 `$0.00 (단가 미설정)`)
- green: `pricing.py`(`Prices`, `estimate_cost`), `BaseAgent` 인자
- gate: `make check`
- notes: D-39.

### P6.6 repo 확보
- depends_on: P6.1
- owned_paths: `control_plane/repo_cache.py`, `control_plane/orchestrator/runner.py`, `control_plane/scheduler/scheduler.py`, `control_plane/config.py`, `.env.example`, `tests/test_repo_cache.py`, `tests/api/test_goal_flow.py`
- red: (a) `RepoCache(root, url_for=).ensure(repo) -> Path`: 존재하는 로컬 경로 → 그 경로, clone 없음 (b) `owner/name` → `root/owner/name`에 `git clone <url_for(repo)>`(기본 `https://github.com/<owner>/<name>.git`; 테스트는 로컬 bare remote를 돌려주는 `url_for`) (c) 이미 있으면 `git fetch` 후 그 경로 (d) `GoalRunner`의 분석 경로(`repo_path_for` 기본값)와 `Scheduler._spec`의 `repo_url`이 둘 다 `RepoCache.ensure`를 쓴다 (e) clone 실패 → `goal.blocked{reason:"repo_unavailable", detail}`, Scheduler는 `task.failed{reason:"launch_failed"}` (f) `Settings.repo_root`(기본 `./repos`)
- green: `repo_cache.py`
- gate: `make check`
- notes: D-38. 워커의 clone/push 인증은 X.1(`WORKER_TOKEN`).

### P6.7 PR 생성 control plane으로
- depends_on: P6.3, P6.6
- owned_paths: `agents/coding.py`, `agents/base.py`, `control_plane/pr_opener.py`, `control_plane/runtime.py`, `control_plane/events/schema.py`(additive 키만), `control_plane/events/projection.py`, `worker/entrypoint.py`, `scripts/e2e_dry_run.py`, `scripts/pc4_run_tasks.py`, `tests/agents/test_coding.py`, `tests/runtime/test_pr_opener.py`, `tests/worker/**`, `tests/integration/test_worker.py`, `tests/events/test_projection.py`
- red: (a) `CodingAgent` pass 경로 이벤트: `task.started → run.started → … → run.artifact_produced{kind:"branch", ref:<branch>} → task.completed{run_id, branch, summary} → run.finished`; `summary`는 기존 `summarize` 노드의 LLM 요약 텍스트(호출 수 그대로 3), `pr.opened`·Issue 코멘트 발행 0, `GitHubTool` 호출 0 (b) `PrOpener(factory, bus, github).handle(task.completed)`: `open_pr(head=branch, base=<project.default_branch>, draft, §7.3 메타)` → `pr.opened{task_id, run_id, pr_number, head, base}` + Issue 요약 코멘트(key `summary:<run>`, 본문은 `task.completed.summary`); 같은 `task.completed` 두 번 → PR 1건(client 멱등 + `tasks.pr_number` 확인) (c) `Runtime`이 group `pr-opener` 컨슈머로 등록, client는 `get_github_client(settings)`(Dry/실은 여기서만) (d) projection: `task.completed.branch` → `tasks.branch_name` (e) 워커 통합 테스트: 컨테이너는 push까지, 스트림에 `pr.opened` 없음 (f) e2e/pc4: `would open_pr`가 스크립트 안 Runtime 컴포넌트에서 나온다, 판정 기준(PR ≥ N) 유지
- green: 스키마 `TaskCompletedPayload.branch/summary: NotRequired`, `RunArtifactProducedPayload{kind, ref}`(신규 TypedDict — 키 추가만), `pr_opener.py`, `coding.py` 그래프에서 `open_pr` 노드 제거, `summarize` 노드는 남겨 LLM 요약 + `task.completed{branch, summary}` 발행만(Issue 코멘트 호출 제거), `agents/tools/github.py`는 남기되 CodingAgent가 쓰지 않음
- gate: `make check`
- notes: D-37. 리뷰 A4. 실 PR은 X.1에서 `DRY_RUN=false`만으로 켜진다.

### P6.8 ROADMAP·runbook 정리
- depends_on: P6.4, P6.5, P6.7
- owned_paths: `ROADMAP.md`, `docs/runbook.md`
- red: 없음(문서)
- green: (1) §4 D 번호 순 재정렬(C1) (2) P5.1 red (a) 필드를 `{name, repo, default_branch?, members?}`로(C2) (3) P4.3 green·P4.5 (e)에 현재 상태 각주(C3) (4) PC-5 pending과 X.2 Anthropic 대기를 §6 한 행으로(C5) (5) §8 X.1을 남은 항목(App 체크리스트, `.env` 실값, 토큰 전달, Discussion 웹훅 구독, cleanup 스크립트)으로 축소(C4) (6) runbook 기동 순서 `docker-up → migrate → run-control-plane → run-api`, 워커 실행 설명을 `HITL_WORKER_LAUNCHER`로
- gate: `scripts/check_runbook.sh` && `make check`

### PC-6 상주 프로세스 + API로 Goal → 브랜치 → done 완주
- 자동: `make check`, `make test-integration`, `scripts/check_runbook.sh`, `scripts/e2e_dry_run.py --fake`
- 자동: 두 프로세스(`make run-control-plane`(`HITL_WORKER_LAUNCHER=inprocess`), `make run-api`)를 띄운 뒤 `scripts/pc6_via_api.py <repo_path> "<goal>"`: `POST /projects` → `POST /goals` → `GET /goals/{gid}`가 `awaiting_plan_approval` → 서명된 `discussion_comment` `/approve` → 폴링으로 Task 전부 `done`(Dry 자동 머지) → `GET /events?since=0` 재계산 `verify_chain` True → bare remote 브랜치 수 ≥ Task 수, `pr.opened` 수 == Task 수. 스크립트는 HTTP·git만 쓰고 relay/projection/scheduler/dry-merge/pr-opener를 조립하지 않는다. LLM은 로컬(`qwen2.5-coder:14b`), Goal은 D-35 수준(예: "Add a maths helpers module with add/mul and tests")
- 소유: `scripts/pc6_via_api.py`, `docs/pc/PC-6.md` (PC-1처럼 PC 항목이 스크립트를 소유)
- 사람: `git -C <remote> log --all --oneline`, `docs/pc/PC-6.md`
- pass: 자동 전부

---

### P7 — X.1 실 GitHub 연결 (2026-09-14 추가, ## 7의 하위)

전제(사용자 제공): GitHub App(App ID, private key PEM, installation id, webhook secret), App이 설치된 테스트 repo
`owner/name`(권한: Contents RW, Issues RW, Pull requests RW, Discussions RW, Metadata R; 웹훅 구독: issue_comment,
discussion_comment, pull_request, pull_request_review; `check_suite`는 선택), 웹훅 공개 URL(smee.io 채널 또는 cloudflared).
`.env`에 `HITL_GITHUB_APP_ID / HITL_GITHUB_APP_PRIVATE_KEY(개행 \n) / HITL_GITHUB_INSTALLATION_ID / HITL_GITHUB_WEBHOOK_SECRET`.
`HITL_DRY_RUN=false`는 PC-7 실행 직전에만.

### P7.1 App 점검 스크립트 + ping
- depends_on: PC-6
- owned_paths: `scripts/github_app_check.py`, `github_adapter/app_check.py`(로직; 스크립트는 얇은 CLI), `github_adapter/webhooks.py`, `tests/github_adapter/test_app_check.py`, `tests/github_adapter/test_webhooks.py`, `tests/fixtures/webhooks/ping.json`, `docs/runbook.md`
- red: (a) `github_app_check.run(settings, http)` → JWT 생성 → `GET /app`(앱 이름) → `GET /app/installations`(설치 목록에 `installation_id` 있음) → `POST /app/installations/{id}/access_tokens`(토큰; 값은 출력 안 함, `permissions`·`repositories` 확인) → `GET /repos/{owner}/{name}`(App으로 접근 가능) → `GET /app/hook/config`(웹훅 URL·secret 설정 여부) → 체크리스트 표 출력, 부족한 권한·구독은 `[FAIL]`. respx로 전부 mock, 실 호출 0 (b) 필수 권한 집합 = contents:write, issues:write, pull_requests:write, discussions:write, metadata:read (c) `X-GitHub-Event: ping` → 200 `{"pong": true}`(App 저장 시 GitHub가 보냄; 서명 검증은 그대로) (d) 실패 종료 코드 1
- green: `scripts/github_app_check.py`(`--repo owner/name`), `webhooks.py`에 `ping`
- gate: `make check`
- notes: D-42. API 경로는 docs.github.com REST(App 인증) 문서를 fetch로 확인하고 출처를 docstring에.

### P7.2 브랜치 push는 control plane (D-41)
- depends_on: P7.1
- owned_paths: `control_plane/pr_opener.py`, `control_plane/repo_cache.py`, `control_plane/runtime.py`, `github_adapter/auth.py`, `tests/runtime/test_pr_opener.py`, `tests/test_repo_cache.py`
- red: (a) `RepoCache(url_for=)` 기본이 `token_provider`가 있으면 `https://x-access-token:<token>@github.com/owner/name.git`; 로그·예외 메시지에 토큰이 안 나타난다(`***`) (b) `PrOpener(pusher=)`: `task.completed{branch}` → `git -C <RepoCache 경로> push origin <branch>`(토큰 URL은 push 시에만, remote 설정에 저장하지 않음 — `git push <url> <branch>` 형태) → 성공 후 `open_pr` (c) push 실패 → `pr.opened` 없음, `pr_opener.push_failed` 기록, Task는 `in_review` 유지 (d) Dry 모드(`dry_run=true`)면 push를 건너뛴다(로컬 bare/clone이 곧 origin) (e) `runtime.py`가 `InstallationTokenProvider`를 만들어 RepoCache·PrOpener에 준다(dry면 None)
- green: `RepoCache.url_for` 토큰 주입, `PrOpener._push`, 토큰 마스킹 유틸
- gate: `make check`
- notes: D-41, §12. 토큰은 `InstallationTokenProvider` 캐시(P2.2)를 재사용.

### P7.3 웹훅 공개 경로
- depends_on: P7.1
- owned_paths: `docs/runbook.md`, `scripts/check_runbook.sh`, `Makefile`
- red: 없음(문서·설정). 
- green: runbook에 "웹훅 받기" 절: `npx smee-client --url <채널> --target http://localhost:8000/webhooks/github`(또는 `cloudflared tunnel --url`), App 설정의 Webhook URL·secret, `ping` 확인, `discussion_comment` 구독 확인. `make run-webhook-tunnel`(SMEE_URL 필요)
- gate: `scripts/check_runbook.sh`
- notes: 이 Task는 사용자가 채널 URL을 주면 실제로 한 번 `ping`을 받아본다(사람 항목).

### P7.4 테스트 repo 시드 + 정리 스크립트 (D-42)
- depends_on: P7.2
- owned_paths: `scripts/seed_test_repo.py`, `scripts/cleanup_repo.py`, `github_adapter/cleanup.py`(로직), `github_adapter/client.py`(list_open_items/close_issue/close_pull/list_refs/delete_ref/commit_count_hint), `tests/github_adapter/test_cleanup.py`
- red: (a) `seed_test_repo.py <owner/name>`: `tests/fixtures/sample_repo`를 토큰 URL로 push(main 강제 아님 — 비어 있지 않으면 중단) (b) `cleanup_repo.py <owner/name> [--apply]`: 기본은 **목록만**; `--apply`면 `ai-platform:meta` 마커 Issue/PR close, `ai-platform:` Discussion 닫기(가능하면), `ai/*` 브랜치 삭제, `ai:` 라벨은 유지. 마커 없는 것은 건드리지 않음 — respx로 검증 (c) 삭제 전 개수 출력, `--apply` 없이 실 변경 0
- green: 두 스크립트, 필요한 client 메서드(`list_issues(marker)`, `close_issue`, `delete_branch`)는 멱등·마커 기반
- gate: `make check`

### PC-7 실 GitHub에서 Goal 1개
- 자동: `make check`, `scripts/github_app_check.py --repo <owner/name>` 전부 `[ok]`
- 사람+자동(`HITL_DRY_RUN=false`, 상주 프로세스 + API + 터널): `seed_test_repo.py` → `POST /projects {repo: owner/name}` → `POST /goals`(D-35 수준 Goal) → GitHub에 Plan Discussion 생성 확인 → 사람이 Discussion에 `/approve` → Issue N개·Milestone 생성 → 워커 실행 → control plane이 브랜치 push + draft PR 생성 → 사람이 PR 1개 머지 → `pull_request.closed` 웹훅 → `pr.merged` → Task done → 의존 Task 배정. 이벤트 체인 `verify_chain` True
- 사람: 실제 Issue/PR/Discussion 스크린샷 또는 URL을 `docs/pc/PC-7.md`에, 마지막에 `cleanup_repo.py --apply`
- pass: 자동 전부 + Goal 1개의 Task ≥ 1이 사람 머지로 done
- 소유: `docs/pc/PC-7.md`

---

### P8 — 상주 운영 결함 수정 (2026-09-15 추가, ## 7의 하위)

근거: 별도 세션의 외부 점검 리포트 F-1~F-12 (`/home/lhjin0j/.claude/jobs/8428db5e/tmp/run-report-2026-09-15-rerun.md`, 사본 `docs/review/runcheck-2026-09-15.md`). PC-6/PC-7은 깨끗한 DB(`pc6`/`pc7`)·Redis DB 12/13·inprocess 런처로 돌렸기 때문에 기본 환경(DB `hitl`·Redis 0의 과거 데이터, docker 런처)의 결함을 못 봤다. 결정 D-43~D-50.

### P8.1 projection 오류 분류 + Dry 원격 clone 금지
- depends_on: PC-7
- owned_paths: `control_plane/events/projection.py`, `control_plane/repo_cache.py`, `control_plane/runtime.py`, `tests/events/test_projection.py`, `tests/test_repo_cache.py`, `tests/runtime/test_runtime.py`
- red: (a) 프로젝트 행이 없는 `task.created`(FK 위반, sqlite는 `PRAGMA foreign_keys`로 재현 또는 `_require` 경로)를 `handle`하면 `ProjectionTransient`가 아니라 retry 스트림에 들어가고 5회 뒤 포기한다(XAUTOCLAIM 재전달 0회 — `bus.handler_failed` 없음) (b) `IntegrityError`는 `_TRANSIENT`에 없다 (c) `RepoCache(dry_run=True)`(또는 token_getter None)로 `owner/name`을 `ensure`하면 네트워크 호출 없이 `RepoUnavailable`(subprocess 호출 0 — monkeypatch) (d) `Runtime`은 `settings.dry_run`을 RepoCache에 전달
- green: `_TRANSIENT`에서 `IntegrityError` 분리 → `OrderingError`로 래핑, `RepoCache(dry_run=)`
- gate: `make check`
- notes: D-48, D-49.

### P8.2 Docker 런처: 프로젝트 repo 개별 마운트 + 호스트 uid
- depends_on: P8.1
- owned_paths: `control_plane/scheduler/launcher.py`, `control_plane/runtime.py`, `worker/entrypoint.py`, `tests/scheduler/test_launcher_docker.py`, `tests/integration/test_launcher.py`
- red: (a) `DockerCliLauncher.launch(spec)`의 argv(테스트는 `docker_bin`을 기록용 스텁으로)에 `-v <spec.repo_url>:<spec.repo_url>`가 REPO_ROOT 밖 로컬 경로일 때 추가된다; 안이면 root 마운트 하나만 (b) `--user <uid>:<gid>`와 `-e HOME=/tmp/worker-home`가 기본으로 들어간다(`user=None`이면 생략) (c) 통합: 호스트 uid 소유·기본 권한(755)인 bare remote를 **chmod 없이** 마운트해 워커가 push 성공 (d) 워커 entrypoint는 HOME이 없어도 git이 동작(`GIT_CONFIG_NOSYSTEM`, `safe.directory *`는 env로)
- green: `mounts` 동적 추가, `user` 인자, entrypoint의 git env
- gate: `make check && make test-integration`
- notes: D-43, F-5a/F-5b. Dockerfile은 그대로(uid 10001 기본).

### P8.3 죽은 워커 정리
- depends_on: P8.2
- owned_paths: `agents/base.py`, `control_plane/runtime.py`, `control_plane/scheduler/launcher.py`, `control_plane/scheduler/scheduler.py`, `tests/agents/test_base.py`, `tests/runtime/test_reaper.py`, `tests/scheduler/test_scheduler.py`
- red: (a) `BaseAgent.run`: `execute` 예외 → 이벤트 순서 `task.started → run.started → task.failed{reason:"error", attempt} → run.finished{failed}`; `outcome=failed`인데 `task.failed`를 이미 낸 경우(scope/tests_failed)는 중복 발행 없음 (b) `WorkerLauncher.is_alive(worker_id) -> bool`: Fake는 set으로 제어, InProcess는 asyncio Task 상태, Docker는 `inspect .State.Running`(스텁) (c) `Runtime` reaper(주기 5s): in_flight run의 워커가 죽었고 그 run의 `run.finished`가 없으면 `task.failed{reason:"worker_died", attempt}` + `run.finished{outcome:"failed", agent_outcome:"failed", error:"worker died"}`(actor system:scheduler) → Task ready(attempt<max)→재배정 / blocked; `timeout_min+5분` 초과면 `reason:"timeout"` (d) 기동 시 DB `assigned/running` Task 중 in_flight에 없는 것 → 같은 처리(`worker_died`) — 외부 점검의 runcheck3~5 고착이 풀린다
- green: `Scheduler.in_flight_runs`(task→(run_id, worker_id, started_at)), `Runtime._reap_loop`
- gate: `make check`
- notes: D-44. `run.finished`는 워커가 안 낸 경우에만 system이 대신 낸다(체인에 두 번 안 남게 `run_id` 기준 확인).

### P8.4 API: repo 유일, events 폴백, 시크릿 fail-closed
- depends_on: P8.1
- owned_paths: `control_plane/api/projects.py`, `control_plane/api/goals.py`, `control_plane/api/app.py`, `github_adapter/webhooks.py`, `tests/api/test_api.py`, `tests/github_adapter/test_webhooks.py`
- red: (a) 같은 `repo`로 `POST /projects` 두 번 → 두 번째 409(projection 전이어도 — events 조회) (b) `POST /projects` 201 직후(pump 없이) `POST /goals` → 202, `GET /projects/{id}` → 200(events 폴백, `created_at`은 이벤트 ts) (c) `WebhookHandler(secret="")`에 어떤 요청이든 503, 서명이 맞아도 (d) `app()`이 dry_run=false인데 secret 비면 경고 로그
- green: `projects.py`·`goals.py`에 `_project_exists(state, id)`(projection → events 순), 409, `webhooks.py` fail-closed
- gate: `make check`
- notes: D-45, D-46, D-50.

### P8.5 ingest 중복 XADD 제거 + tool_calls 확인
- depends_on: P8.1
- owned_paths: `control_plane/scheduler/scheduler.py`, `control_plane/events/chain.py`, `tests/scheduler/test_scheduler.py`, `tests/events/test_bus.py`
- red: (a) 워커가 XADD한 `task.started`를 ingest한 뒤 relay를 돌려도 스트림 entry 수가 늘지 않는다(중복 0), DB `events` 행의 `stream_id`는 워커 메시지 id, `published_at` 설정 (b) `run.tool_called` ingest → `tool_calls` 행 1건, `events` 행 0건(D-31 확인 테스트) (c) WS `?since`는 DB seq 기준이라 서명본 미XADD와 무관(기존 테스트 유지)
- green: `Scheduler.ingest(event, message_id=)`가 `append_signed` 후 부기 컬럼 갱신; `Delivery.message_id` 전달
- gate: `make check`
- notes: D-47, F-11, F-12(D-31 설계대로 — 리포트에 답).

### P8.6 no-reload, gitignore, 문서·환경 불일치
- depends_on: P8.3, P8.4, P8.5
- owned_paths: `Makefile`, `.gitignore`, `docs/runbook.md`, `.env.example`, `tests/fixtures/sample_repo/.gitignore`, `docs/review/runcheck-2026-09-15.md`
- red: 없음(문서·설정). 
- green: `run-api`는 `API_RELOAD=1`일 때만 `--reload --reload-dir control_plane`; `.gitignore`에 `repos/`; sample_repo에 `.pytest_cache/` gitignore + 정리; runbook에 "docker 런처는 REPO_ROOT 밖 로컬 경로도 개별 마운트, 호스트 uid로 실행", 이미지 태그(`foreman-worker:dev` 기본, 통합 테스트는 `:test`) 명시, MinIO 미사용 명시; `.env`의 `HITL_LLM_MODEL` 중복 줄은 사용자에게 안내(에이전트는 `.env`를 읽지 않음); 리포트 사본 저장
- gate: `scripts/check_runbook.sh && make check`
- notes: D-48, F-4, F-10.

### PC-8 외부 점검 절차 재실행
- 자동: `make check`, `make test-integration`, `scripts/check_runbook.sh`
- 사전(사용자 결정 2026-09-15): 개발 DB `hitl` drop → `make migrate`, Redis 0 `FLUSHDB` — 외부 점검의 runcheck* 데이터는 지운다. 고착 복구(P8.3 (d))는 PC-8 (5)에서 컨테이너를 죽여 재현한다
- 자동(외부 점검과 같은 환경: 기본 DB `hitl`·Redis 0·**docker 런처**, `make run-control-plane` + `make run-api`): (1) 기동 직후 `bus.handler_failed`·`ProjectionTransient` 무한 재전달 0 (2) `HITL_REPO_ROOT` **밖** 로컬 경로 repo로 프로젝트 → Goal → `/approve` → 워커 컨테이너가 push 성공 → PR(dry) → done (3) 같은 repo로 두 번째 프로젝트 409 (4) 스트림 distinct id == entry 수 (5) 컨테이너를 `docker kill` 하면 5분 안에 `task.failed{worker_died}` → 재배정
- 사람: 리포트의 확인 절차 6개 항목별 통과 표, `docs/pc/PC-8.md`
- pass: 자동 전부

---

### P9 — 공개 데모 호스팅 (2026-09-16 추가, ## 7의 하위)

근거: 원티드 AI 챔피언십 제출 규정(심사자가 로그인·설치·API 키 없이 웹에서 핵심 기능 체험, 심사 기간 내 접속 보장). 사용자 결정: 이 서버 `foreman.antaewoo.com`(nginx·인증서·DNS는 사용자), 로컬 Ollama(gemma4:12b 후보), 실 GitHub 공개 repo, PR 머지는 소유자가 GitHub에서. 결정 D-52, D-53.

### P9.1 Plan 본문·Goal 목록·Task 링크 API (0.5d)
- depends_on: PC-8
- owned_paths: `control_plane/events/schema.py`(추가만), `control_plane/events/projection.py`, `control_plane/store/models.py`, `alembic/versions/0003_goal_plan_markdown.py`, `control_plane/orchestrator/graph.py`, `control_plane/api/{goals,tasks,projects,deps}.py`, `tests/events/test_projection.py`, `tests/api/test_api.py`, `tests/orchestrator/test_graph.py`
- red: (a) `goal.plan_proposed{plan_markdown:"# Plan"}` projection → `Goal.plan_markdown == "# Plan"`, 키 없으면 None(구 이벤트 호환) (b) `draft_plan`(graph.py:158-164)이 낸 payload에 `plan_markdown == markdown` (c) `GET /projects/{id}/goals` → `{items:[…]}` created_at desc: id/title/status/plan_revision/plan_discussion_number/created_at/total/done (d) `GET …/goals/{gid}`에 `plan_markdown`, `plan_discussion_url`(repo가 `owner/name`이면 `https://github.com/{repo}/discussions/{n}`, 아니면 None) (e) `TaskOut`에 `spec`, `issue_url`, `pr_url` (f) `ProjectOut`에 `repo_url`
- green: `GoalPlanProposedPayload.plan_markdown: NotRequired[str]`; `_goal_plan_proposed`(projection.py:126)에 `plan_markdown` 저장; `Goal.plan_markdown` Text nullable + 0003; `deps.gh_url(repo, kind, n)` 도우미 하나를 세 라우터가 공유; `list_goals` 라우트. Task summary는 컬럼 없이 UI가 `task.completed` 이벤트에서 읽는다.
- gate: `make check && make migrate`

### P9.2 데모 콘솔 — 정적 1페이지 (0.75d)
- depends_on: P9.1
- owned_paths: `control_plane/api/static/{index.html,demo.js,demo.css}`, `control_plane/api/app.py`, `tests/api/test_demo_ui.py`
- red: (a) `GET /` 200 `text/html`, 본문에 `id="goals"` (b) `GET /static/demo.js` 200 (c) `/health`·`/docs` 그대로
- green: `app.py`에 `StaticFiles` 마운트 + `GET /`(`include_in_schema=False`) → `FileResponse`. 빌드·CDN 없음, 같은 origin이라 CORS 불필요. 화면: 헤더(repo 링크, "PR 머지는 repo 소유자가 GitHub에서" 안내, 작동 원리 3줄) / 프로젝트: `GET /projects` 첫 항목 고정 / Goal 목록 + 생성 폼(예시 Goal 버튼 3개, X-2와 같은 문장) / Goal 상세: 상태 배지, Plan(`<pre>`), Discussion 링크, `awaiting_plan_approval`일 때만 Approve/Reject(reason) → `X-User-Id: judge` 고정 / Task 표(title·status·attempt·Issue·PR 링크·spec 접기) / 이벤트 로그: WS `/projects/{id}/stream?since=<last seq>`(`wss:` 자동, 끊기면 3s 후 재접속), 이벤트 도착 시 500ms 디바운스로 goal·tasks 재조회, WS 실패 시에만 5s 폴링. 429/403/409 응답 본문은 토스트로 그대로. `[Showcase]` 접두 Goal은 목록 맨 위 고정.
- gate: `make check` + 브라우저 수동(스크린샷 `docs/pc/PC-9-*.png`)
- notes: D-52. ≤ 400줄.

### P9.3 데모 모드 가드 + 레이트리밋 (0.5d, P9.2와 병렬 가능)
- depends_on: P9.1
- owned_paths: `control_plane/config.py`, `control_plane/api/demo_guard.py`(신규), `control_plane/api/{deps,app,projects,goals,tasks}.py`, `tests/api/test_demo_guard.py`, `.env.example`
- red (Settings `demo_mode=True, admin_token="t", demo_max_running_goals=1, demo_goals_per_hour=6, demo_post_per_ip_per_min=10`): (a) `POST /projects` 토큰 없음 401, `X-Admin-Token: t` 201 (b) `…/cancel`, `PATCH …/tasks/{tid}` 토큰 없음 401 (c) 프로젝트에 status ∈ {planning, active} Goal이 있으면 `POST goals` 429 `demo: a goal is already running`(awaiting은 미포함) (d) 1시간 내 6개면 429 (e) 같은 IP(`X-Forwarded-For` 첫 값) POST 11번째 429 (f) `demo_mode=False`면 전부 기존 동작
- green: `demo_guard.py` — `require_admin` 의존성(demo_mode일 때만 검사, `SecretStr`, 로그 금지), `RateLimit` 순수 ASGI 미들웨어(IdempotencyMiddleware 패턴, 메모리 토큰버킷, POST/PATCH만), `goal_quota(state, project_id)`를 `create_goal` 앞에서(DB `goals` count). uvicorn `--proxy-headers --forwarded-allow-ips 127.0.0.1`.
- stretch(시간 남으면): awaiting 30분 방치 Goal 자동 거절(`by:"system:demo"`, lifespan 60s 루프).
- gate: `make check`
- notes: D-52. `/docs`는 켜둔다(관리 라우트는 401이라 무해).

### P9.4 배포 파일 + docs/deploy.md (0.25d) — nginx는 사용자 담당
- depends_on: P9.3
- owned_paths: `deploy/systemd/foreman-{control-plane,api}.service`, `deploy/nginx/foreman.antaewoo.com.conf`(참고용 사본), `docs/deploy.md`, `docs/runbook.md`(링크 1줄)
- 포트: **API 8000**(nginx가 `127.0.0.1:8000`으로 프록시, WS 업그레이드 필요). control plane은 포트 없음.
- green: systemd **system unit** 2개: `User=lhjin0j`, `SupplementaryGroups=docker`, `WorkingDirectory=/home/lhjin0j/foreman`, `EnvironmentFile=…/.env`, `Environment=HITL_DRY_RUN=false HITL_DEMO_MODE=true`, `Restart=always`, `After=network-online.target docker.service`; api `ExecStart=/home/lhjin0j/.local/bin/uv run uvicorn control_plane.api.app:app --factory --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1`, control-plane `… uv run python -m control_plane`. `docs/deploy.md` = 사용자 명령 순서 + 롤백 + `journalctl -u foreman-api -f`.
- gate: `scripts/check_runbook.sh`.

### P9.5 LLM 선정 (0.25d + 실행 대기, P9.1과 병렬로 백그라운드)
- depends_on: PC-8
- owned_paths: `docs/pc/X-2.md`, `.env.example`(주석)
- 절차: `ollama pull gemma4:12b`. 후보 `qwen2.5-coder:14b`(현행), `gemma4:12b`, `gemma4:e4b`(속도 기준선). 각 모델 `HITL_LLM_PROVIDER=openai_compat HITL_LLM_MODEL=<m> uv run python scripts/e2e_dry_run.py tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests" --dump docs/pc/x2-<m>` × 2회(X-2 표와 같은 조건, `setsid nohup`, 다른 GPU 작업 없을 때). 기록: Task 수 / done / 브랜치 / Plan JSON 1회 통과 / 호출당 초 / 토큰. 판정: Plan JSON 첫 시도 유효 ∧ done ≥ 현행 ∧ 호출당 ≤ 60s. 동률이면 현행 14b. 26b는 "VRAM 초과, 제외" 한 줄.
- 데모 `.env`(사용자): `HITL_LLM_PROVIDER=openai_compat`, `HITL_LLM_MODEL=<선정>`, `HITL_SCHEDULER_MAX_WORKERS=2`(GPU 1개, Ollama가 직렬화). 권장: ollama 서비스 `OLLAMA_KEEP_ALIVE=-1`, 심사 기간에 다른 Ollama 워크로드(27GB 모델) 중지.
- gate: X-2.md 결론 문단.

### P9.6 데모 콘텐츠 + repo 정리 (0.25d)
- depends_on: P9.3, P9.5, 배포 완료
- owned_paths: `scripts/demo_seed.py`(신규), `docs/deploy.md`(§데모 준비)
- green: `demo_seed.py` — (1) `scripts/cleanup_repo.py AnTaewoo/foreman_test --apply` (2) DB `hitl` drop → `make migrate`, Redis 0 FLUSHDB 안내(PC-8 절차) (3) `POST /projects {name:"foreman demo", repo:"AnTaewoo/foreman_test", members:[{AnTaewoo,owner},{judge,approver}]}`(admin token) (4) `[Showcase]` Goal 생성 → 사용자 approve → 사용자 PR 머지 → done.
- 정리 정책: 심사 기간(09-20 ~ +7일)에는 cleanup을 돌리지 않는다(DB↔repo 불일치 방지). 종료 후 cleanup + DB 초기화. Discussions는 누적 허용(D-42).
- gate: 콘솔에서 showcase Goal done + merged PR 링크.

### P9.7 Goal·Epic 완료 판정 (0.25d) — §6 2026-09-19
- owned_paths: `control_plane/scheduler/scheduler.py`, `tests/scheduler/test_completion.py`(신규), `docs/design.md`(§9.4 한 문단)
- red: 마지막 Task done → done Task가 있는 Epic마다 `epic.completed` 1건 → `goal.completed` 1건(seq가 뒤); 웹훅 재전송에도 중복 없음; 일부 cancelled여도 done ≥ 1이면 완료; Task 없는 Epic은 막지 않음; 전부 cancelled면 미발행.
- green: `pr.merged`/`task.completed`/`task.cancelled` 처리 뒤 프로젝트의 active Goal을 판정. 중복 방지는 `activated_epics`와 같은 memo + 상태 조회. 스키마 무변경.
- gate: `make check`.

### P9.8 installation 토큰 풀 + 설치 탐지 (0.5d)
- owned_paths: `github_adapter/auth.py`, `github_adapter/__init__.py`, `tests/github_adapter/`
- red(respx): `find_installation(repo)` 200 → `(id, account_login)`, 404 → None, JWT로 호출; 풀이 installation별 토큰을 따로 받고 캐시; 팩토리가 `installation_id` 인자로 그 installation 토큰을 쓴다, 없으면 env.
- gate: `make check`.

### P9.9 프로젝트별 installation 실행 경로 (0.5d)
- owned_paths: `control_plane/api/app.py`, `control_plane/runtime.py`, `control_plane/repo_cache.py`, `control_plane/pr_opener.py`, `control_plane/orchestrator/**`의 GitHub 호출부, 해당 tests
- red: installation이 다른 두 프로젝트의 clone URL·REST 호출 Authorization이 각자 토큰; `installation_id` 없는 프로젝트는 env 경로(회귀 없음).
- gate: `make check`.

### P9.10 연결 API (0.5d)
- owned_paths: `control_plane/api/projects.py`, `control_plane/api/deps.py`, `control_plane/events/schema.py`(필드 추가만), `control_plane/events/projection.py`, `github_adapter/app_check.py`, `github_adapter/client.py`(초기 커밋), tests
- red: 미설치 repo 400 `app_not_installed` + `install_url`; 외부 설치 repo는 데모 모드에서도 admin token 없이 201, members=[installation 계정 owner]; `project.created.installation_id` → `projects.installation_id`; 빈 repo는 400(점검 실패 사유 그대로, 초기 커밋은 취소 — §6); `run_check`가 탐지한 installation을 쓰고 Discussions/Plans는 경고.
- gate: `make check`.

### P9.11 Plan Issue fallback + 콘솔 2단계 (0.5d)
- owned_paths: `control_plane/orchestrator/**`의 Plan 게시부, `github_adapter/webhooks.py`·`control_plane/api/approvals.py`(Issue `/approve` 매칭), `control_plane/api/static/*`, tests
- red: Plans 카테고리 없음 → Plan이 마커 달린 Issue로(멱등), `plan_discussion_url`에 Issue URL, 그 Issue의 `/approve`로 승인; 콘솔에 App 설치 링크 → repo 연결 2단계.
- gate: `make check` + 시크릿 창 리허설(두 번째 GitHub 계정, llm=openai).

### PC-9 심사자 워크스루 (0.5d, 09-19)
- 자동: `make check`, `scripts/check_runbook.sh`, `curl -I https://foreman.antaewoo.com/` 200, `/health`, WS 연결
- 사람(시크릿 창, 가능하면 다른 네트워크): (1) `/` 로드, showcase의 Plan·Issue·merged PR 링크 → GitHub (2) 예시 Goal 생성 → draft→planning→awaiting ≤ 3분 (3) Plan 본문 = Discussion (4) Approve → Issue 링크 (5) 워커 → draft PR 링크 (6) 소유자 GitHub 머지 → 웹훅 → Task done이 새로고침 없이 갱신 (7) 두 번째 Goal 즉시 → 429 (8) POST 11회 → 429 (9) `curl -X POST /projects` 401 (10) `systemctl restart` 뒤 awaiting Goal 복원·Approve 202 (11) App "Redeliver ping" 200 (12) `journalctl`에 토큰·키 0
- pass: 자동 전부 + 사람 12개. 기록 `docs/pc/PC-9.md`.

---

## 8. 후속 (이 로드맵 밖, PC-5 이후)

| ID | 내용 | 비고 |
|---|---|---|
| X.1 | **실 GitHub 연결** — 남은 것: GitHub App 체크리스트·`.env` 실값, App 토큰으로 `RepoCache` clone·워커 push 인증(`WORKER_TOKEN`, D-38), 웹훅 구독(`issue_comment`·`discussion_comment`·`pull_request`), `sample_repo`를 테스트 repo에 push 후 `HITL_DRY_RUN=false`로 Goal 1개, `scripts/cleanup_repo.py`. (P6에서 끝난 것: 상주 프로세스, PR 생성 위치, discussion_comment 매핑, 대기 복원, Dry 머지) | 설계 §15 MVP 1 DoD의 진짜 판정 |
| X.2 | 프롬프트 튜닝 (Task 크기 30분~2시간) | **1차 완료 — 로컬 판정** (2026-09-13, f6a3458). 원칙(사용자): 프롬프트를 개별로 손대지 않고 세트 단위로 바꾸고 Goal 입력부터 브랜치까지 e2e로만 판정. 14b 4회: 분해 개선, 코딩은 '전역 store import' 한 종류 실패 반복 → 최종 판정은 §6 블로커(Anthropic 크레딧) 해소 후. 기록 `docs/pc/X-2.md` |
| X.3 | 설계 변경 절차 예: `awaiting_rebase` | `docs/design.md` diff 먼저 |
| X.4 | MVP 2 진입 — CLAUDE.md "현재 단계" 갱신, Review Agent부터 | 이 파일에 P6~ 추가 |

## 9. 환경 메모 (1차 실행에서 확인, 저장소 밖 사실)

- Docker 소켓은 `root:docker`. 새 셸에 그룹이 반영 안 되면 `echo "<cmd>" | newgrp docker`로 실행 (`sg` 없음, sudo는 비밀번호 필요).
- 호스트 9000/9001은 다른 프로세스가 점유 → `MINIO_HOST_PORT=9100 MINIO_CONSOLE_HOST_PORT=9101 make docker-up`. 5432/6379는 비어 있음.
- 실 LLM 자격 증명(`HITL_ANTHROPIC_API_KEY`/`ANTHROPIC_API_KEY`/`ant auth`)은 사용자가 준다. 없으면 PC-3/PC-5 실 LLM 항목은 `[막힘]`으로 묻는다.
- ruff E501은 한글을 폭 2로 센다. 파이프 뒤 종료 코드는 make의 것이 아니다(§0.3).
- `agents/`·`worker/`에서 `control_plane.events.schema`·`control_plane.store.enums` import는 허용(가드는 `control_plane.config`만 막는다).
- `make test`는 진짜 Redis가 필요하다(D-32): `echo "docker compose up -d --wait redis" | newgrp docker` 후 실행. 테스트는 DB 15를 쓰고 매번 FLUSHDB 한다.
