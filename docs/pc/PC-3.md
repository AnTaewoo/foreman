# PC-3 — 그래프 완주 + 눈검사

- 일시: 2026-09-13 16:40 KST (1차 16:05는 Anthropic 크레딧 부족으로 pending)
- 판정: **pass** — 자동 전부 pass, 사람 3항목 검토안에 사용자 서명(2026-09-13, 2번 조건부)
- provider: D-33에 따라 로컬 Ollama `qwen2.5-coder:7b` (`openai_compat`). Anthropic 최종 판정은 PC-5.

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (298 tests) |
| `scripts/pc3_plan_dryrun.py --fake …` | PASS (interrupt → auto-approve → Plan 6섹션 → TaskDraft 4 → Dry Issue 4) |
| `scripts/pc3_plan_dryrun.py tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests"` (Ollama) | PASS — 호출 2회, 토큰 in 2223 / out 1000, Plan 6섹션, TaskDraft 8, Dry Issue 8, 이벤트 `goal.plan_proposed → goal.activated → epic.created×2 → task.created×8` causation 체인. 전체 출력: `PC-3-ollama-output.txt` |
| Anthropic 경로 | 400 "credit balance is too low" (키 인증 통과, 잔액 0) — PC-5에서 재시도 |

## 사람 항목 — 에이전트 사전 검토 (사용자 서명 필요)

| # | 기준 | 검토 | 판정안 |
|---|---|---|---|
| 1 | Plan §5.2 6섹션 | Understanding / Acceptance Criteria(5) / Epics(2) / Task Graph / Decisions Expected(none) / Budget 모두 있음. 결함: 모델이 "AC-1:" 접두를 넣어 "AC-1 AC-1:"로 중복 렌더링 → 커밋 91e4ed7에서 렌더러가 접두를 떼도록 수정 | pass |
| 2 | Task ≥ 3, spec만 보고 구현 가능 | 8개, 전부 파일 경로·메서드·검증 방법이 spec에 있음. **약점**: (a) "Create user data model"·"Create UserStore in-memory store"는 이미 `src/app/models.py`에 있는 `User`/`UserStore`와 중복(요약의 CONTEXT.md에 적혀 있는데 7B 모델이 무시) (b) "Refactor … users.py"는 목표에 불필요 (c) `depends_on`을 하나도 안 채움 (d) `epics: []`로 반환(emit이 task.epic으로 복원) | pass (조건부: 약점은 X.2 프롬프트 튜닝 과제) |
| 3 | owned_paths 겹침 없음/직렬화 | 원본은 5개 Task가 `src/app/main.py`, 4개가 `tests/test_main.py`를 공유 → `serialize_overlaps`가 입력 순으로 직렬화(#3 GET → #5 POST → #6 PUT → #7 DELETE → #8 refactor, #4 tests-model은 겹침 없어 앞으로). 사이클 없음 | pass |

에이전트 판정안: **3/3 pass (2번은 조건부)**. 2/3 이상이므로 P3.3 프롬프트 튜닝 재검사는 필요 없음(ROADMAP 기준). 약점은 §8 X.2로.

## X.2 프롬프트 튜닝 후보 (PC-3 결과 기반)

1. decompose.md: "요약에 이미 있는 클래스/파일을 새로 만들지 말 것 — CONTEXT.md·README의 심볼을 먼저 열거"를 명시.
2. decompose.md: `depends_on`을 비우면 거부하도록 예시에 의존을 넣고, "같은 파일을 만지는 Task는 반드시 depends_on" 강조(현재는 코드가 직렬화로 보정).
3. plan.md: Task Graph를 "T-n" 형식으로 강제(현재 "CRUD Epics → …"처럼 모호).
4. Anthropic(claude-opus-5)로 같은 goal을 돌려 7B 결과와 비교 — PC-5.
