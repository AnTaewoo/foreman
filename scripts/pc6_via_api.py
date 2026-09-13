"""PC-6 — 상주 프로세스 + API로 Goal → 브랜치 → done 완주. 이 스크립트는 HTTP·git만 쓴다.

    make run-control-plane   # HITL_WORKER_LAUNCHER=inprocess (또는 docker), 같은 DB/Redis
    make run-api             # HITL_GITHUB_WEBHOOK_SECRET 설정
    uv run python scripts/pc6_via_api.py --api http://localhost:8000 --secret <s> <repo> "<goal>"

흐름: repo_path 사본을 git init(main) → POST /projects(repo=그 경로) → POST /goals →
awaiting_plan_approval → 서명된 discussion_comment `/approve`(Plan Discussion 번호) → Task 전부 done
(Dry 자동 머지) → 브랜치·pr.opened·체인 검증(DB 읽기 전용, `HITL_DATABASE_URL`).
relay/projection/scheduler/PrOpener/DryMerger는 조립하지 않는다 — 상주 프로세스의 몫.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

GENV = {
    "GIT_AUTHOR_NAME": "pc6",
    "GIT_AUTHOR_EMAIL": "pc6@x",
    "GIT_COMMITTER_NAME": "pc6",
    "GIT_COMMITTER_EMAIL": "pc6@x",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}
IGNORE = shutil.ignore_patterns(
    ".git", "dot_git_stub", ".venv", "node_modules", "__pycache__", ".pytest_cache"
)
TERMINAL = {"done", "blocked", "cancelled"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GENV
    ).stdout


def prepare_repo(src: Path, workdir: Path) -> Path:
    """비-bare 작업 clone: RepoCache는 그대로 쓰고, 워커는 여기서 clone 해 ai/* 브랜치를 push."""
    repo = workdir / "repo"
    shutil.copytree(src, repo, ignore=IGNORE)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed: pc6")
    git(repo, "config", "receive.denyCurrentBranch", "updateInstead")
    return repo


def signed(secret: str, body: bytes, delivery: str) -> dict[str, str]:
    return {
        "X-GitHub-Event": "discussion_comment",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": "sha256="
        + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        "Content-Type": "application/json",
    }


def approve_body(repo_full_name: str, number: int, author: str) -> bytes:
    payload = {
        "action": "created",
        "discussion": {
            "number": number,
            "title": f"Plan #{number}",
            "node_id": "D",
            "state": "open",
            "category": {"name": "Plans", "slug": "plans"},
            "user": {"login": "foreman[bot]", "type": "Bot"},
        },
        "comment": {
            "id": 1,
            "node_id": "DC",
            "body": "/approve",
            "html_url": "",
            "user": {"login": author, "type": "User"},
        },
        "repository": {"full_name": repo_full_name},
        "sender": {"login": author, "type": "User"},
    }
    return json.dumps(payload).encode()


async def wait_for(
    client: httpx.AsyncClient, url: str, pred: Any, timeout: float, label: str
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        r = await client.get(url)
        if r.status_code == 404 and time.monotonic() < deadline:  # projection 지연(§17 3)
            await asyncio.sleep(1)
            continue
        r.raise_for_status()
        data = r.json()
        line = f"  … {label}: {data.get('status')} tasks={data.get('tasks')}"
        if line != last:
            print(line)
            last = line
        if pred(data):
            return dict(data)
        if time.monotonic() > deadline:
            raise TimeoutError(f"{label}: {data}")
        await asyncio.sleep(2)


async def verify_chain(project_id: str) -> bool:
    """DB 읽기 전용 검증(서명·canonical은 API가 안 준다)."""
    from control_plane.config import Settings
    from control_plane.events.chain import verify_chain_db
    from control_plane.store import session as sess

    engine = sess.create_engine(Settings())
    factory = sess.create_session_factory(engine)
    async with factory() as s:
        ok = await verify_chain_db(s, project_id)
    await engine.dispose()
    return ok


async def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)  # 로그 파일로 보낼 때도 진행이 바로 보이게
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("goal")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--secret", required=True, help="API의 HITL_GITHUB_WEBHOOK_SECRET")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--plan-timeout", type=float, default=900)
    ap.add_argument("--tasks-timeout", type=float, default=3600)
    args = ap.parse_args()
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="pc6-"))
    workdir.mkdir(parents=True, exist_ok=True)
    repo = prepare_repo(Path(args.repo_path).resolve(), workdir)
    ok = True

    def check(cond: bool, label: str) -> None:
        nonlocal ok
        print(f"  [{'ok' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    async with httpx.AsyncClient(base_url=args.api, timeout=60) as c:
        assert (await c.get("/health")).json() == {"status": "ok"}
        r = await c.post(
            "/projects",
            json={
                "name": "pc6",
                "repo": str(repo),
                "members": [{"user_id": "alice", "role": "owner"}],
            },
            headers={"X-User-Id": "alice"},
        )
        r.raise_for_status()
        pid = r.json()["id"]
        print(f"=== 1. project {pid} repo={repo}")
        # 201은 outbox 저장까지. projection(상주 프로세스)이 반영해야 GET이 200 — 읽기 지연은 설계
        for _ in range(60):
            if (await c.get(f"/projects/{pid}")).status_code == 200:
                break
            await asyncio.sleep(0.5)
        else:
            raise TimeoutError("project not projected — control plane running?")
        r = await c.post(
            f"/projects/{pid}/goals",
            json={"title": args.goal, "description": args.goal},
            headers={"X-User-Id": "alice"},
        )
        r.raise_for_status()
        gid = r.json()["id"]
        print(f"=== 2. goal {gid} → Plan 대기")
        g = await wait_for(
            c,
            f"/projects/{pid}/goals/{gid}",
            lambda d: d["status"] in ("awaiting_plan_approval", "cancelled", "blocked"),
            args.plan_timeout,
            "plan",
        )
        check(
            g["status"] == "awaiting_plan_approval",
            f"goal awaiting_plan_approval (got {g['status']})",
        )
        number = g.get("plan_discussion_number") or 0
        print(f"=== 3. /approve on Discussion #{number}")
        body = approve_body(str(repo), int(number), "alice")
        r = await c.post(
            "/webhooks/github", content=body, headers=signed(args.secret, body, f"pc6-{pid}")
        )
        check(r.status_code == 202, f"webhook /approve → {r.status_code}")
        print("=== 4. Task 진행 (상주 프로세스가 배정·실행·PR·머지)")
        g = await wait_for(
            c,
            f"/projects/{pid}/goals/{gid}",
            lambda d: (
                d["total"] >= 1
                and sum(d["tasks"].get(s, 0) for s in TERMINAL) == d["total"]
                or d["status"] in ("cancelled", "blocked")
            ),
            args.tasks_timeout,
            "tasks",
        )
        tasks = (await c.get(f"/projects/{pid}/tasks")).json()["items"]
        print("=== 5. tasks")
        for t in tasks:
            print(
                f"  {t['status']:<10} #{t['issue_number']} {t['title']}  "
                f"branch={t['branch_name']} pr={t['pr_number']}"
            )
        events: list[dict[str, Any]] = []
        since = 0
        while True:
            page = (
                await c.get(f"/projects/{pid}/events", params={"since": since, "limit": 500})
            ).json()
            events += page["items"]
            if not page["items"] or page["next_since"] == since:
                break
            since = page["next_since"]
        by_type: dict[str, int] = {}
        for e in events:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        print("=== 6. events", dict(sorted(by_type.items())))
    heads = [
        b.strip("* ").strip() for b in git(repo, "branch", "--list").splitlines() if "ai/" in b
    ]
    print("=== 7. branches:", heads)
    chain_ok = await verify_chain(pid)
    total = len(tasks)
    check(total >= 1, f"tasks: {total} >= 1")
    check(all(t["status"] == "done" for t in tasks), "all tasks done")
    check(len(heads) >= total, f"branches {len(heads)} >= {total}")
    check(
        by_type.get("pr.opened", 0) == total, f"pr.opened {by_type.get('pr.opened', 0)} == {total}"
    )
    check(
        by_type.get("pr.merged", 0) == total,
        f"pr.merged (dry) {by_type.get('pr.merged', 0)} == {total}",
    )
    check(chain_ok, "verify_chain_db True")
    print("PC-6:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
