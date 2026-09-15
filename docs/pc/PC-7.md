# PC-7 — 실 GitHub에서 Goal 1개 (X.1)

- 일시: 2026-09-14 20:20 ~ 2026-09-15 19:15 KST
- 판정: **pass — 자동 전부 + Task 1개가 사람 머지로 done** (사용자 서명·cleanup 대기)
- 대상: App `foreman-antaewoo` (id 4940123), installation 161606086, repo `AnTaewoo/foreman_test` (private)
- 구성: `python -m control_plane`(inprocess 런처, Postgres `pc7`, Redis DB 12) + `uvicorn …:app`(:8000) + smee 터널.
  `HITL_DRY_RUN=false`는 환경변수로만(`.env`는 그대로 true). LLM 로컬 Ollama `qwen2.5-coder:7b`.

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (459) |
| `scripts/github_app_check.py --repo AnTaewoo/foreman_test` | PASS (7/7; `check_suite`는 선택 — Checks 권한 없으면 안 보임) |
| `scripts/seed_test_repo.py` | `main` @ 324f637 |
| Goal → Plan Discussion | **Discussions #2** "Plan #1: Add a maths helpers module…" (카테고리 Plans) |
| `/approve` (discussion_comment 웹훅, smee) | 202 → `goal.activated` → 분해 Task 4 → 라벨 22·Milestone 1·**Issue #3~#6** 생성 |
| 워커 → push → PR | control plane이 토큰으로 push(D-41) → **PR #7**(draft, Issue #3) + Issue 요약 코멘트 |
| 사람 머지 → done | PR #7 머지 → `pull_request.closed` 웹훅 → `pr.merged` → Task #3 **done** → 의존 Task #5 자동 배정 → **PR #8** |
| 체인 | 이벤트 47건, `verify_chain_db` True |

최종 Task: #3 done(PR #7 merged) / #5 in_review(PR #8) / #6 ready(#5 대기) / #4 blocked(7b 분해가 owned_paths를
`tests/tests_maths.py`로 오타 → 모델의 `tests/test_maths.py` 쓰기를 툴이 3회 거부 = `run.tool_denied` 3, 설계대로).

## 실행 중 발견·수정

1. **API 쪽 clone에 토큰 없음** — 첫 Goal이 `repo_unavailable`로 취소. `GoalRunner`가 토큰을 미리 갱신하고 RepoCache(토큰)로 clone, `build_runner`가 `make_token_provider` 배선 (2b0b107).
2. **`check_suite` 구독 불가** — Checks 권한이 없는 App엔 목록에 안 보임. MVP 1은 `pr.checks_*` noop이라 선택으로 (aa6825a).
3. **App 웹훅 Active 꺼짐** — 코멘트가 GitHub 배달 기록에 아예 없었음. ping 재전송은 Active와 무관하게 나가서 터널은 정상으로 보였다. 점검 스크립트로는 알 수 없는 항목(API에 `active` 없음) → runbook에 체크리스트로.
4. **`pr.opened` 중복 발행** — PrOpener가 발행 + `pull_request.opened` 웹훅도 발행 → PR당 2건(4 = 2×2). projection은 멱등이라 무해. 후속: 웹훅 쪽이 `tasks.pr_number == number`면 건너뛰기(MVP 2).
5. 첫 Goal(취소)의 흔적: `goal.cancelled` 1건 — 체인엔 정상 기록.

## 사람 항목

- URL: Discussion https://github.com/AnTaewoo/foreman_test/discussions/2 · Issues #3–#6 · PR #7(merged) · PR #8(open)
- cleanup 계획(dry): issues [6,5,4,3] close, pulls [8] close, branches 2 delete → 사용자 확인 후 `--apply`.
