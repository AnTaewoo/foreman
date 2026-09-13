# PC-2 — GitHub Adapter, mock only

- 일시: 2026-09-13 15:10 KST
- 판정: **pass**
- 실 GitHub 검증은 하지 않는다 (§8 X.1). MVP 1 전 구간 실제 GitHub 호출 0.

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (238 tests, ruff, mypy 30 files) |
| `uv run pytest tests/github_adapter -q` | 59 passed (auth 7, client 18, discussions 7, webhooks 17, dry_run 10) |
| `grep -rn "api.github.com" --include=*.py . \| grep -v tests \| grep -v .venv` | `github_adapter/client.py:28` 상수 1건뿐 (`discussions.py`는 같은 client의 base_url을 쓰므로 상수 없음) |
| `get_github_client(Settings())` 타입 | `DryRunGitHubClient` |

## 구현 요약 (P2.1~P2.5)

- **auth.py**: App JWT RS256(exp−iat=600) → installation token, 만료 5분 전 갱신, `InstallationAuth` 401 시 1회 재발급.
- **protocol.py / markers.py / client.py**: `GitHubClient` Protocol + frozen I/O 모델. 7개 쓰기 메서드 전부 멱등(Issue 마커 목록 스캔 D-22, status 라벨 교체, key 코멘트, 브랜치 존재 확인, 열린 PR 재사용, milestone 제목). httpx 직접, PyGithub 미사용.
- **discussions.py**: GraphQL create/list/comment. 공개 SDL(`docs.github.com/public/fpt/schema.docs.graphql`)로 D-09 형태 재확인, 출처 docstring.
- **webhooks.py**: HMAC(`compare_digest`), delivery 중복 캐시, 앱 봇 자기 루프 무시(B10), 6종 매핑(표는 docstring), 슬래시 명령 훅, 메타 블록 없는 사람 PR 무시. correlation은 `resolve_goal` 훅이 있으면 goal_id, 없으면 project_id(D-25).
- **dry_run.py / __init__.py**: 메모리 상태 DryRun client 2개(결정적 번호, Issue/PR 공유 번호 공간, would 로그, snapshot), 팩토리는 `dry_run=True`(기본)면 Dry.

## 조정 (설계·ROADMAP과 다른 점)

1. `WebhookHandler`에 선택 인자 `resolve_goal`, `bot_login` 추가. 웹훅은 goal을 모르므로 correlation 결정에 훅이 필요했고, 봇 무시는 앱 자신에게만 적용해야 `github-actions[bot]`의 check_suite가 살아남는다.
2. `check_suite` 이벤트는 PR 본문이 없어 `task_id`를 실을 수 없다 → payload에 `pr_number`, `conclusion`, `head_sha`만. projection은 MVP 1에서 noop.
3. P2.3 ROADMAP은 "web fetch로 문서 확인"인데 docs.github.com의 reference 페이지는 fetch 시 색인만 돌아와 공개 SDL 파일로 확인했다(더 정확한 출처).

## 후속 (§8 X.1)

- 실 GitHub 연결 시 `discussion_comment` 웹훅(Plan Discussion의 `/approve`)을 `issue_comment`와 같은 슬래시 경로에 붙인다.
- `check_suite`에 task_id를 붙이려면 PR 번호 → Task 조회 훅이 필요하다.

## 추가 점검 (2026-09-13, 사용자 요청: P0~P2를 실체 위에서 한 번에)

`tests/integration/test_p0_p2_flow.py` — 진짜 Postgres + Redis, 네트워크 0:

| 단계 | 확인 |
|---|---|
| P0 | `create_app()` + 웹훅 라우터 마운트, `GET /health` 200 |
| P1 | project/goal/epic/task×2 이벤트 → outbox relay → projection consumer → Task ready(issue_number 반영) |
| P2 Dry GitHub | ensure_labels, milestone, Issue×2(멱등 재호출 확인), branch×2, PR×2, key 코멘트×2 — `snapshot()`으로 검증 |
| P2 webhook | 서명된 `/approve` → 슬래시 훅 202 / 앱 봇의 `pull_request.opened` 204(B10) / 사람의 `closed(merged)` 202 → `pr.merged` / 같은 delivery 재전송 200 duplicate |
| D-30(b) | Task 2는 `pr.merged` 웹훅이 `task.completed`보다 먼저 → `pr_merged_at`만 → completed 후 done |
| 봉투 | 웹훅 이벤트의 correlation = goal_id(`resolve_goal`), actor = github:alice, projection_error 0 |
| 체인 | `verify_chain_db` True → TRUNCATE → replay → force apply → Task 2개 done 재구축 |

함께 재실행: `make check` 238 passed, `make test-integration` 4 passed, `scripts/pc1_roundtrip.py` PASS.
