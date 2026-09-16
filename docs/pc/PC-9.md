# PC-9 — 심사자 워크스루 (공개 데모 foreman.antaewoo.com)

- 일시: 2026-09-16 코드 준비 완료 (커밋 e3a00da) · 배포·워크스루는 사용자 sudo 단계(`docs/deploy.md` 1~9) 뒤
- 판정: **pending — 배포됨(demo_up.sh, 19:14 KST), showcase Goal 진행 중, 사람 항목 대기**
- 구성: nginx(443, 사용자) → `127.0.0.1:8000` `foreman-api.service` + `foreman-control-plane.service`(docker 런처),
  `HITL_DRY_RUN=false`·`HITL_DEMO_MODE=true`(유닛), 실 GitHub `AnTaewoo/foreman_test`(public), LLM `qwen2.5-coder:14b`.

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (494) — P9.1~P9.4 포함 |
| `scripts/check_runbook.sh` | 5/5 |
| 데모 콘솔 `GET /`·`/static/*`·`/demo` (임시 API :8020, Postgres) | 200 / 200 / `{"demo_mode":true,"user_id":"judge",…}` |
| 데모 가드 (임시 API) | `POST /projects` 토큰 없음 401, 토큰 201, 같은 repo 409 → `demo_seed.py` 재사용 |
| `curl -I https://foreman.antaewoo.com/` 200, `/health` | 200 / `{"status":"ok"}` (19:00 KST) |
| WS `wss://foreman.antaewoo.com/projects/<id>/stream` 연결 | ok — 첫 이벤트 `project.created` 수신 |

## 사람 항목 — 시크릿 창에서 (배포 후, 가능하면 다른 네트워크)

| # | 절차 | 기대 | 결과 |
|---|---|---|---|
| 1 | `/` 로드, `[Showcase]` Goal 클릭 → Plan·Discussion·Issue·merged PR 링크 | GitHub 페이지 열림 | |
| 2 | 예시 Goal 버튼 → "Goal 생성" | 목록에 draft → planning → awaiting ≤ 3분 | |
| 3 | Plan 본문 | Discussion 본문과 동일, 6섹션 | |
| 4 | Approve | Task 표에 Issue 링크(#n), 상태 ready/running | |
| 5 | 워커 진행 | Task에 PR 링크(draft), 이벤트 로그가 새로고침 없이 흐름 | |
| 6 | 소유자가 GitHub에서 PR 머지 | 웹훅 → Task done(새로고침 없이), 의존 Task 배정 | |
| 7 | 진행 중에 두 번째 Goal 생성 | 429 "a goal is already running" 토스트 | |
| 8 | 같은 IP로 쓰기 11회/분 | 429 "too many requests" | |
| 9 | `curl -X POST https://foreman.antaewoo.com/projects` | 401 | |
| 10 | `sudo systemctl restart foreman-api` (awaiting Goal이 있을 때) | 로그 `runner.restored_waiting`, 콘솔 Approve 202 | |
| 11 | GitHub App → Redeliver ping | 200 (`journalctl -u foreman-api`) | |
| 12 | `journalctl -u foreman-api -u foreman-control-plane` | 토큰·키·PEM 노출 0 | |

pass 조건: 자동 전부 + 사람 12개. 제출 전(09-20) 시크릿 창에서 1·2·4·5를 한 번 더.

## 준비 중 발견

- 이 서버 Ollama 0.20.0은 `gemma4:12b`를 받을 수 없다(412) → 비교는 `gemma4:e4b`로, 결정은 14b 유지(`docs/pc/X-2.md`).
- 통합 테스트가 `HITL_DATABASE_URL` 기본값(`hitl`)을 downgrade base로 비웠다(오늘 `-o addopts=""` 전체 실행 중) →
  전용 `hitl_test`로 분리(`tests/integration/conftest.py`). 데모 DB는 이제 테스트가 건드리지 않는다.
- PC-7·PC-8 잔여 프로세스(8000·8010, smee)는 내렸다. PC-7의 `pc7` DB·Redis 12 데이터는 그대로.
- 배포 직후 세 건 수정(§6 "P9 배포"): 0.0.0.0 바인드, runner의 project.created 폴백, 상대 repo_root clone 경로,
  runner_error → goal.cancelled. 그 사이 만든 showcase Goal 2개는 cancelled로 남아 있다(콘솔 고정 제외).
- systemd는 아직 미적용 — `deploy/demo_up.sh`로 떠 있다. 옮길 때: `deploy/demo_down.sh` → `sudo systemctl enable --now …`.
