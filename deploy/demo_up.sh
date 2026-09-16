#!/usr/bin/env bash
# systemd 유닛과 같은 환경으로 API + control plane을 detached로 띄운다 (sudo 없이 쓰는 임시 실행, P9.4).
# 정식은 deploy/systemd/*.service. 내리려면 deploy/demo_down.sh. 로그: $LOG_DIR/foreman-{api,cp}.log
set -euo pipefail
cd "$(dirname "$0")/.."
LOG_DIR="${LOG_DIR:-$HOME/.foreman-logs}"; mkdir -p "$LOG_DIR"
export HITL_DRY_RUN=false HITL_DEMO_MODE=true HITL_LOG_FORMAT=json
if ss -ltn | grep -q ":8000 "; then echo "port 8000 is already in use — deploy/demo_down.sh 먼저" >&2; exit 1; fi
setsid nohup uv run --no-sync python -m control_plane > "$LOG_DIR/foreman-cp.log" 2>&1 < /dev/null &
setsid nohup uv run --no-sync uvicorn control_plane.api.app:app --factory \
  --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips "127.0.0.1,192.168.0.0/24" \
  > "$LOG_DIR/foreman-api.log" 2>&1 < /dev/null &
for _ in $(seq 1 60); do curl -sf localhost:8000/health >/dev/null && break; sleep 1; done
curl -s localhost:8000/health; echo
curl -s localhost:8000/demo; echo
tail -1 "$LOG_DIR/foreman-cp.log" | cut -c1-160
