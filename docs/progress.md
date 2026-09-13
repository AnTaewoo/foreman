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
