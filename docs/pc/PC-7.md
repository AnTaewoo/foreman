# PC-7 — 실 GitHub에서 Goal 1개 (X.1)

- 일시: 2026-09-14 20:20 KST ~ (진행 중)
- 판정: _(진행 중)_
- 대상: App `foreman-antaewoo` (id 4940123), installation 161606086, repo `AnTaewoo/foreman_test` (private)
- 웹훅 수신: smee.io 채널 → `make run-webhook-tunnel` (사용자 선택 2026-09-14)

## 자동 항목

| 항목 | 결과 |
|---|---|
| `make check` | pass (457) |
| `scripts/github_app_check.py --repo AnTaewoo/foreman_test` 1차 | app/installation/token/permissions/repo/webhook `[ok]`, **events `[FAIL]` (`check_suite` 미구독)**, webhook URL은 아직 `https://example.com/…` 자리표시자 |
| `scripts/seed_test_repo.py AnTaewoo/foreman_test` | pushed → `main` @ `324f637` (직후 `GET /commits`는 409 — GitHub 반영 지연, `ls-remote`·`/branches`로 확인) |

## 사람이 해야 할 설정 (2026-09-14 확인)

1. App → Permissions & events → Subscribe to events: `check_suite` 추가 (나머지 4개는 구독됨).
2. `foreman_test` → Settings → Features → **Discussions 켜기**, 카테고리 **Plans** 생성 (지금 `has_discussions: false`).
3. smee.io 채널 URL을 App의 Webhook URL에 넣고, 같은 URL을 `SMEE_URL`로 전달.
4. (권장) App 설치 범위를 `foreman_test`만으로 좁히기 — 지금은 계정의 repo 8개 전부(foreman 본 repo 포함).

## 다음 단계

`github_app_check` 전부 `[ok]` → `.env`에 `HITL_DRY_RUN=false` → `make run-control-plane` / `make run-api` /
`SMEE_URL=… make run-webhook-tunnel` → `POST /projects {repo: "AnTaewoo/foreman_test", members: [...]}` → `POST /goals`
→ GitHub Discussion에서 `/approve` → Issue·PR 생성 확인 → 사람이 PR 머지 → `pr.merged` → done → `cleanup_repo.py --apply`.
