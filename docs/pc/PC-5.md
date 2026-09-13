# PC-5 — MVP 1 (dev) 완료 판정

- 일시: 2026-09-13 18:30 KST
- 판정: **pending** — 자동 항목 중 "실 LLM e2e"가 Anthropic이 아닌 Ollama(7B, 14B)로만 실행됐고(크레딧 부족, D-33)
  두 결과 모두 기준 미달(14B는 코딩은 나아졌으나 분해가 Task 2개). 사람 항목 (1)~(3)은 사용자 판정 필요. Anthropic 크레딧이 생기면
  `.env`의 `HITL_LLM_PROVIDER=anthropic`으로 바꾸고 같은 명령을 다시 돌린다(코드 변경 없음).

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (401 tests, mypy 63 files) |
| `make test-integration` (Docker) | pass (5) |
| `scripts/e2e_dry_run.py --fake` | PASS — Plan → 자동 승인 → TaskDraft 2 → would create issue ×2 → Coding done ×2 → 브랜치 2, 8 LLM 호출 |
| 실 LLM: `scripts/e2e_dry_run.py tests/fixtures/sample_repo "Add a /users CRUD endpoint with tests"` (Ollama `qwen2.5-coder:7b`) | **FAIL** — Task 8 (≥3 ✓), 브랜치 1 (≥3 ✗), would open_pr 0 (≥3 ✗). Task 1 "Extend User model"이 3회 실패 → blocked, 나머지 7개는 전부 Task 1에 의존해 배정되지 않음. 토큰: orchestrator 2호출 2316/1281, coding 4호출 5169/818. 전체 출력·diff: `PC-5-ollama-output.txt` |

| 실 LLM 2차: 같은 명령, `--model qwen2.5-coder:14b` (RTX 3060 12GB, 9GB VRAM, 사용자 요청) | **FAIL** — Task 2 (≥3 ✗), 브랜치 2, would open_pr 1. Task 1 "Define User CRUD Routes" done(1회차), Task 2 "Implement GET /users"는 코드는 맞는데 모델이 쓴 테스트가 `create_app()`의 전역 store와 새 `UserStore()`를 섞어 3회 실패. 토큰 orchestrator 2383/687, coding 7호출 7981/3302. 호출당 ~40s |
| 참고: `pc4_run_tasks.py --tasks hard --model qwen2.5-coder:14b` | 2/3 — 7B가 못 하던 `UserStore.update/delete`(기존 파일 수정)는 1회차 통과, users 모듈은 테스트의 공유 store id 가정으로 3회 실패 |

실 LLM 실패 분석 (7B 편차, PC-3/PC-4와 같은 결함):
- 분해: `User`/`UserStore`가 이미 `src/app/models.py`에 있는데 "Extend User model", "Implement UserStore" Task를 만들었고(PC-3 약점 (a) 재현), 8개 Task를 전부 Task 1에 직렬로 묶었다(owned_paths 겹침 직렬화 + 빈 depends_on 보정).
- 코딩: 모델이 새 파일 `src/app/user.py`에 별도 `User`(username/email)를 만들고 기존 `tests/test_main.py`에 존재하지 않는 `UserStore.store()`를 호출하는 테스트를 추가 → 기존 테스트까지 깨짐. 지시("기존 파일 심볼 재사용")를 무시하는 7B 특성.
- 파이프라인 자체는 완주(체인 검증 True, projection_error 0, 브랜치 push·트레일러 OK).

## 사람 항목 (사용자 판정 필요)

| # | 기준 | 에이전트 사전 검토 | 판정안 |
|---|---|---|---|
| 1 | 브랜치 3개 diff 중 2개 이상이 사람 수정 없이 머지 가능 | Ollama 결과로는 브랜치 1개뿐이고 그것도 기존 테스트를 깨뜨림 → **불충족**. PC-4(D-35 간단 Task) 결과로는 3/3이 머지 가능한 수준(`PC-4-ollama-output.txt` 끝부분 diff). Anthropic 재실행 전엔 판정 보류 권고 | pending |
| 2 | 분해 품질 약점 vs 에이전트 자체 평가 | 약점: (a) 기존 심볼 중복 Task (b) depends_on 비움 → 코드가 직렬화로 보정해 병렬성 0 (c) "Refactor to Blueprints (optional)" 같은 목표 밖 Task. 자체 평가: Plan 6섹션·AC 8개는 양호, Task spec은 파일 경로·검증 방법 포함. X.2 프롬프트 튜닝 후보 그대로 유효 | 사용자 확인 |
| 3 | 토큰 비용 → MVP 2 예산 | Ollama 기준 Goal 1개 = orchestrator ~3.6k + Task당 ~3–6k 토큰(성공 3호출, 실패 4호출). Anthropic claude-opus-5 가격으로 환산하면 Task당 수 센트 수준(실측은 Anthropic 실행 후). MVP 2 예산 제안: Goal당 $5 상한, Task당 $1 경고(`budget.warning`) | 사용자 확인 |

## 남은 일

1. Anthropic 크레딧 → `HITL_LLM_PROVIDER=anthropic` → 위 실 LLM 명령 재실행 → 이 문서 갱신 + 사람 (1) 판정.
2. `docs/postmortem/mvp1.md` 작성 완료(이 커밋). ROADMAP §8 X.1(실 GitHub), X.2(프롬프트 튜닝), X.4(MVP 2)는 사용자 결정.
