# PC-1 — 이벤트 한 바퀴 + 스키마 동결

- 일시: 2026-09-13 13:45 KST
- 판정: **pass**
- 태그: `event-schema-v1` (이 커밋). 이후 `control_plane/events/schema.py`는 **추가만** 가능.

## 자동 항목

| 항목 | 결과 |
|---|---|
| `docker compose up -d --wait redis postgres` + `uv run alembic upgrade head` | ok (MinIO는 이 PC에서 안 씀) |
| `scripts/pc1_roundtrip.py` — 36 이벤트 발행(체인 30 + tool_called 6) → relay → projection consumer 36 처리, pending 0 | ok |
| Goal active(rev 1), Epic active, Task 3행 done, Run 3행 success, tool_call_count [2,2,2], projection_error 0, 미투영 0 | ok |
| `verify_chain_db` True | ok |
| TRUNCATE tasks/runs/epics/goals/tool_calls → replay 30(seq 순) → apply(force) 30 → 스냅샷 동일 | ok |
| `make check` | pass (179 tests, 진짜 Redis) |
| `make test-integration` | pass (3, Postgres 트리거·JSONB 왕복) |

## 발견 사항과 조치

1. **run.tool_called이 relay보다 먼저 도착한다.** D-31로 tool_called는 publish 시점에 바로 XADD 되고 체인 이벤트는 outbox relay를 기다리므로, projection이 run.started보다 tool_called를 먼저 본다. 1차 실행에서 OrderingError → retry 스트림 → retry가 `events` 테이블에서 재로드 실패(체인 밖 행은 없음) → `tool_call_count` 0. **조치**(커밋 fefffb3): tool_called 핸들러는 Run이 없으면 건너뛰고, run.started/run.finished가 tool_calls 행 수를 재계산. 체인 밖 이벤트는 retry 스트림에 넣지 않는다. 회귀 테스트 `test_tool_called_before_run_started_is_tolerated`.
2. `tool_call_count`는 TRUNCATE tool_calls 후 replay로 복원되지 않는다(설계상 tool_calls는 감사 체인 밖). 스냅샷 비교에서 제외. 감사 체인이 보장하는 것은 `events`에 있는 30건이다.
3. relay 카운트는 다른 project의 미발행 행(이전 통합 테스트 잔여)이 섞일 수 있어 project 스트림 길이로 판정한다.

## 사람 항목

- 태그와 이 문서는 ROADMAP 허용대로 에이전트가 대행했다. 동결 대상: EventType 44개(G절 목록), 봉투 필드(id, project_id, ts, actor{type,id}, type, subject{entity,id}, payload, correlation_id, causation_id: str|None, signature: str|None), PAYLOAD_TYPES 14개의 필수 키.

## 실행 로그 (요약)

```
1. published 36 events (30 chained, 6 tool_called)
  [ok] 2. relayed 30; stream len 36 == 36
  [ok] 3. consumer handled 36 == 36 / no pending
  [ok] 4. goal ('active', 1, 1) / epic active / tasks done ×3 / runs success ×3 / tool_call_count [2, 2, 2]
  [ok] 4. events rows 30, tool_calls rows 6 / projection_error 0 / unprojected 0
  [ok] 5. verify_chain_db True
  [ok] 6. replayed 30 → force-applied 30 → snapshot equal
PC-1 roundtrip: PASS
```
