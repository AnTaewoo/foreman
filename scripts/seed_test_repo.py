"""테스트 repo 시드 (P7.4, D-42): 비어 있는 repo에 sample_repo 픽스처를 default 브랜치로 push.

uv run python scripts/seed_test_repo.py owner/name [--source tests/fixtures/sample_repo]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from control_plane.config import Settings
from control_plane.repo_cache import token_url
from github_adapter import make_installation_http, make_token_provider
from github_adapter.cleanup import seed_repo
from github_adapter.client import GitHubRestClient


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--source", default="tests/fixtures/sample_repo")
    args = ap.parse_args()
    settings = Settings()
    provider = make_token_provider(settings)
    token = await provider.token()
    async with make_installation_http(settings) as http:
        result = await seed_repo(
            GitHubRestClient(http),
            args.repo,
            Path(args.source),
            url_for=lambda r: token_url(r, token),
            workdir=Path(tempfile.mkdtemp(prefix="seed-")),
        )
    print(f"seed {args.repo}: {'pushed to ' + result.branch if result.pushed else result.reason}")
    return 0 if result.pushed else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
