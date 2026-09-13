# PC-4 — 브랜치 3개 push (D-34: Ollama 실 코드 생성)

- 일시: 2026-09-13 16:10–16:45 KST
- 판정: **pending** — 자동 항목 중 `pc4_run_tasks.py`(Ollama)가 한 번의 실행에서 3/3에 못 미침(최고 2/3). 나머지 자동 항목 전부 pass. 사용자 판정 필요(아래 선택지).
- provider: D-34에 따라 로컬 Ollama `qwen2.5-coder:7b`(`openai_compat`). `--fake`는 스모크 전용.

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (383 tests) |
| `make test-integration` (Docker: Postgres, worker 컨테이너) | pass (5) |
| `scripts/pc4_run_tasks.py --fake` | PASS — 브랜치 3, would open_pr 3, pr.opened 3, verify_chain True, projection_error 0 |
| `scripts/pc4_run_tasks.py` (Ollama, 최종 코드 기준 2회) | **FAIL 2/3, 2/3** — 5차: T-101·T-103 done, T-102 blocked(모델 테스트 오류 + 파서 버그→수정) / 6차: T-102·T-103 done, T-101 blocked(모델이 쓴 테스트가 `delete` 뒤 `KeyError`를 기대). 브랜치 3/3 push·트레일러 OK, verify_chain True, projection_error 0. 전체 출력 `PC-4-ollama-output.txt` |
| Task별 단독 실행(`--only 1`) | T-101 done 2/2 (블록 형식 전환 후) |

파이프라인 자체(Scheduler 배정 → 프로세스 내 워커 → 미서명 XADD → ingest/서명 → projection → 브랜치 push → Dry PR → 체인 검증)는 6회 실행 모두 끝까지 돌았다. 못 미친 것은 7B 모델의 편차뿐이다: 각 Task는 어느 실행에서든 통과했고(T-101 4·5차, T-102 3·6차, T-103 3·5·6차), 실패 원인은 모델이 쓴 테스트의 잘못된 기대값 또는 지시 무시(`from src.app…`)였다.

## Ollama 실행에서 발견한 플랫폼 결함 (전부 수정, ROADMAP §6 (기록) PC-4)

| # | 결함 | 수정 | 커밋 |
|---|---|---|---|
| 1 | pytest가 남긴 `__pycache__/*.pyc`를 `git add -A`가 커밋 | shell 툴 env `PYTHONDONTWRITEBYTECODE=1` + 테스트 | c728123 |
| 2 | 편집 프롬프트 관련 파일이 owned 파일뿐 → 새 파일 Task는 기존 API·import 관례를 못 봄(`store.users`, `from src.app` 환각) | `related_files` = owned + spec 언급 경로 + owned 디렉토리 형제 | c728123 |
| 3 | edit 노드가 CONTEXT.md 없는 원본 input으로 컨텍스트 조립 | plan과 같은 컨텍스트 | 9518095 |
| 4 | **Scheduler**: 재배정 뒤 이전 run의 `run.finished`가 슬롯을 비워 같은 Task 3중 배정(워커 3개, `running→assigned` projection_error) | 현재 run memo, 오래된 종료 무시 + 테스트 | 1f1c460 |
| 5 | JSON EditPlan 안의 코드에서 7B 모델이 `"""`·`@dataclass`·빈 줄을 잃음(T-101 3회 연속 실패) | 편집 응답을 파일 블록 텍스트로(`parse_edit_plan`, JSON 폴백, 느슨한 마커) | 9518095 |
| 6 | 스크립트가 테스트와 같은 Redis DB 15(FLUSHDB 충돌) | DB 14 | 9518095 |

## 사람 항목

- `git -C /tmp/pc4-6w14x6m2/remote.git log --all --oneline` — 6차 실행 remote(재부팅 전까지 유효). 브랜치 `ai/users-api/{101,102,103}-…`, 각 커밋에 `Task #<n> / Run <id>` 트레일러. 생성 diff는 `PC-4-ollama-output.txt` 끝부분.

## 판정 선택지 (사용자)

1. **pass (조건부)** — 파이프라인 검증 완료, 모델 편차는 D-33대로 PC-5(Anthropic)에서 최종 판정. P5 진행.
2. Ollama에서 계속 튜닝 — 후보: `temperature=0`(현재 provider가 안 보냄), 더 큰 로컬 모델(`qwen2.5-coder:14b`), 편집 프롬프트에 "테스트는 spec의 기대값만 검증" 추가. 실행당 ~1분.
3. fail — Anthropic 크레딧 충전 후 재검사까지 P5 보류.
