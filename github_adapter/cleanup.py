"""테스트 repo 시드 + 정리 (P7.4, D-42). 마커(``ai-platform:``)가 없는 것은 절대 건드리지 않는다.

- ``plan_cleanup``: 열린 Issue/PR 중 마커 있는 것 + ``ai/*`` 브랜치를 **목록만**.
- ``apply_cleanup``: 계획 항목만 닫고 지운다. 이미 없는 브랜치(422)는 건너뛴다.
- ``seed_repo``: 비어 있는 repo에만 픽스처를 default 브랜치로 push (토큰 URL은 push 인자로만).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from github_adapter import markers
from github_adapter.client import GitHubRestClient

log = structlog.get_logger(__name__)
IGNORE = shutil.ignore_patterns(".git", "dot_git_stub", ".venv", "node_modules", "__pycache__")
GIT_ENV_DEFAULT: dict[str, str] = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "foreman-seed",
    "GIT_AUTHOR_EMAIL": "seed@foreman.local",
    "GIT_COMMITTER_NAME": "foreman-seed",
    "GIT_COMMITTER_EMAIL": "seed@foreman.local",
}


@dataclass
class CleanupPlan:
    issues: list[int] = field(default_factory=list)
    pulls: list[int] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.issues) + len(self.pulls) + len(self.branches)

    def render(self) -> str:
        return "\n".join(
            [
                f"  issues to close ({len(self.issues)}): {self.issues}",
                f"  pulls to close  ({len(self.pulls)}): {self.pulls}",
                f"  branches to delete ({len(self.branches)}): {self.branches}",
            ]
        )


@dataclass
class CleanupResult:
    closed_issues: list[int] = field(default_factory=list)
    closed_pulls: list[int] = field(default_factory=list)
    deleted_branches: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


async def plan_cleanup(client: GitHubRestClient, repo: str) -> CleanupPlan:
    plan = CleanupPlan()
    for item in await client.list_open_items(repo):
        number = int(item["number"])
        body = item.get("body")
        if "pull_request" in item:
            if markers.parse_pr_meta(body) is not None:
                plan.pulls.append(number)
        elif markers.parse_issue_marker(body) is not None:
            plan.issues.append(number)
    plan.branches = await client.list_refs(repo, "heads/ai/")
    return plan


async def apply_cleanup(client: GitHubRestClient, repo: str, plan: CleanupPlan) -> CleanupResult:
    result = CleanupResult()
    for n in plan.issues:
        await client.close_issue(repo, n)
        result.closed_issues.append(n)
    for n in plan.pulls:
        await client.close_pull(repo, n)
        result.closed_pulls.append(n)
    for b in plan.branches:
        (result.deleted_branches if await client.delete_ref(repo, b) else result.skipped).append(b)
    log.info("cleanup.applied", repo=repo, **{k: len(v) for k, v in vars(result).items()})
    return result


@dataclass
class SeedResult:
    pushed: bool
    branch: str
    reason: str = ""


async def seed_repo(
    client: GitHubRestClient,
    repo: str,
    source: Path,
    *,
    url_for: Callable[[str], str],
    workdir: Path,
    git_env: Mapping[str, str] | None = None,
) -> SeedResult:
    """``source``(픽스처)를 ``repo``의 default 브랜치로 push. repo가 비어 있지 않으면 중단."""
    info = await client._request("GET", f"/repos/{repo}")
    branch = str(info.get("default_branch") or "main")
    count = await client.commit_count_hint(repo)
    if count is None or count > 0:
        return SeedResult(False, branch, f"{repo} is not empty (commits: {count}) — seed aborted")
    env = dict(git_env or GIT_ENV_DEFAULT)
    work = Path(workdir) / "seed"
    shutil.copytree(source, work, ignore=IGNORE)

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=work, check=True, capture_output=True, env=env)

    run("init", "-q", "-b", branch)
    run("add", "-A")
    run("commit", "-q", "-m", "seed: foreman sample_repo")
    r = subprocess.run(
        ["git", "push", url_for(repo), f"HEAD:refs/heads/{branch}"],
        cwd=work, capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip
    if r.returncode != 0:
        return SeedResult(False, branch, "push failed: " + r.stderr.strip()[-200:])
    log.info("seed.pushed", repo=repo, branch=branch)
    return SeedResult(True, branch)
