#!/usr/bin/env bash
# 개발용 git "원격" = 로컬 bare repo (D-14). 사용: make_remote.sh <dir>
set -euo pipefail
dir="${1:?usage: make_remote.sh <dir>}"
git init --bare -q -b main "$dir"
echo "$dir"
