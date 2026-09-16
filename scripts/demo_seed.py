"""공개 데모 준비 (P9.6, D-52): 데모 프로젝트 + showcase Goal을 API로 만든다. 관리 토큰 필요.

uv run python scripts/demo_seed.py --repo AnTaewoo/foreman_test --owner AnTaewoo
    --api http://127.0.0.1:8000 (기본)
    --admin-token $HITL_ADMIN_TOKEN (기본: 환경변수 HITL_ADMIN_TOKEN)
    --showcase "[Showcase] Add a maths helpers module with add and mul functions and tests"  (기본)
    --no-showcase   프로젝트만
    --cleanup       먼저 scripts/cleanup_repo.py --apply (마커 있는 Issue/PR 닫기 + ai/* 삭제)

이미 같은 repo의 프로젝트가 있으면(409) 그것을 쓴다. DB 초기화는 하지 않는다(docs/deploy.md).
끝나면 콘솔에서 showcase Goal을 Approve 하고, PR은 repo 소유자가 GitHub에서 머지한다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys

import httpx

DEFAULT_SHOWCASE = "[Showcase] Add a maths helpers module with add and mul functions and tests"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name (GitHub App이 설치된 공개 repo)")
    ap.add_argument("--owner", required=True, help="repo 소유자 GitHub login (members owner)")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--admin-token", default=os.environ.get("HITL_ADMIN_TOKEN", ""))
    ap.add_argument("--judge", default="judge", help="콘솔의 X-User-Id (approver)")
    ap.add_argument("--showcase", default=DEFAULT_SHOWCASE)
    ap.add_argument("--no-showcase", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    if not args.admin_token:
        print("admin token이 없다: --admin-token 또는 HITL_ADMIN_TOKEN", file=sys.stderr)
        return 64

    if args.cleanup:
        cmd = [sys.executable, "scripts/cleanup_repo.py", args.repo, "--apply"]
        print("==", " ".join(cmd))
        subprocess.run(cmd, check=True)

    admin = {"X-Admin-Token": args.admin_token, "X-User-Id": args.owner}
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as c:
        r = await c.post(
            "/projects",
            headers=admin,
            json={
                "name": "foreman demo",
                "repo": args.repo,
                "members": [
                    {"user_id": args.owner, "role": "owner"},
                    {"user_id": args.judge, "role": "approver"},
                ],
            },
        )
        if r.status_code == 409:
            pid = ""
            for _ in range(20):  # 읽기는 projection 반영 후 (control plane이 떠 있어야 한다)
                items = (await c.get("/projects")).json()["items"]
                pid = next((p["id"] for p in items if p["repo"] == args.repo), "")
                if pid:
                    break
                await asyncio.sleep(0.5)
            if not pid:
                print(
                    "project exists but is not projected yet — is the control plane running?",
                    file=sys.stderr,
                )
                return 1
            print(f"project exists: {pid}")
        elif r.status_code == 201:
            pid = r.json()["id"]
            print(f"project created: {pid} ({args.repo})")
        else:
            print(f"POST /projects failed: {r.status_code} {r.text}", file=sys.stderr)
            return 1
        if args.no_showcase:
            return 0
        r = await c.post(
            f"/projects/{pid}/goals",
            headers={"X-User-Id": args.owner},
            json={"title": args.showcase},
        )
        if r.status_code != 202:
            print(f"POST goals failed: {r.status_code} {r.text}", file=sys.stderr)
            return 1
        gid = r.json()["id"]
        print(f"showcase goal: {gid}")
        print(f"→ 콘솔 {args.api}/ 에서 Plan이 뜨면 Approve, PR은 GitHub에서 머지 (README §3)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
