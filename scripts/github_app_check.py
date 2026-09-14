"""실 GitHub 연결 전 App 점검 (P7.1, D-42). 읽기 전용 — Issue/PR/브랜치를 만들지 않는다.

    uv run python scripts/github_app_check.py --repo owner/name

.env의 HITL_GITHUB_APP_ID / HITL_GITHUB_APP_PRIVATE_KEY / HITL_GITHUB_INSTALLATION_ID /
HITL_GITHUB_WEBHOOK_SECRET.
종료 코드 0 = 전부 [ok].
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from control_plane.config import Settings
from github_adapter.app_check import run_check
from github_adapter.client import GITHUB_API_BASE_URL


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name (App이 설치된 테스트 repo)")
    args = ap.parse_args()
    async with httpx.AsyncClient(base_url=GITHUB_API_BASE_URL, timeout=30) as http:
        report = await run_check(Settings(), http, repo=args.repo)
    print(report.render())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
