#!/usr/bin/env bash
# docs/runbook.md의 ```bash check 블록을 순서대로 실행한다. 하나라도 실패하면 종료 코드 1.
# 전제: make docker-up (Postgres/Redis). 사용: scripts/check_runbook.sh [runbook.md]
set -uo pipefail
DOC="${1:-$(dirname "$0")/../docs/runbook.md}"
cd "$(dirname "$0")/.."
# 들여쓴 펜스(목록 안)도 허용: 펜스의 들여쓰기만큼 블록 줄에서 떼어낸다
blocks=$(awk '
  match($0, /^[[:space:]]*```bash check[[:space:]]*$/) {
    inb=1; n++; ind=index($0, "`") - 1; print "__BLOCK__ " n; next }
  /^[[:space:]]*```/ { if (inb) { inb=0; print "__END__" } ; next }
  inb { print substr($0, ind + 1) }
' "$DOC")
total=0; failed=0; current=""; name=""
while IFS= read -r line; do
  if [[ "$line" == __BLOCK__* ]]; then name="${line#__BLOCK__ }"; current=""; continue; fi
  if [[ "$line" == "__END__" ]]; then
    total=$((total + 1))
    echo "--- runbook block $name"
    if bash -euo pipefail -c "$current"; then
      echo "--- block $name: OK"
    else
      echo "--- block $name: FAIL"; failed=$((failed + 1))
    fi
    continue
  fi
  current+="$line"$'\n'
done <<< "$blocks"
echo "runbook: $((total - failed))/$total blocks passed"
[[ "$failed" -eq 0 && "$total" -gt 0 ]]
