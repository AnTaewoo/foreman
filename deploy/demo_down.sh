#!/usr/bin/env bash
# deploy/demo_up.sh로 띄운 API + control plane을 내린다. (systemd로 옮길 때 먼저 실행 — 8000 포트 충돌 방지)
# 패턴은 자기 자신의 명령줄과 겹치지 않게 grep -F 로만 찾는다.
for pat in "uvicorn control_plane.api.app:app" "python -m control_plane"; do
  for pid in $(ps -eo pid,args | grep -F "$pat" | grep -v grep | awk '{print $1}'); do kill "$pid" 2>/dev/null || true; done
done
sleep 2
left=$(ps -eo pid,args | grep -F -e "uvicorn control_plane.api.app:app" -e "python -m control_plane" | grep -v grep | wc -l)
echo "remaining foreman processes: $left"
