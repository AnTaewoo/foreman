# PC-3 — 그래프 완주 + 눈검사

- 일시: 2026-09-13 16:05 KST
- 판정: **pending** (실 LLM 항목이 계정 크레딧 부족으로 실행 불가)

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (292 tests) |
| `scripts/pc3_plan_dryrun.py --fake tests/fixtures/sample_repo "goal"` | PASS — interrupt at `wait_plan_approval` → auto-approve → Plan 6섹션 → TaskDraft 4 → Dry Issue 4, 이벤트 `goal.plan_proposed → goal.activated → epic.created → task.created×4` causation 체인, 토큰 in=1454 out=365(fake 추정) |
| 실 LLM: `uv run python scripts/pc3_plan_dryrun.py tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests"` | **미완** — `anthropic.BadRequestError 400 invalid_request_error: "Your credit balance is too low to access the Anthropic API"` (request_id req_011CeztUzY46JRokb27hBTCw). 키 인증은 통과, 잔액 0 |

## 사람 항목 (실 LLM 출력 대상 — 미실행)

1. Plan §5.2 6섹션 — 미검사
2. Task ≥ 3, spec만 보고 구현 가능 — 미검사
3. owned_paths 겹침 없음/직렬화 — 미검사

## 필요한 조치

- Anthropic 콘솔 Plans & Billing에서 크레딧 충전(또는 잔액 있는 다른 키를 `.env`의 `HITL_ANTHROPIC_API_KEY`에) 후 위 명령 재실행.
- 또는 ROADMAP §0.1-8대로 `pass (조건부)`: 실 LLM 검사 3항목을 PC-5로 이월하고 P4를 시작. P4(Coding Agent·Worker·Scheduler)는 FakeProvider로 개발·검증되므로 LLM 크레딧이 없어도 진행 가능하다.
