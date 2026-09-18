# progress

| 시각 | ID | 체크포인트 | 핵심 1줄 | 커밋 |
|---|---|---|---|---|
| 2026-09-13T10:59 | P0.1 | 착수 | 골격+툴체인+Settings+import 가드 | |
| 2026-09-13T11:00 | P0.1 | Red | test_scaffold.py 8케이스, ModuleNotFoundError control_plane | |
| 2026-09-13T11:01 | P0.1 | Green | 패키지 11개+Settings+pyproject+Makefile, 17 passed | |
| 2026-09-13T11:03 | P0.1 | 완료 | make check pass, 보드 done | 16f90dd |
| 2026-09-13T11:18 | P0.2 | 착수 | 설계 § 참조 확인 | |
| 2026-09-13T11:18 | P0.2 | 완료 | § 8개 전부 존재 | 5fcebdd |
| 2026-09-13T11:18 | P0.3 | 착수 | compose/.env.example/pre-commit/structlog/health | |
| 2026-09-13T11:18 | P0.3 | Red | test_infra.py 10케이스, .env.example/compose/logging 부재로 실패 | |
| 2026-09-13T11:19 | P0.3 | Green | compose/.env.example/logging/app/pre-commit/Makefile, 27 passed | |
| 2026-09-13T11:20 | P0.3 | 완료 | gate 3종 pass (2/3), 보드 done | 61ae633 |
| 2026-09-13T11:24 | PC-0 | 결과 | 3 서비스 healthy, /health 200, make check pass → pass | |
| 2026-09-13T11:55 | P1.1 | 착수 | schema.py: EventType 44, 봉투, canonical/sign/verify, PAYLOAD_TYPES | |
| 2026-09-13T11:56 | P1.1 | Red | test_schema.py 26케이스+파라미터, ImportError control_plane.events.schema | |
| 2026-09-13T11:57 | P1.1 | Green | schema.py: EventType 44, Event/Actor/Subject, canonical/sign/verify, PAYLOAD_TYPES 14, 110 passed | |
| 2026-09-13T11:58 | P1.1 | Gate | make check pass (110 tests), 시도 2/3 (E501 11건 정리) | |
| 2026-09-13T11:58 | P1.1 | 완료 | 보드 done | b490bfa |
| 2026-09-13T12:05 | P1.2 | 착수 | enums/models(9 tables)/transitions | |
| 2026-09-13T12:06 | P1.2 | Red | test_transitions 13 + test_models 7, ModuleNotFoundError store.enums/models/transitions | |
| 2026-09-13T12:08 | P1.2 | Green | enums 10 + transitions 4표 + models 9 tables, 137 passed | |
| 2026-09-13T12:09 | P1.2 | Gate | make check pass (137 tests), 시도 2/3 (E501 10건) | |
| 2026-09-13T12:09 | P1.2 | 완료 | 보드 done | b1979cd |
| 2026-09-13T12:14 | P1.3 | 착수 | alembic 0001/0002 + session.py + pg 통합 테스트 | |
| 2026-09-13T12:15 | P1.3 | Red | test_migrations 7 + integration 3, ImportError store.session / alembic 부재 | |
| 2026-09-13T12:18 | P1.3 | Green | alembic 0001(9 tables, pg 공유 enum)/0002(트리거) + session.py, 144 passed | |
| 2026-09-13T12:19 | P1.3 | Gate | make check pass (144) + test-integration 3 passed (pg), 시도 2/3 | |
| 2026-09-13T12:19 | P1.3 | 완료 | 보드 done | 02c7dc4 |
| 2026-09-13T13:06 | P1.4 | 착수 | chain.py/bus.py/outbox.py | |
| 2026-09-13T13:07 | P1.4 | Red | test_chain 9 + test_bus 9, ImportError events.chain/outbox | |
| 2026-09-13T13:17 | P1.4 | Green | chain/bus/outbox + 진짜 Redis 픽스처(D-32), 163 passed | |
| 2026-09-13T13:18 | P1.4 | Gate | make check pass (163, 진짜 Redis), pre-commit pass, 시도 2/3 | |
| 2026-09-13T13:18 | P1.4 | 완료 | 보드 done, D-32 기록 | 4c244bd |
| 2026-09-13T13:27 | P1.5 | 착수 | projection.py 44 핸들러 + D-30 + guard | |
| 2026-09-13T13:29 | P1.5 | Red | test_projection 12 + guard 2, ImportError events.projection | |
| 2026-09-13T13:31 | P1.5 | Green | projection.py 44 핸들러 + D-30 handle/apply_retries + schema NotRequired 수정, 178 passed | |
| 2026-09-13T13:32 | P1.5 | Gate | make check pass (178), pre-commit pass, 시도 3/3 (E501 19건→1→0) | |
| 2026-09-13T13:32 | P1.5 | 완료 | 보드 done | c32ff1f |
| 2026-09-13T13:41 | PC-1 | 결과 | roundtrip PASS, make check 179, integration 3, tag event-schema-v1 → pass | fefffb3 |
| 2026-09-13T13:47 | P2.1 | 착수 | auth.py JWT/installation token/InstallationAuth | |
| 2026-09-13T13:47 | P2.1 | Red | test_auth 7, ModuleNotFoundError github_adapter.auth | |
| 2026-09-13T13:48 | P2.1 | Green | auth.py app_jwt/InstallationTokenProvider/InstallationAuth, 186 passed | |
| 2026-09-13T13:49 | P2.1 | Gate | make check pass (186), 시도 2/3 | |
| 2026-09-13T13:49 | P2.1 | 완료 | 보드 done | 187bc62 |
| 2026-09-13T13:49 | P2.2 | 착수 | client/protocol/markers | |
| 2026-09-13T13:50 | P2.2 | Red | test_client 18, ImportError markers/client/protocol | |
| 2026-09-13T13:51 | P2.2 | Green | protocol/markers/client 7 메서드 멱등, 204 passed | |
| 2026-09-13T13:52 | P2.2 | Gate | make check pass (205), 시도 2/3 | |
| 2026-09-13T13:52 | P2.2 | 완료 | 보드 done | 9e15cd3 |
| 2026-09-13T13:52 | P2.3 | 착수 | discussions.py GraphQL (D-09 문서 재확인) | |
| 2026-09-13T13:54 | P2.3 | Red | test_discussions 7, ModuleNotFoundError discussions | |
| 2026-09-13T13:55 | P2.3 | Green | discussions.py GraphQL(create/list/comment, 스키마 SDL로 확인), 212 passed | |
| 2026-09-13T13:55 | P2.3 | Gate | make check pass (212), 시도 2/3 | |
| 2026-09-13T13:55 | P2.3 | 완료 | 보드 done | b8bc6e7 |
| 2026-09-13T13:55 | P2.4 | 착수 | webhooks.py HMAC + 6종 변환 + slash 훅 | |
| 2026-09-13T13:56 | P2.4 | Red | test_webhooks 17 + 픽스처 15, ModuleNotFoundError webhooks | |
| 2026-09-13T13:58 | P2.4 | Green | webhooks.py HMAC/6종 변환/slash 훅/dedupe/bot 무시, 229 passed | |
| 2026-09-13T13:59 | P2.4 | Gate | make check pass (229), pre-commit pass, 시도 3/3 (E501 표 폭) | |
| 2026-09-13T13:59 | P2.4 | 완료 | 보드 done | 726252f |
| 2026-09-13T13:59 | P2.5 | 착수 | dry_run.py + 팩토리 | |
| 2026-09-13T14:00 | P2.5 | Red | test_dry_run 10, ImportError dry_run/팩토리 | |
| 2026-09-13T14:01 | P2.5 | Green | dry_run.py 2 client + 팩토리, 238 passed | |
| 2026-09-13T14:01 | P2.5 | Gate | make check pass (238), pre-commit pass, 시도 2/3 | |
| 2026-09-13T14:01 | P2.5 | 완료 | 보드 done | 2069ef5 |
| 2026-09-13T14:02 | PC-2 | 결과 | adapter 59 passed, grep 상수 1건, factory Dry → pass | |
| 2026-09-13T14:12 | PC-2 | 추가 | P0~P2 e2e 통합 테스트 pass (pg+redis), make check 238, pc1 PASS | |
| 2026-09-13T14:13 | P3.1 | 착수 | agents/llm ModelProvider/Fake/Anthropic/get_provider | |
| 2026-09-13T14:13 | P3.1 | Red | test_llm 12 + conftest, ModuleNotFoundError agents.llm.fake | |
| 2026-09-13T14:14 | P3.1 | Green | agents/llm base/fake/anthropic/get_provider, 250 passed | |
| 2026-09-13T14:16 | P3.1 | Gate | make check pass (250), pre-commit pass, 시도 3/3 (E501·mypy omit) | |
| 2026-09-13T14:16 | P3.1 | 완료 | 보드 done | 4815b4a |
| 2026-09-13T14:16 | P3.2 | 착수 | context.py RepoSummary + sample_repo | |
| 2026-09-13T14:17 | P3.2 | Red | test_context 9 + sample_repo 픽스처(자체 pytest 2 passed), ModuleNotFoundError context | |
| 2026-09-13T14:18 | P3.2 | Green | context.py build_summary/render_summary + sample_repo, 260 passed | |
| 2026-09-13T14:19 | P3.2 | Gate | make check pass (260), pre-commit pass, 시도 2/3 | |
| 2026-09-13T14:19 | P3.2 | 완료 | 보드 done | 034f9ff |
| 2026-09-13T14:19 | P3.3 | 착수 | prompts/*.md + drafts.py | |
| 2026-09-13T14:19 | P3.3 | Red | test_drafts 10, ModuleNotFoundError drafts | |
| 2026-09-13T14:21 | P3.3 | Green | prompts 3 + drafts.py(PlanDraft/TaskDraft/DecomposeResult/decompose_with_retry), 270 passed | |
| 2026-09-13T14:22 | P3.3 | Gate | make check pass (270), pre-commit pass, 시도 3/3 (E501·B007) | |
| 2026-09-13T14:22 | P3.3 | 완료 | 보드 done | 0ba957a |
| 2026-09-13T14:22 | P3.4 | 착수 | graph.py + state.py (LangGraph, interrupt, checkpointer) | |
| 2026-09-13T14:23 | P3.4 | Red | test_graph 8, ModuleNotFoundError graph/state | |
| 2026-09-13T14:25 | P3.4 | Green | graph.py 6노드(interrupt/resume/reject/blocked) + state.py + checkpointer, 278 passed | |
| 2026-09-13T14:27 | P3.4 | Gate | make check pass (278), pre-commit pass, 시도 3/3 | |
| 2026-09-13T14:27 | P3.4 | 완료 | 보드 done | cc4e86b |
| 2026-09-13T14:27 | P3.5 | 착수 | emit.py toposort/overlap/emit | |
| 2026-09-13T14:27 | P3.5 | Red | test_emit 9, ModuleNotFoundError emit | |
| 2026-09-13T14:28 | P3.5 | Green | emit.py toposort/paths_overlap/serialize_overlaps/emit(멱등, 사이클→goal.blocked), 292 passed | |
| 2026-09-13T14:29 | P3.5 | Gate | make check pass (292), pre-commit pass, 시도 3/3 | |
| 2026-09-13T14:29 | P3.5 | 완료 | 보드 done | 2057464 |
| 2026-09-13T14:30 | PC-3 | 결과 | fake PASS; 실 LLM 400 크레딧 부족 → pending, 사용자 결정 대기 | |
| 2026-09-13T15:10 | P3.1+ | Green | ollama.py OllamaCompatProvider + llm_provider 선택 (D-33), 298 passed | |
| 2026-09-13T15:12 | P3.1+ | Gate | make check pass (298), pre-commit pass — D-33 반영 | 3ceff07 |
| 2026-09-13T15:15 | PC-3 | 결과 | Ollama 경로 PASS (2 calls, 8 tasks), 검토안 3/3 → 사람 서명 대기 | 91e4ed7 |
| 2026-09-13T15:35 | PC-3 | 서명 | 사용자 pass (2번 조건부) → pass | |
| 2026-09-13T15:35 | P4.1 | 착수 | agents/tools base/fs/shell/git/github | |
| 2026-09-13T15:36 | P4.1 | Red | tools 4 파일 34 함수, conftest(remote/worktree/spy/ctx), ModuleNotFoundError agents.tools.base | |
| 2026-09-13T15:38 | P4.1 | Green | agents/tools base/fs/shell/git/github, 349 passed | |
| 2026-09-13T15:40 | P4.1 | Gate | make check pass (349), pre-commit pass, 시도 3/3 | |
| 2026-09-13T15:40 | P4.1 | 완료 | 보드 done | b39eee5 |
| 2026-09-13T15:40 | P4.2 | 착수 | agents/base.py + context.py | |
| 2026-09-13T15:40 | P4.2 | Red | test_base 8, ModuleNotFoundError agents.base | |
| 2026-09-13T15:42 | P4.2 | Green | agents/base.py(계약+BaseAgent.run) + context.py(조립·예산), 356 passed | |
| 2026-09-13T15:44 | P4.2 | Gate | make check pass (356), pre-commit pass, 시도 2/3 | |
| 2026-09-13T15:44 | P4.2 | 완료 | 보드 done | 6be7531 |
| 2026-09-13T15:44 | P4.3 | 착수 | agents/coding.py LangGraph 루프 + 스크립트 픽스처 | |
| 2026-09-13T15:44 | P4.3 | Red | test_coding 7 + coding_scripts 5, ModuleNotFoundError agents.coding | |
| 2026-09-13T15:47 | P4.3 | Green | agents/coding.py LangGraph 루프(pass/retry/fail/scope/dependency), 363 passed | |
| 2026-09-13T15:49 | P4.3 | Gate | make check pass (363), pre-commit pass, 시도 3/3 | |
| 2026-09-13T15:49 | P4.3 | 완료 | 보드 done | 9d8dd0c |
| 2026-09-13T15:49 | P4.4 | 착수 | worker entrypoint/publish/Dockerfile — 워커 XADD 직접, DB append는 Scheduler ingest(D-26) | |
| 2026-09-13T15:50 | P4.4 | Red | test_entrypoint 7 + integration test_worker 1, ModuleNotFoundError worker.entrypoint | |
| 2026-09-13T15:53 | P4.4 | Green | worker entrypoint/publish/__main__/Dockerfile + compose worker, 371 passed | |
| 2026-09-13T15:58 | P4.4 | Gate | make check pass (371), docker 컨테이너 통합 1 passed, pre-commit pass, 시도 3/3 | |
| 2026-09-13T15:58 | P4.4 | 완료 | 보드 done | 1c9a026 |
| 2026-09-13T15:58 | P4.5 | 착수 | scheduler queue/graph/launcher/scheduler + ingest | |
| 2026-09-13T15:59 | P4.5 | Red | test_scheduler 6 (a~i), ModuleNotFoundError scheduler.graph | |
| 2026-09-13T16:03 | P4.5 | Green | scheduler graph/launcher/queue/scheduler + ingest, projection unsigned skip, 378 passed | |
| 2026-09-13T16:04 | P4.5 | Gate | make check pass (378), pre-commit pass, 시도 3/3 | |
| 2026-09-13T16:04 | P4.5 | 완료 | 보드 done | 2ad0f2e |
| 2026-09-13T16:10 | PC-4 | 착수 | scripts/pc4_run_tasks.py — Ollama 실 코드 생성(D-34), --fake는 스모크 | |
| 2026-09-13T16:14 | PC-4 | 자동 | make check pass (378), test-integration 5 passed, --fake PASS; Ollama 1차 0/3 (pycache 커밋, API 환각, 파일 전체 재작성 손상) | c728123 |
| 2026-09-13T16:20 | PC-4 | 자동 | Ollama 2차: Scheduler 중복 배정 버그 발견 → 수정 | 1f1c460 |
| 2026-09-13T16:27 | PC-4 | 자동 | Ollama 3차 2/3; Task 1(기존 파일 수정)은 JSON 안 코드 손상 → 파일 블록 형식으로 변경 → 단독 1회차 통과 | |
| 2026-09-13T16:40 | PC-4 | 자동 | Ollama 5·6차 각 2/3 (모델 편차), 파서 마커 느슨화, make check pass (383) | 9518095 |
| 2026-09-13T16:45 | PC-4 | 판정 | pending — docs/pc/PC-4.md, 사용자 선택지 3개 | |
| 2026-09-13T16:55 | PC-4 | 자동 | D-35 간단 Task 3개로 Ollama 3/3 PASS ×2 (Task당 1회차, 10–16s) | |
| 2026-09-13T16:56 | PC-4 | 판정 | pass (조건부, D-35) — 사용자 결정, P5 진행 | |
| 2026-09-13T17:00 | P5.1 | 착수 | api/ 라우터 4 파일 + deps + Idempotency 미들웨어 | |
| 2026-09-13T17:02 | P5.1 | Red | tests/api 7건, create_app(factory=) TypeError | 1d3cde9 |
| 2026-09-13T17:08 | P5.1 | Green | projects/goals/tasks/events + idempotency, 390 passed | 83b8ae0 |
| 2026-09-13T17:09 | P5.1 | Gate | make check pass (390), mypy clean, 시도 2/3 | |
| 2026-09-13T17:09 | P5.1 | 완료 | 보드 done | 83b8ae0 |
| 2026-09-13T17:15 | P5.2 | 착수 | runner + approvals + 웹훅 연결 | |
| 2026-09-13T17:20 | P5.2 | Red | test_goal_flow 6건, ModuleNotFoundError orchestrator.runner | 07d1109 |
| 2026-09-13T17:30 | P5.2 | Green | GoalRunner/ApprovalService/app wiring, members 키, 396 passed | 6742383 |
| 2026-09-13T17:31 | P5.2 | Gate | make check pass (396), mypy clean, 시도 3/3 | |
| 2026-09-13T17:31 | P5.2 | 완료 | 보드 done | 6742383 |
| 2026-09-13T17:35 | P5.3 | 착수 | api/stream.py WS | |
| 2026-09-13T17:37 | P5.3 | Red | test_stream 3건, WebSocketDisconnect | bd30f07 |
| 2026-09-13T17:42 | P5.3 | Green | 연결당 group($)+replay(seq)+dedupe, 399 passed | 5681505 |
| 2026-09-13T17:43 | P5.3 | Gate | make check pass (399), mypy clean, 시도 3/3 | |
| 2026-09-13T17:43 | P5.3 | 완료 | 보드 done | 5681505 |
| 2026-09-13T17:50 | P5.4 | 착수 | scripts/e2e_dry_run.py | |
| 2026-09-13T17:52 | P5.4 | Red | test_e2e_script 2건, FileNotFoundError | b227e4b |
| 2026-09-13T18:05 | P5.4 | Green | e2e 스크립트(자동 머지 포함), 401 passed | e3322f6 |
| 2026-09-13T18:06 | P5.4 | Gate | make check pass (401) && e2e --fake PASS (8 calls), 시도 3/3 | |
| 2026-09-13T18:06 | P5.4 | 완료 | 보드 done | e3322f6 |
| 2026-09-13T18:10 | P5.5 | 착수 | docs/runbook.md + scripts/check_runbook.sh | |
| 2026-09-13T18:18 | P5.5 | Gate | check_runbook.sh 5/5 blocks OK (imports, migrate, app factory, e2e --fake, lint), 시도 2/3 | |
| 2026-09-13T18:18 | P5.5 | 완료 | 보드 done | aa90ad5 |
| 2026-09-13T18:20 | PC-5 | 자동 | make check pass (401), test-integration 5 passed, e2e --fake PASS | |
| 2026-09-13T18:30 | PC-5 | 자동 | 실 LLM(Ollama 7B) e2e FAIL — Task 8, 브랜치 1, PR 0 (Task 1 3회 실패, 나머지 의존 대기) | |
| 2026-09-13T18:32 | PC-5 | 판정 | pending — docs/pc/PC-5.md, docs/postmortem/mvp1.md; Anthropic 재실행·사람 (1)~(3) 대기 | 5ad94a7 |
| 2026-09-13T19:10 | P6.1 | 착수 | runtime.py, __main__, InProcessLauncher, mounts, settings | |
| 2026-09-13T19:15 | P6.1 | Red | tests/runtime 6 + integration test_launcher, ModuleNotFoundError control_plane.runtime | 4787e91 |
| 2026-09-13T19:40 | P6.1 | Green | Runtime 체인 컨슈머, per-project repo, launchers, 412 passed | 8309e7e |
| 2026-09-13T19:45 | P6.1 | Gate | make check pass (412) && test-integration 6 passed (docker launcher 실제 기동), 시도 3/3 | |
| 2026-09-13T19:45 | P6.1 | 완료 | 보드 done | 8309e7e |
| 2026-09-13T19:50 | P6.2 | Red | test_goal_flow 2건 (복원, 체크포인트 없으면 skip) | 8953c69 |
| 2026-09-13T19:58 | P6.2 | Green | GoalRunner.restore_waiting (startup), 414 passed | 725422e |
| 2026-09-13T20:00 | P6.2 | Gate | make check pass (414), mypy clean, 시도 3/3 | |
| 2026-09-13T20:00 | P6.2 | 완료 | 보드 done | 725422e |
| 2026-09-13T20:05 | P6.3 | Red | test_dry_merge 5건 + e2e (dry-merge) 출력 | 9059168 |
| 2026-09-13T20:15 | P6.3 | Green | control_plane/dry_merge.py, Runtime 체인, e2e 자체 머지 제거, 419 passed | c6c9e88 |
| 2026-09-13T20:16 | P6.3 | Gate | make check pass (419), 시도 3/3 | |
| 2026-09-13T20:16 | P6.3 | 완료 | 보드 done | c6c9e88 |
| 2026-09-13T20:20 | P6.4 | Red | test_webhooks 2 + test_goal_flow 2, discussion_comment 204 | 870726a |
| 2026-09-13T20:28 | P6.4 | Green | _discussion_comment 핸들러(문서 확인), SlashCommand.source/number, 매칭 규칙, 423 passed | bb757c8 |
| 2026-09-13T20:29 | P6.4 | Gate | make check pass (423), 시도 2/3 | |
| 2026-09-13T20:29 | P6.4 | 완료 | 보드 done | bb757c8 |
| 2026-09-13T20:35 | P6.5 | Red | pricing/base/worker/runtime/e2e 5건 | 0d45171 |
| 2026-09-13T20:45 | P6.5 | Green | pricing.py, BaseAgent(prices), WORKER_LLM_PRICE_*, HITL_LLM_PRICE_*, 427 passed | abadc51 |
| 2026-09-13T20:46 | P6.5 | Gate | make check pass (427), pc4 --fake PASS, 시도 2/3 | |
| 2026-09-13T20:46 | P6.5 | 완료 | 보드 done | abadc51 |
| 2026-09-13T20:50 | P6.6 | Red | test_repo_cache 5 + goal_flow 1 + scheduler 1 | 6c2c175 |
| 2026-09-13T21:10 | P6.6 | Green | repo_cache.py, Settings.repo_root, runner/scheduler/runtime/app 배선, 434 passed | d70a254 |
| 2026-09-13T21:12 | P6.6 | Gate | make check pass (434), mypy clean, 시도 3/3 | |
| 2026-09-13T21:12 | P6.6 | 완료 | 보드 done | d70a254 |
| 2026-09-13T21:20 | P6.7 | Red | coding/worker/projection/pr_opener/runtime/e2e 6건 | 5b0af4b |
| 2026-09-13T21:45 | P6.7 | Green | pr_opener.py, coding.py 그래프 분할, 스키마 additive, Runtime 체인, 438 passed | d5b1b10 |
| 2026-09-13T21:50 | P6.7 | Gate | make check pass (438) && test-integration 6 passed, pc4 --fake PASS, 시도 3/3 | |
| 2026-09-13T21:50 | P6.7 | 완료 | 보드 done | d5b1b10 |
| 2026-09-13T21:55 | P6.8 | Green | ROADMAP C1~C5, runbook 기동 순서 | 5f98754 |
| 2026-09-13T21:58 | P6.8 | Gate | check_runbook.sh 5/5 && make check pass (438) | |
| 2026-09-13T21:58 | P6.8 | 완료 | 보드 done | 5f98754 |
| 2026-09-13T22:00 | PC-6 | 자동 | make check 440, test-integration 6, check_runbook 5/5; pc6_via_api 1차 3/4 done → Dry 머지가 git main 안 옮김 발견 | 94c0090 |
| 2026-09-13T22:40 | PC-6 | 자동 | 4차: owned_paths 디렉토리 매칭 버그 + ingest 순서 역전 예외 발견·수정 | 3409a6a, aa89d2f |
| 2026-09-13T23:10 | PC-6 | 판정 | pending — 5차 파이프라인 완주(T1 done→git merge→의존 배정), T2 7B import 오류로 blocked. docs/pc/PC-6.md | |
| 2026-09-14T18:50 | PC-6 | 판정 | pass (조건부) — 사용자 결정, X.1 진행 | ea92dfa |
| 2026-09-14T18:55 | P7 | 계획 | X.1을 P7.1~P7.4 + PC-7로 §7에 작성 (D-41 제안, D-42) — 사용자 검토·App 자격 증명 대기 | |
| 2026-09-14T19:05 | P7.1 | Red | test_app_check 5 + ping 1 | cc6d9d2 |
| 2026-09-14T19:20 | P7.1 | Green | app_check.py(6개 항목, 문서 확인 경로), CLI, ping, runbook 3b, 448 passed | d215282 |
| 2026-09-14T19:22 | P7.1 | Gate | make check pass (448), check_runbook 5/5, 시도 3/3 | |
| 2026-09-14T19:22 | P7.1 | 완료 | 보드 done | d215282 |
| 2026-09-14T19:30 | P7.2 | Red | token_nowait, 토큰 URL 마스킹, PrOpener push, GitPusher, runtime 배선 5건 | 7954214 |
| 2026-09-14T19:55 | P7.2 | Green | D-41 구현, 453 passed | f39fa6c |
| 2026-09-14T19:57 | P7.2 | Gate | make check pass (453), 시도 3/3 | |
| 2026-09-14T19:57 | P7.2 | 완료 | 보드 done | f39fa6c |
| 2026-09-14T20:05 | P7.3 | Green | runbook 웹훅 받기(smee/cloudflared), make run-webhook-tunnel | ad11188 |
| 2026-09-14T20:06 | P7.3 | Gate | check_runbook 5/5 | |
| 2026-09-14T20:06 | P7.3 | 완료 | 보드 done (사람 항목: 채널 URL 받으면 ping 실제 수신) | ad11188 |
| 2026-09-14T20:15 | P7.4 | Red | test_cleanup 4건 | 6327521 |
| 2026-09-14T20:35 | P7.4 | Green | cleanup.py + client 메서드 + 스크립트 2개, 457 passed | 7175123 |
| 2026-09-14T20:36 | P7.4 | Gate | make check pass (457), 시도 2/3 | |
| 2026-09-14T20:36 | P7.4 | 완료 | 보드 done | 7175123 |
| 2026-09-14T20:37 | PC-7 | 대기 | 사용자 입력(App 자격 증명·테스트 repo·터널) 필요 | |
| 2026-09-15T19:00 | PC-7 | 자동 | Discussion #2 → /approve → Issue #3–#6 → PR #7 → 사람 머지 → done → PR #8. verify_chain True | 2b0b107 |
| 2026-09-15T19:15 | PC-7 | 판정 | pass — docs/pc/PC-7.md, 사용자 서명·cleanup --apply 대기 | |
| 2026-09-16T10:30 | P8.1 | Red | IntegrityError→retry, dry clone 금지, runtime 3건 | |
| 2026-09-16T10:45 | P8.1 | Green+Gate | _PERMANENT 분류, RepoCache(dry_run=), make check pass (463) | 5f21ec0 |
| 2026-09-16T11:10 | P8.2 | Green+Gate | 개별 마운트, --user 호스트 uid, WORKER_WORKDIR/git env; make check 467, test-integration 6 (chmod 없이 push) | fd4060d |
| 2026-09-16T11:40 | P8.3 | Green+Gate | task.failed 보장, is_alive, reap/recover_orphans, reaper 루프; make check 472 | 1a4e5dc |
| 2026-09-16T12:20 | P8.4 | Green+Gate | 409/폴백/검증/목록/approve·reject API/fail-closed; make check 479 | 18416d6 |
| 2026-09-16T12:50 | P8.5 | Green+Gate | ingest → mark_published_by_id, 중복 XADD 0, tool_calls 확인; make check 480 | 1897264 |
| 2026-09-19T00:30 | P9.7 | 착수+Red | 운영진 공지 대응(§6 2026-09-19): 완료 판정 red 3건 중 2 fail(의도), 전부 cancelled 케이스는 이미 pass | |
| 2026-09-19T00:45 | P9.7 | Green+Gate | Scheduler.complete: Epic별 판정 후 Goal, memo+상태 조회로 중복 0; make check 547 | |
| 2026-09-19T01:00 | P9.8 | Red | find_installation·InstallationTokenPool·ClientRegistry 6건 — import 실패(의도) | |
| 2026-09-19T01:20 | P9.8 | Green+Gate | find_installation, InstallationTokenPool, ClientRegistry(dry 공유), _app_creds 중복 제거; make check 553 | |
| 2026-09-19T01:50 | P9.9 | Red | RepoRouter·RoutingGitHubClient 4건 + token_getter(repo) 시그니처(repo_cache·pr_opener·runtime·build_runner 테스트) fail(의도) | |
| 2026-09-19T02:30 | P9.9 | Green+Gate | RepoRouter(repo→installation, prepare·refresh_all·token_nowait(repo)), Routing client 위임, build_runner·Runtime·RepoCache·GitPusher 배선; make check 557 | |
| 2026-09-19T03:10 | P9.10 | Red | run_check repo별 installation 탐지·설치 링크·Discussions 경고, 연결 API(외부 설치 무토큰·미설치 400·서버 installation은 토큰) 7 fail + 5 error(의도) | |
| 2026-09-19T03:40 | P9.10 | Green+Gate | run_check repo별 installation·install_url·Discussions 경고, POST /projects 외부 설치=무토큰·owner=설치 계정·GitHub 이름/브랜치, installation_id 이벤트(추가)→projection; 조정: 빈 repo 자동 초기 커밋 취소(문서 미확인)→400; make check 563 | |
