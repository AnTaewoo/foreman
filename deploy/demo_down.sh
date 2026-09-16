#!/usr/bin/env bash
# deploy/demo_up.sh로 띄운 API + control plane을 내린다. (systemd로 옮길 때 먼저 실행 — 8000 포트 충돌 방지)
# 패턴은 자기 자신의 명령줄과 겹치지 않게 grep -F 로만 찾는다.
find_pids() {
  ps -eo pid,args | grep -F -e "uvicorn control_plane.api.app:app" -e "-m control_plane" | grep -v grep | awk '{print $1}'
}
for pid in $(find_pids); do kill "$pid" 2>/dev/null || true; done
# uvicorn은 열린 WebSocket(콘솔 탭)이 있으면 graceful shutdown에서 끝없이 기다린다 → 5초 뒤 SIGKILL
for _ in 1 2 3 4 5; do [ -z "$(find_pids)" ] && break; sleep 1; done
for pid in $(find_pids); do kill -9 "$pid" 2>/dev/null || true; done
sleep 1
echo "remaining foreman processes: $(find_pids | wc -l)"
