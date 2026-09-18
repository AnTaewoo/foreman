#!/usr/bin/env bash
# systemd 유닛과 같은 환경으로 API + control plane을 detached로 띄운다 (sudo 없이 쓰는 임시 실행, P9.4).
# 환경은 .env + HITL_DRY_RUN=false 뿐이다.
# 정식은 deploy/systemd/*.service. 내리려면 deploy/demo_down.sh. 로그: $LOG_DIR/foreman-{api,cp}.log
set -euo pipefail
cd "$(dirname "$0")/.."
# control plane은 워커 컨테이너를 띄운다(docker run) → 프로세스에 docker 그룹이 있어야 한다.
# 로그인 셸에 그룹이 안 잡혀 있으면(이 서버) newgrp로 자신을 다시 실행한다. 없으면 모든 Task가 launch_failed.
if ! id -nG | tr ' ' '\n' | grep -qx docker; then
  if [[ -z "${FOREMAN_NEWGRP:-}" ]]; then
    # 파이프라인 안의 exec는 서브셸만 바꾼다 → newgrp의 종료 코드로 여기서 끝낸다
    echo "FOREMAN_NEWGRP=1 exec $(printf '%q' "$PWD/deploy/demo_up.sh")" | newgrp docker
    exit $?
  fi
  echo "docker group unavailable even after newgrp — workers cannot start" >&2; exit 1
fi
LOG_DIR="${LOG_DIR:-$HOME/.foreman-logs}"; mkdir -p "$LOG_DIR"
# .env는 그대로(기본 dry-run). 실 GitHub는 PC-7 때부터 환경변수로만 켠다. 데모 모드는 .env에 HITL_DEMO_MODE=true를 넣을 때만
export HITL_DRY_RUN=false
if ss -ltn | grep -q ":8000 "; then echo "port 8000 is already in use — deploy/demo_down.sh 먼저" >&2; exit 1; fi
setsid nohup uv run --no-sync python -m control_plane > "$LOG_DIR/foreman-cp.log" 2>&1 < /dev/null &
setsid nohup uv run --no-sync uvicorn control_plane.api.app:app --factory \
  --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips "127.0.0.1,192.168.0.0/24" \
  > "$LOG_DIR/foreman-api.log" 2>&1 < /dev/null &
for _ in $(seq 1 60); do curl -sf localhost:8000/health >/dev/null && break; sleep 1; done
curl -s localhost:8000/health; echo
curl -s localhost:8000/demo; echo
tail -1 "$LOG_DIR/foreman-cp.log" | cut -c1-160
docker version --format 'docker ok (server {{.Server.Version}})' || echo "WARNING: docker API not reachable — workers will fail" >&2
