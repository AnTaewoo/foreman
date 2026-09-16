# PC-9 — 심사자 워크스루 (공개 데모 foreman.antaewoo.com)

- 일시: 2026-09-16 코드 준비 완료 (커밋 e3a00da) · 배포·워크스루는 사용자 sudo 단계(`docs/deploy.md` 1~9) 뒤
- 판정: **pending — 배포됨. showcase 1회 완주(Discussion #3 → Issue #4~#9 → PR #10·#11) 뒤 사용자 요청으로 19:29 KST 초기 상태로 리셋(프로젝트만, Goal 0). 사람 항목은 사용자가 직접 Goal을 만들며 확인**
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
| showcase: Goal → Plan(Discussion #3) → 승인 → Issue #4~#9 → 워커 2개(docker, host uid) → push → PR #10·#11 | ok (19:14~19:17 KST, 14b) |

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
- reaper 경쟁 발견·수정(§6 P9 배포 (6)): 정상 종료한 워커를 `worker_died`로 오판 → `REAP_DEAD_GRACE_S=60`. control plane만 재시작(19:23).
- 남은 사용자 단계: GitHub App 웹훅 URL을 `https://foreman.antaewoo.com/webhooks/github`로(지금은 smee라 **PR 머지가 플랫폼에 안 들어온다**),
  PR #10·#11 머지, (선택) systemd 전환·`OLLAMA_KEEP_ALIVE`.
- 19:29 KST 초기화(§6 "P9 초기화"): GitHub Issue/PR/브랜치 정리, DB·Redis 초기화, 프로젝트만 시드. 같은 제목 Goal이
  옛 Discussion을 재사용하던 문제를 고쳐(제목에 Goal id) 심사자 여러 명이 같은 예시 버튼을 눌러도 각자 Discussion을 받는다.
- 19:5x KST: 사용자 결정으로 `.env`의 P9 키 삭제, 데모 모드 꺼짐(선택), 기존 설정 + `HITL_DRY_RUN=false`로만 실행.
  19:51:23에 DB `hitl`이 외부에서 DROP/CREATE 되어 재시작이 실패 → migrate + 프로젝트 재시드로 복구(§6 "P9 env 원복").
- 20:4x KST: 콘솔 "GitHub repo 연결" 폼 + `GET /projects/check`(discussions 항목 포함). `foreman_test`는 사용자가 삭제,
  새 repo `foreman_demo` 점검 8/8 ok(웹훅 URL 갱신 확인). DB 비움 → 사용자가 콘솔에서 직접 연결해 시험(§6 "P9 repo 연결").
- 21:5x KST: 다른 세션의 foreman_demo 라이브 테스트(Goal #2 분해 즉사, Goal #3 Task 1회 만에 blocked) 리포트 8건 중 7건 수정
  (§6 "P9 버그 #2~#8"). 대상 repo `foreman_demo`는 커밋 0개였다 — 점검에 `content` 항목 추가. 재시험 전 repo에 커밋이 필요.
- 23:3x KST 2차 라이브(다른 세션): 머지 → 웹훅 2초 → 의존 Task 자동 배정은 정상. 후속 PR #10·#11 충돌(옛 base) + clone의
  origin에 토큰 잔류 → §6 "P9 버그 #9~#10"으로 수정·배포(51ecc98). PR #10·#11은 닫는 것을 권함(중복 Task 산출물).
- 00:2x KST 3차 라이브(다른 세션, foreman_calculator): Task #3 "Install Flask"가 requirements.txt로 needs_decision → Goal 사망.
  §6 "P9 3차 라이브" #1~#3 수정·배포(1b2397b). "Task 완료 정의 = 인수 테스트" 제안은 §6 결정 요청으로 올림(D-55 후보).
