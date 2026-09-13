# MVP 1 (dev) 포스트모템 — 2026-09-13

범위: ROADMAP v2 P0~P5 + PC-0~PC-4 (PC-5는 `docs/pc/PC-5.md`). 한 세션, 빈 저장소에서 시작.

## 숫자

| 항목 | 값 |
|---|---|
| 커밋 | 134 (feat/fix 32, 나머지 test/chore/docs — Red→Green→Refactor→Gate 흔적) |
| 코드 / 테스트 | 7,996 / 7,454 줄 (테스트가 코드의 93%) |
| 테스트 | 401 unit (진짜 Redis) + 5 integration (Docker: Postgres, 워커 컨테이너) |
| 결정 | D-01~D-35 (35), §6 (기록) 16건 |
| 실 LLM | Ollama `qwen2.5-coder:7b` (Anthropic 크레딧 부족 → D-33/D-34/D-35). PC-3 2호출 2.2k/1.0k, PC-4 Task당 3호출 ~2.4k/0.7k 토큰 |

## 잘 된 것

1. **이벤트 소싱 뼈대가 끝까지 버텼다.** outbox → Redis Streams → projection(유일한 DB 갱신) + 해시 체인.
   PC-1 왕복, P0–P2 e2e, PC-4 실 실행 6회, e2e 스크립트까지 `verify_chain` 실패 0회. `seq` 커서(D-29)와
   `canonical_json` 서명 덕에 sqlite/Postgres 차이가 체인에 스며들지 않았다.
2. **동결 전 스키마 리뷰(D-25~D-31)가 값을 했다.** 사용자 리뷰 9건이 잡은 것(canonical_json 컬럼, ingest 락,
   D-30 재시도 큐, tool_called 분리, task.started 순서, epic.activated 발행자)은 전부 뒤 Plan에서 실제로 쓰였다.
3. **실 모델로 돌린 PC가 가짜로는 못 잡는 결함을 잡았다.** PC-4에서 6건(§6 (기록) PC-4): `__pycache__` 커밋,
   관련 파일 부족, edit 컨텍스트 누락, Scheduler 중복 배정, JSON 안 코드 손상, Redis DB 충돌. 특히 Scheduler
   버그(재배정 뒤 이전 run의 종료가 슬롯을 비움)는 FakeProvider 경로에서는 절대 안 나온다.
4. **DryRun 강제.** MVP 1 전 구간 실 GitHub 호출 0 (`assert_all_mocked=True`, `DRY_RUN=true` 기본, 워커의
   GitHub 쓰기는 항상 Dry).

## 안 된 것 / 비용

1. **7B 로컬 모델의 편차.** 같은 Task가 실행마다 1/3 확률로 실패(모델이 쓴 테스트의 잘못된 기대값, 지시 무시).
   PC-4는 D-35로 "간단한 수준"에서 통과시켰고, 기존 파일을 크게 고치는 Task는 PC-5(Anthropic)로 넘겼다.
   서비스는 Claude API 위에서 돌 예정이라 최종 판정은 거기서.
2. **편집 응답 형식을 한 번 바꿨다.** JSON EditPlan → 파일 블록 텍스트(`parse_edit_plan`). 작은 모델이 JSON
   문자열 안의 `"""`·`@dataclass`·빈 줄을 잃는 문제. Anthropic에서도 같은 형식을 쓰므로 PC-5에서 재확인.
3. **의존 Task는 사람 머지 전엔 안 돈다.** §6.1대로 `done`은 `pr.merged`에서만 — e2e 스크립트가 Dry PR을
   자동 머지해 풀었다(§6 (기록) P5.4). 실 운영에서는 Review Agent(MVP 2) 전까지 사람이 병목.
4. **게이트 재시도의 대부분이 E501.** ruff가 한글을 폭 2로 세어 거의 모든 Task에서 2~3회 재시도. 자동
   줄바꿈이 docstring에는 안 먹는다 — 도구 문제이지 설계 문제는 아님.
5. **Anthropic 미검증.** 크레딧 부족으로 PC-3/PC-4/PC-5 실 LLM 항목이 전부 Ollama. `HITL_LLM_PROVIDER=
   anthropic`로 바꾸면 코드 변경 없이 같은 스크립트가 돈다(D-33).

## 다음 (ROADMAP §8)

- **X.1 실 GitHub 연결** — App 체크리스트, `discussion_comment` 웹훅(지금은 `issue_comment`만 슬래시 명령).
- **X.2 프롬프트 튜닝** — PC-3 약점(중복 Task, 빈 depends_on), PC-4 약점(모델이 쓴 테스트 기대값).
  후보: "테스트는 spec의 기대값만 검증", 기존 심볼 열거 강제, temperature 0.
- **MVP 2** — Review Agent(자동 머지 판단), `/changes` 재계획(B6), Idempotency 저장소를 프로세스 밖으로,
  WS 인증, `awaiting_rebase`(X.3).
