# PC-0 — 스택이 뜬다

- 일시: 2026-09-13 11:24 KST
- 판정: **pass**

## 자동 항목

| 항목 | 결과 |
|---|---|
| `MINIO_HOST_PORT=9100 MINIO_CONSOLE_HOST_PORT=9101 docker compose up -d --wait` | postgres:16, redis:7, minio 3개 `healthy` |
| `uvicorn control_plane.api.app:app --factory` (port 8765) → `GET /health` | 200 `{"status":"ok"}` |
| `docker compose down` | 컨테이너 0, 네트워크 제거 |
| `make check` | pass (ruff, mypy 14 files, 27 tests) |
| `uv run pre-commit run --all-files` | pass |

## 사람 항목 (기록)

- `git remote -v` = `git@github.com:AnTaewoo/foreman.git` — 일치
- `git var GIT_AUTHOR_IDENT` = `AnTaewoo <atw13730@gmail.com>` — noreply 주소가 아니지만 사용자가 그대로 쓰기로 결정 (2026-09-13)

## 환경 메모

- Docker는 `echo "<cmd>" | newgrp docker`로 실행 (ROADMAP §9)
- 호스트 9000/9001 점유 → MinIO는 9100/9101. `HITL_MINIO_ENDPOINT`를 쓰는 코드가 생기면 `.env`에 `http://localhost:9100`으로 맞출 것
- API는 8000 대신 8765로 확인(8000 충돌 회피용, 코드 기본값은 8000 유지)
