"""P2.5 — DryRun client 2개 + 팩토리 (red a~g). respx autouse가 켜져 있으므로 네트워크 0이 강제된다."""

from __future__ import annotations

import pytest
import structlog
from structlog.testing import capture_logs

from control_plane.config import Settings
from github_adapter import get_discussions_client, get_github_client
from github_adapter.client import GitHubRestClient
from github_adapter.discussions import DiscussionsClient
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient
from github_adapter.protocol import EpicMilestone, GitHubClient, PrMeta, TaskIssue

REPO = "org/demo"


def _task(task_id: str = "01T", title: str = "t") -> TaskIssue:
    return TaskIssue(
        task_id=task_id, title=title, spec="s", role="coding", kind="feature", tier="T1"
    )


@pytest.fixture
def dry() -> DryRunGitHubClient:
    return DryRunGitHubClient()


# (a) Protocol 만족 (runtime; mypy는 gate에서)
def test_dry_run_satisfies_protocol(dry: DryRunGitHubClient) -> None:
    assert isinstance(dry, GitHubClient)
    client: GitHubClient = dry  # mypy 구조적 검사
    assert client is dry


# (b) would 로그 + 결정적 번호 (Issue/PR 같은 번호 공간)
async def test_writes_log_would_and_number_deterministically(dry: DryRunGitHubClient) -> None:
    with capture_logs() as logs:
        issue = await dry.create_task_issue(REPO, _task("01T"))
        pr = await dry.open_pr(
            REPO,
            "ai/e/1-x",
            "main",
            "t",
            "b",
            True,
            PrMeta(task_id="01T", run_id="R", agent_id="a", tier="T1"),
        )
        ms = await dry.create_milestone(REPO, EpicMilestone(epic_id="E1", title="Epic"))
        labels = await dry.ensure_labels(REPO)
        branch = await dry.create_branch(REPO, "ai/e/1-x", "main")
        changed = await dry.update_issue_status_label(REPO, issue.number, "running")
        comment = await dry.comment(REPO, issue.number, "hi", key="summary:R")
    assert issue.number == 1 and issue.created is True
    assert pr.number == 2 and pr.created is True  # Issue와 같은 번호 공간
    assert ms.number == 1 and ms.created is True
    assert len(labels) == 22 and branch.created is True and changed is True
    assert comment.id == 1 and comment.created is True
    events = [e["event"] for e in logs]
    assert any(e.startswith("would create_task_issue") for e in events)
    assert any(e.startswith("would open_pr") for e in events)
    assert any(e.startswith("would create_milestone") for e in events)
    assert any(e.startswith("would ensure_labels") for e in events)
    assert any(e.startswith("would create_branch") for e in events)
    assert any(e.startswith("would update_issue_status_label") for e in events)
    assert any(e.startswith("would comment") for e in events)


# (c) 멱등
async def test_idempotent_writes(dry: DryRunGitHubClient) -> None:
    a = await dry.create_task_issue(REPO, _task("01T"))
    b = await dry.create_task_issue(REPO, _task("01T", title="renamed"))
    assert a.number == b.number and b.created is False
    meta = PrMeta(task_id="01T", run_id="R", agent_id="a", tier="T1")
    p1 = await dry.open_pr(REPO, "h", "main", "t", "b", True, meta)
    p2 = await dry.open_pr(REPO, "h", "main", "t2", "b2", False, meta)
    assert p1.number == p2.number and p2.created is False
    m1 = await dry.create_milestone(REPO, EpicMilestone(epic_id="E1", title="Epic"))
    m2 = await dry.create_milestone(REPO, EpicMilestone(epic_id="E2", title="Epic"))
    assert m1.number == m2.number and m2.created is False
    c1 = await dry.comment(REPO, a.number, "x", key="k")
    c2 = await dry.comment(REPO, a.number, "y", key="k")
    assert c1.id == c2.id and c2.created is False
    c3 = await dry.comment(REPO, a.number, "z")  # key 없음 → 새 코멘트
    assert c3.id != c1.id and c3.created is True
    assert (await dry.create_branch(REPO, "b", "main")).created is True
    assert (await dry.create_branch(REPO, "b", "main")).created is False
    assert await dry.ensure_labels(REPO) == []  # 두 번째는 없음
    assert await dry.update_issue_status_label(REPO, a.number, "ready") is True
    assert await dry.update_issue_status_label(REPO, a.number, "ready") is False


# (d) 네트워크 0 — respx autouse(assert_all_mocked) 아래에서 위 테스트가 통과하는 것 자체가 증거.
async def test_no_network(dry: DryRunGitHubClient) -> None:
    await dry.create_task_issue(REPO, _task())
    await dry.open_pr(
        REPO,
        "h",
        "main",
        "t",
        "b",
        True,
        PrMeta(task_id="01T", run_id="R", agent_id="a", tier="T1"),
    )


# (e) snapshot
async def test_snapshot(dry: DryRunGitHubClient) -> None:
    issue = await dry.create_task_issue(REPO, _task("01T"))
    await dry.update_issue_status_label(REPO, issue.number, "running")
    await dry.comment(REPO, issue.number, "hello", key="summary:R")
    await dry.create_branch(REPO, "ai/e/1-x", "main")
    snap = dry.snapshot()
    repo = snap["repos"][REPO]
    assert repo["issues"][1]["task_id"] == "01T"
    assert "status:running" in repo["issues"][1]["labels"]
    assert repo["issues"][1]["comments"][0]["key"] == "summary:R"
    assert "ai/e/1-x" in repo["branches"]
    assert repo["pulls"] == {}


# (f) Discussions dry
async def test_dry_discussions() -> None:
    d = DryRunDiscussionsClient()
    with capture_logs() as logs:
        ref = await d.create_discussion(REPO, "Plans", "Plan #1", "body")
        again = await d.create_discussion(REPO, "Plans", "Plan #1", "body2")
        c = await d.add_discussion_comment(ref.id, "/approve")
    assert ref.number == 1 and ref.created is True and again.number == 1 and again.created is False
    assert [x.number for x in await d.list_discussions(REPO, "plans")] == [1]
    with pytest.raises(LookupError):
        await d.create_discussion(REPO, "Nope", "t", "b")
    assert c.id and any(e["event"].startswith("would create_discussion") for e in logs)
    assert d.snapshot()["repos"][REPO]["discussions"][1]["comments"] == ["/approve"]


# (g) 팩토리
def test_factory_dry_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.dry_run is True
    assert isinstance(get_github_client(settings), DryRunGitHubClient)
    assert isinstance(get_discussions_client(settings), DryRunDiscussionsClient)


def test_factory_real_requires_installation_and_creds(private_key_pem: str) -> None:
    settings = Settings(
        _env_file=None, dry_run=False, github_app_id="1", github_app_private_key=private_key_pem
    )
    with pytest.raises(ValueError, match="installation"):
        get_github_client(settings)
    real = get_github_client(settings, installation_id=7)
    assert isinstance(real, GitHubRestClient)
    assert isinstance(get_discussions_client(settings, installation_id=7), DiscussionsClient)
    missing = Settings(_env_file=None, dry_run=False)
    with pytest.raises(ValueError, match="github_app"):
        get_github_client(missing, installation_id=7)


def test_structlog_available() -> None:
    assert structlog.get_logger("x") is not None
