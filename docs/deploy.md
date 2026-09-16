# 공개 데모 배포 — foreman.antaewoo.com (P9, D-52)

원티드 AI 챔피언십 제출용. 심사자가 로그인·설치 없이 브라우저로 `https://foreman.antaewoo.com/`을 열어
Goal → Plan 승인 → Issue → PR을 체험한다. 구성: 이 서버(nginx + Let's Encrypt) → `127.0.0.1:8000`(API, 데모 콘솔)
+ 상주 control plane(docker 워커) + Postgres/Redis(compose) + 로컬 Ollama. 실 GitHub 공개 repo `AnTaewoo/foreman_test`.

## 포트·프로세스

| 무엇 | 어디 | 비고 |
|---|---|---|
| API + 데모 콘솔 + Orchestrator | `127.0.0.1:8000` (`foreman-api.service`) | nginx가 443 → 8000. WS `/projects/{id}/stream` 업그레이드 필요 |
| control plane | 포트 없음 (`foreman-control-plane.service`) | relay·projection·scheduler(docker 런처)·PrOpener·reaper |
| Postgres / Redis | compose `foreman-postgres-1`, `foreman-redis-1` (5432/6379) | `make docker-up` |
| 워커 | `docker run … foreman-worker:dev` (호스트 uid) | `make worker-image` |
| Ollama | `localhost:11434` | 워커는 `host.docker.internal:11434` |

## 사용자 실행 순서 (sudo 단계)

1. **DNS**: `foreman.antaewoo.com A 1.243.86.151` → `dig +short foreman.antaewoo.com`
2. **nginx**: `/etc/nginx/sites-available/foreman.antaewoo.com` 작성 — `deploy/nginx/foreman.antaewoo.com.conf` 참고
   (80 블록 먼저 활성) → `sudo ln -s … sites-enabled/` → `sudo nginx -t && sudo systemctl reload nginx`
3. **인증서**: SAN 인증서 확장 — **기존 도메인 전부 나열**(누락하면 그 도메인 인증이 끊긴다):
   ```
   sudo certbot certonly --webroot -w /var/www/certbot --expand \
     -d antaewoo.com -d www.antaewoo.com -d blog.antaewoo.com -d code.antaewoo.com \
     -d comfyui.antaewoo.com -d jupyter.antaewoo.com -d ollama.antaewoo.com -d opencode.antaewoo.com \
     -d video.antaewoo.com -d vite-localhost.antaewoo.com -d foreman.antaewoo.com
   ```
   → 443 블록 활성 → `sudo nginx -t && sudo systemctl reload nginx`
4. **`.env`** (`HITL_DRY_RUN`은 유닛이 false로 켠다, `.env`는 그대로):
   ```
   HITL_DEMO_MODE=true
   HITL_ADMIN_TOKEN=<openssl rand -hex 24>
   HITL_LLM_PROVIDER=openai_compat
   HITL_LLM_MODEL=<docs/pc/X-2.md 선정 모델>
   HITL_SCHEDULER_MAX_WORKERS=2
   HITL_GITHUB_APP_ID / HITL_GITHUB_APP_PRIVATE_KEY / HITL_GITHUB_INSTALLATION_ID / HITL_GITHUB_WEBHOOK_SECRET
   ```
5. **이전 프로세스 종료**: 8000 포트의 수동 uvicorn·`python -m control_plane`·smee(PC-7 잔여)를 내린다.
   `ps -eo pid,args | grep -F -e "uvicorn control_plane" -e "python -m control_plane" -e smee-client | grep -v grep`
6. **DB·Redis 초기화** (데모 시작 전 한 번, 기존 점검 데이터 제거):
   ```
   docker compose exec -T postgres psql -U hitl -d postgres -c "DROP DATABASE IF EXISTS hitl WITH (FORCE);" -c "CREATE DATABASE hitl OWNER hitl;"
   docker compose exec -T redis redis-cli -n 0 FLUSHDB
   make migrate && make worker-image
   ```
7. **systemd**:
   ```
   sudo cp deploy/systemd/*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now foreman-control-plane foreman-api
   systemctl status foreman-api foreman-control-plane --no-pager
   curl -s https://foreman.antaewoo.com/health
   ```
8. **GitHub App**: Webhook URL `https://foreman.antaewoo.com/webhooks/github`, secret = `HITL_GITHUB_WEBHOOK_SECRET`,
   **Active 체크** → "Redeliver ping" → `journalctl -u foreman-api -n 20`에 200.
9. **repo**: `AnTaewoo/foreman_test` → Settings → Visibility **public** (Discussions + Plans 카테고리는 이미 있음).
10. **데모 준비** (아래).
11. (선택) Ollama 상주: `sudo systemctl edit ollama` → `[Service] Environment=OLLAMA_KEEP_ALIVE=-1`.
    심사 기간에는 다른 Ollama 워크로드(27GB 모델)를 내린다 — GPU 하나를 orchestrator·워커가 같이 쓴다.

## 데모 준비 (P9.6)

```
export HITL_ADMIN_TOKEN=<.env 값>
uv run python scripts/cleanup_repo.py AnTaewoo/foreman_test --apply       # 이전 Issue/PR/ai-* 정리 (Discussions는 남는다)
uv run python scripts/demo_seed.py --repo AnTaewoo/foreman_test --owner AnTaewoo
```
→ 콘솔 `https://foreman.antaewoo.com/`에서 `[Showcase] …` Goal의 Plan을 **Approve** → Issue·PR이 생기면 소유자가
GitHub에서 PR을 **머지** → Task done. 심사자는 이 showcase로 결과를 바로 보고, 예시 Goal을 직접 만들어 본다.

심사 기간(제출 ~ +7일)에는 cleanup·DB 초기화를 **하지 않는다**(DB↔repo 불일치). 종료 후 6번 + cleanup을 한 번에.

## 운영

- 로그: `journalctl -u foreman-api -f`, `journalctl -u foreman-control-plane -f` (json). 토큰·키는 로그에 없다.
- 재시작: `sudo systemctl restart foreman-api` — `awaiting_plan_approval` Goal은 복원되지만 **planning 중인 Goal은
  끊긴다**(P6.2). 심사 기간에는 피한다. control plane 재시작은 안전(재기동 시 `recover_orphans`).
- 롤백: `sudo systemctl stop foreman-api foreman-control-plane`, nginx 사이트 링크 제거 후 reload.
- 한도(429): 프로젝트당 진행 중 Goal 1개, 시간당 6개, IP당 쓰기 10회/분(`HITL_DEMO_*`). 관리 라우트
  (`POST /projects`, cancel, task patch)는 `X-Admin-Token`.
- 개발 중 테스트: `make check`는 sqlite. **통합 테스트(`make test-integration`)는 `hitl_test` DB를 비우고 다시 만든다** —
  데모 DB `hitl`은 건드리지 않는다(`FOREMAN_TEST_DATABASE_URL`로 변경 가능). `-o addopts=""`로 전체를 돌리지 말 것.
- 접속 확인은 시크릿 창에서: `/`, `/health`, `/docs`, Goal 생성 → 429/승인 흐름 (`docs/pc/PC-9.md`).
