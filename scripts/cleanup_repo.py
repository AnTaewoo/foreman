"""테스트 repo 정리 (P7.4, D-42): 마커 있는 Issue/PR 닫기 + ai/* 브랜치 삭제. 기본은 **목록만**.

uv run python scripts/cleanup_repo.py owner/name          # 계획만 출력
uv run python scripts/cleanup_repo.py owner/name --apply  # 실제로 닫고 지운다
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from control_plane.config import Settings
from github_adapter import make_installation_http
from github_adapter.cleanup import apply_cleanup, plan_cleanup
from github_adapter.client import GitHubRestClient


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    async with make_installation_http(Settings()) as http:
        client = GitHubRestClient(http)
        plan = await plan_cleanup(client, args.repo)
        print(f"=== cleanup plan for {args.repo} ({plan.total} items)")
        print(plan.render())
        if not args.apply:
            print("(dry) --apply 를 주면 실제로 닫고 지운다")
            return 0
        result = await apply_cleanup(client, args.repo, plan)
        print(f"closed issues {result.closed_issues}, pulls {result.closed_pulls}, "
              f"deleted {result.deleted_branches}, skipped {result.skipped}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
