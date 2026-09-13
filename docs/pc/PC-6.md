# PC-6 — 상주 프로세스 + API로 Goal → 브랜치 → done 완주

- 일시: 2026-09-13 21:00–22:30 KST
- 판정: **pending — 사용자 판정** (파이프라인 항목 전부 pass, "Task 전부 done"만 7B 모델 한계로 미달)
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
| `scripts/pc6_via_api.py` 4차 (git 머지 후) | FAIL — Task 5 생성, 1번이 `scope_violation`×3: 분해가 owned_paths를 `src/math/`(디렉토리)로 줬는데 매처가 하위 파일을 소유로 안 봄 → **플랫폼 버그**, 수정(aa89d2f). 이 실행에서 `Scheduler.ingest`의 순서 역전 예외(→ 컨슈머 실패·30초 재전달)도 잡아 D-30 재시도 큐로 수정(3409a6a) |
| `scripts/pc6_via_api.py` 5차 (두 수정 후) | **파이프라인 pass / 완주 미달** — Task 3 생성, T1 done: 워커 push → `PrOpener` PR #1 → `DryMerger`가 git `main`을 ff(`bdd12c1`) → `pr.merged` → done → 의존 T2 배정. T2(테스트 작성)가 `from maths_helpers import`(잘못된 import, 7B)로 3회 실패 → blocked, T3는 T2 의존으로 대기 → 스크립트 종료 |

## 1차 실행에서 잡은 것 (전부 수정·§6 기록)

1. Dry 머지가 git `main`을 안 옮김 → `DryMerger`가 repo의 `refs/heads/<base>`를 ff/worktree 머지로 옮긴 뒤 `pr.merged`. 충돌이면 PR을 `in_review`에 둔다.
2. API 201/202 직후 GET 404 — 읽기 지연(§17 3). 스크립트가 projection 반영을 폴링.
3. 같은 repo 경로로 프로젝트 2개 → 웹훅 `resolve_project`가 첫 행 → 실행마다 고유 workdir.
4. API 재시작 시 `runner.restored_waiting`이 이전 Goal을 복원(P6.2 실증).
5. 환경: 이 머신의 Ollama는 다른 워크로드의 27GB 모델과 공유 → 백그라운드 작업이 메모리 부족으로 두 번 종료. 7b로, 하네스 밖(setsid)에서 실행.
6. **ingest 순서 역전이 컨슈머를 죽임**: 프로세스 내 워커가 `task.started`를 즉시 XADD 하는데 `task.assigned`는 아직 relay·projection 전 → `Scheduler.ingest`의 `ready→running` 불허 전이가 예외로 새어 체인 전체 실패(ack 없음, 30초마다 재전달). `Projection.apply_or_retry`(D-30 재시도 큐)로 감쌈. 스크립트 pump에서는 relay를 먼저 돌려 숨어 있던 경쟁.

## 판정 근거

PC-6의 목적은 "스크립트가 컴포넌트를 조립하지 않아도 상주 프로세스 + API만으로 Goal → 브랜치 → done이 되는가"다.
5차 실행에서 그 경로는 전부 실제로 돌았다: `POST /projects` → projection 반영 → `POST /goals` → GoalRunner(Ollama Plan)
→ `awaiting_plan_approval` → 서명된 `discussion_comment` `/approve` → 분해 → Scheduler 배정 → InProcessLauncher 워커
→ push → `PrOpener` → `DryMerger`(git main 이동) → done → 의존 Task 배정. 남은 실패는 7B 모델이 쓴 테스트의 import 오류.
"Task 전부 done" 기준은 모델 품질에 걸려 있으므로 D-40/D-35 취지대로 사용자 판정으로 남긴다.

**후속 과제(코드)**: blocked Task의 후속이 영원히 `ready`로 남는다(§9.2 데드락 감지 미구현). 지금은 스크립트가 60초
정체를 감지해 종료한다. `task.retried`(blocked→ready) 발행 API 또는 후속 cascade-block이 필요 — MVP 2.

## 사람 항목

- 5차 repo: `git -C /tmp/pc6-mmj4ownb/repo log --all --oneline` (`main`에 `bdd12c1` = T1이 머지된 것).
