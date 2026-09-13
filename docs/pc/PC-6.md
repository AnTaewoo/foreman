# PC-6 — 상주 프로세스 + API로 Goal → 브랜치 → done 완주

- 일시: 2026-09-13 21:00–22:30 KST
- 판정: _(실행 결과 기입)_
- 구성: `python -m control_plane`(`HITL_WORKER_LAUNCHER=inprocess`, relay+projection+scheduler+retry+PrOpener+DryMerger) +
  `uvicorn control_plane.api.app:app --factory`(:8765) 두 프로세스, Postgres `pc6` DB, Redis DB 13. 스크립트
  `scripts/pc6_via_api.py`는 HTTP·git만 쓴다. LLM은 로컬 Ollama `qwen2.5-coder:7b`(RAM 사정, D-35 수준 Goal).
- Goal: "Add a maths helpers module with add and mul functions and tests" (`tests/fixtures/sample_repo`)

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (440 tests) |
| `make test-integration` (Docker) | pass (6, `test_launcher.py` 포함 — DockerCliLauncher 실제 기동) |
| `scripts/check_runbook.sh` | 5/5 |
| `scripts/e2e_dry_run.py --fake` | PASS (runbook block 4) |
| `scripts/pc6_via_api.py` 1차 (git 머지 전) | **FAIL** — Task 4/4 생성, 3 done, 1 blocked: `pr.merged`를 이벤트로만 흉내 내서 Task 4가 선행 코드 없는 `main`을 clone(§6 (기록) PC-6 (1)). 파이프라인 자체(배정→워커→PrOpener→DryMerger→done→의존 Task 배정)는 완주 |
| `scripts/pc6_via_api.py` 최종 (git 머지 후) | _(기입)_ |

## 1차 실행에서 잡은 것 (전부 수정·§6 기록)

1. Dry 머지가 git `main`을 안 옮김 → `DryMerger`가 repo의 `refs/heads/<base>`를 ff/worktree 머지로 옮긴 뒤 `pr.merged`. 충돌이면 PR을 `in_review`에 둔다.
2. API 201/202 직후 GET 404 — 읽기 지연(§17 3). 스크립트가 projection 반영을 폴링.
3. 같은 repo 경로로 프로젝트 2개 → 웹훅 `resolve_project`가 첫 행 → 실행마다 고유 workdir.
4. API 재시작 시 `runner.restored_waiting`이 이전 Goal을 복원(P6.2 실증).
5. 환경: 이 머신의 Ollama는 다른 워크로드의 27GB 모델과 공유 → 백그라운드 작업이 메모리 부족으로 두 번 종료. 7b로 실행.

## 사람 항목

- `git -C <workdir>/repo log --all --oneline` (경로는 실행 출력 `repo=`).
