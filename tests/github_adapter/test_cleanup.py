"""P7.4 — 테스트 repo 시드 + 정리 (D-42, red a~e). 실 호출 0(respx); push 대상은 로컬 bare.

REST 경로(2026-09-14 docs.github.com 확인): GET /repos/{o}/{r}/issues (PR은 pull_request 키로 구분),
PATCH /repos/{o}/{r}/issues/{n} {state: closed}, PATCH /repos/{o}/{r}/pulls/{n} {state: closed},
GET /repos/{o}/{r}/git/matching-refs/heads/ai/, DELETE /repos/{o}/{r}/git/refs/heads/<branch>,
GET /repos/{o}/{r}/commits (빈 repo는 409).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from github_adapter import markers
from github_adapter.client import GitHubRestClient
from github_adapter.protocol import PrMeta

REPO = "org/demo"
ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
GENV = {
    "GIT_AUTHOR_NAME": "s",
    "GIT_AUTHOR_EMAIL": "s@x",
    "GIT_COMMITTER_NAME": "s",
    "GIT_COMMITTER_EMAIL": "s@x",
    "PATH": "/usr/bin:/bin",
}


def mock_repo_items(router: respx.MockRouter) -> None:
    meta = markers.pr_meta_block(
        PrMeta(task_id="T1", run_id="R1", agent_id="a", tier="T1"), summary="s"
    )
    router.get(f"/repos/{REPO}/issues").mock(
        return_value=httpx.Response(200, json=[
            {"number": 1, "body": markers.issue_marker("T1") + "\n\nspec", "html_url": ""},
            {"number": 2, "body": "human issue, no marker", "html_url": ""},
            {"number": 3, "body": meta, "html_url": "", "pull_request": {"url": "x"}},
            {"number": 4, "body": "human PR", "html_url": "", "pull_request": {"url": "x"}},
        ])
    )  # fmt: skip
    router.get(f"/repos/{REPO}/git/matching-refs/heads/ai/").mock(
        return_value=httpx.Response(200, json=[
            {"ref": "refs/heads/ai/users-api/1-add", "object": {"sha": "a" * 40}},
            {"ref": "refs/heads/ai/users-api/2-fix", "object": {"sha": "b" * 40}},
        ])
    )  # fmt: skip


# (a) 계획: 마커 있는 Issue/PR과 ai/* 브랜치만
async def test_plan_lists_only_platform_items(
    github_mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    from github_adapter.cleanup import plan_cleanup

    mock_repo_items(github_mock)
    plan = await plan_cleanup(GitHubRestClient(http), REPO)
    assert plan.issues == [1] and plan.pulls == [3]
    assert plan.branches == ["ai/users-api/1-add", "ai/users-api/2-fix"]
    assert plan.total == 4 and "human" not in plan.render()


# (b) --apply 없이는 쓰기 호출 0; --apply면 계획 항목만 닫고 지운다 (422는 이미 없음으로 허용)
async def test_apply_only_with_flag(github_mock: respx.MockRouter, http: httpx.AsyncClient) -> None:
    from github_adapter.cleanup import apply_cleanup, plan_cleanup

    mock_repo_items(github_mock)
    close1 = github_mock.patch(f"/repos/{REPO}/issues/1").mock(
        return_value=httpx.Response(200, json={})
    )
    close3 = github_mock.patch(f"/repos/{REPO}/pulls/3").mock(
        return_value=httpx.Response(200, json={})
    )
    del1 = github_mock.delete(f"/repos/{REPO}/git/refs/heads/ai/users-api/1-add").mock(
        return_value=httpx.Response(204)
    )
    del2 = github_mock.delete(f"/repos/{REPO}/git/refs/heads/ai/users-api/2-fix").mock(
        return_value=httpx.Response(422, json={"message": "Reference does not exist"})
    )
    client = GitHubRestClient(http)
    plan = await plan_cleanup(client, REPO)
    assert close1.call_count == 0 and del1.call_count == 0
    result = await apply_cleanup(client, REPO, plan)
    assert close1.call_count == 1 and json.loads(close1.calls[0].request.content) == {
        "state": "closed",
        "state_reason": "not_planned",
    }
    assert close3.call_count == 1 and json.loads(close3.calls[0].request.content) == {
        "state": "closed"
    }
    assert del1.call_count == 1 and del2.call_count == 1
    assert result.closed_issues == [1] and result.closed_pulls == [3]
    assert result.deleted_branches == ["ai/users-api/1-add"] and result.skipped == [
        "ai/users-api/2-fix"
    ]
    # 사람 Issue #2 / PR #4에는 어떤 호출도 없다
    assert not any(
        "/issues/2" in str(c.request.url) or "/pulls/4" in str(c.request.url)
        for c in github_mock.calls
    )


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    remote = tmp_path / "origin.git"
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(remote)], check=True, capture_output=True)
    return remote


# (d) 빈 repo(commits 409) → 픽스처를 default 브랜치로 push
async def test_seed_pushes_into_empty_repo(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, bare: Path, tmp_path: Path
) -> None:
    from github_adapter.cleanup import seed_repo

    github_mock.get(f"/repos/{REPO}").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    github_mock.get(f"/repos/{REPO}/commits").mock(
        return_value=httpx.Response(409, json={"message": "Git Repository is empty."})
    )
    result = await seed_repo(
        GitHubRestClient(http),
        REPO,
        FIXTURES / "sample_repo",
        url_for=lambda r: str(bare),
        workdir=tmp_path / "w",
        git_env=GENV,
    )
    assert result.pushed and result.branch == "main"
    log = subprocess.run(
        ["git", "log", "--format=%s", "main"], cwd=bare, capture_output=True, text=True, env=GENV
    ).stdout
    assert "seed" in log.lower()
    assert (
        "README.md"
        in subprocess.run(
            ["git", "ls-tree", "--name-only", "main"],
            cwd=bare,
            capture_output=True,
            text=True,
            env=GENV,
        ).stdout
    )


# (e) 비어 있지 않으면 push 없이 중단
async def test_seed_aborts_on_nonempty_repo(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, bare: Path, tmp_path: Path
) -> None:
    from github_adapter.cleanup import seed_repo

    github_mock.get(f"/repos/{REPO}").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    github_mock.get(f"/repos/{REPO}/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40}])
    )
    result = await seed_repo(
        GitHubRestClient(http),
        REPO,
        FIXTURES / "sample_repo",
        url_for=lambda r: str(bare),
        workdir=tmp_path / "w",
        git_env=GENV,
    )
    assert not result.pushed and "not empty" in result.reason
    assert (
        subprocess.run(
            ["git", "branch", "--list"], cwd=bare, capture_output=True, text=True, env=GENV
        ).stdout.strip()
        == ""
    )
