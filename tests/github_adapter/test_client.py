"""P2.2 — REST client (red a~h): 메서드마다 정상 1 + 멱등 1. respx로 api.github.com mock."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from github_adapter import markers
from github_adapter.client import GitHubError, GitHubRestClient
from github_adapter.protocol import (
    BranchRef,
    CommentRef,
    EpicMilestone,
    GitHubClient,
    IssueRef,
    MilestoneRef,
    PrMeta,
    PullRef,
    TaskIssue,
)

REPO = "org/demo"
ALL_LABELS = {
    "ai:task",
    "role:coding", "role:test", "role:review", "role:research", "role:architect",
    "tier:T0", "tier:T1", "tier:T2", "tier:T3",
    "status:ready", "status:running", "status:blocked", "status:awaiting-decision",
    "kind:feature", "kind:bugfix", "kind:test", "kind:refactor", "kind:research",
    "kind:fix-from-review", "kind:fix-from-test",
    "human:override",
}  # fmt: skip


@pytest.fixture
def client(http: httpx.AsyncClient) -> GitHubRestClient:
    return GitHubRestClient(http)


def _task(task_id: str = "01TASK") -> TaskIssue:
    return TaskIssue(
        task_id=task_id, title="Add users endpoint", spec="## Spec\ndo it", role="coding",
        kind="fix_from_test", tier="T1", epic_number=3, milestone_number=7,
    )  # fmt: skip


def _body(req: httpx.Request) -> dict[str, Any]:
    return json.loads(req.content)  # type: ignore[no-any-return]


def test_protocol_is_runtime_checkable(client: GitHubRestClient) -> None:
    assert isinstance(client, GitHubClient)


# ---------------------------------------------------------------- markers
def test_markers_roundtrip() -> None:
    assert markers.issue_marker("01T") == "<!-- ai-platform:meta task=01T -->"
    assert markers.parse_issue_marker("intro\n<!-- ai-platform:meta task=01T -->\nbody") == "01T"
    assert markers.parse_issue_marker("no marker") is None
    meta = PrMeta(task_id="84", run_id="01J", agent_id="backend-2", tier="T2")
    block = markers.pr_meta_block(meta, summary="s", changes="c", tests="t", decisions=["#42"])
    assert block.startswith("<!-- ai-platform:meta task=84 run=01J agent=backend-2 tier=T2 -->")
    for section in (
        "### Summary",
        "### Changes",
        "### Tests",
        "### Decisions referenced",
        "### Checklist",
    ):
        assert section in block
    assert markers.parse_pr_meta(block) == meta
    assert markers.parse_pr_meta("plain body") is None
    assert markers.comment_marker("summary:01R") == "<!-- ai-platform:comment key=summary:01R -->"
    assert (
        markers.parse_comment_marker("x\n<!-- ai-platform:comment key=summary:01R -->")
        == "summary:01R"
    )


# ---------------------------------------------------------------- (a) ensure_labels
async def test_ensure_labels_creates_missing_only(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    existing = [{"name": "ai:task"}, {"name": "tier:T1"}, {"name": "unrelated"}]
    github_mock.get(f"/repos/{REPO}/labels").mock(return_value=httpx.Response(200, json=existing))
    post = github_mock.post(f"/repos/{REPO}/labels").mock(return_value=httpx.Response(201, json={}))
    created = await client.ensure_labels(REPO)
    assert set(created) == ALL_LABELS - {"ai:task", "tier:T1"}
    assert post.call_count == len(ALL_LABELS) - 2
    names = {_body(c.request)["name"] for c in post.calls}
    assert names == ALL_LABELS - {"ai:task", "tier:T1"}
    assert all(_body(c.request)["color"] for c in post.calls)


async def test_ensure_labels_idempotent(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    github_mock.get(f"/repos/{REPO}/labels").mock(
        return_value=httpx.Response(200, json=[{"name": n} for n in ALL_LABELS])
    )
    post = github_mock.post(f"/repos/{REPO}/labels")
    assert await client.ensure_labels(REPO) == []
    assert post.call_count == 0


# ---------------------------------------------------------------- (b) create_task_issue
async def test_create_task_issue(github_mock: respx.MockRouter, client: GitHubRestClient) -> None:
    github_mock.get(f"/repos/{REPO}/issues", params={"labels": "ai:task", "state": "all"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    post = github_mock.post(f"/repos/{REPO}/issues").mock(
        return_value=httpx.Response(201, json={"number": 12, "html_url": "https://gh/i/12"})
    )
    ref = await client.create_task_issue(REPO, _task())
    assert ref == IssueRef(number=12, url="https://gh/i/12", created=True)
    body = _body(post.calls[0].request)
    assert body["title"] == "Add users endpoint"
    assert body["body"].startswith("<!-- ai-platform:meta task=01TASK -->")
    assert "## Spec" in body["body"]
    assert set(body["labels"]) == {
        "ai:task",
        "role:coding",
        "kind:fix-from-test",
        "tier:T1",
        "epic:3",
    }
    assert body["milestone"] == 7


async def test_create_task_issue_idempotent_by_marker(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    listing = [
        {"number": 5, "html_url": "https://gh/i/5", "body": "<!-- ai-platform:meta task=OTHER -->"},
        {
            "number": 9,
            "html_url": "https://gh/i/9",
            "body": "x\n<!-- ai-platform:meta task=01TASK -->\ny",
        },
    ]
    github_mock.get(f"/repos/{REPO}/issues", params={"labels": "ai:task", "state": "all"}).mock(
        return_value=httpx.Response(200, json=listing)
    )
    post = github_mock.post(f"/repos/{REPO}/issues")
    ref = await client.create_task_issue(REPO, _task())
    assert ref == IssueRef(number=9, url="https://gh/i/9", created=False)
    assert post.call_count == 0


# ---------------------------------------------------------------- (c) update_issue_status_label
async def test_update_status_label_replaces(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    github_mock.get(f"/repos/{REPO}/issues/12").mock(
        return_value=httpx.Response(
            200,
            json={"labels": [{"name": "ai:task"}, {"name": "status:ready"}, {"name": "tier:T1"}]},
        )
    )
    put = github_mock.put(f"/repos/{REPO}/issues/12/labels").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert await client.update_issue_status_label(REPO, 12, "running") is True
    assert set(_body(put.calls[0].request)["labels"]) == {"ai:task", "status:running", "tier:T1"}


async def test_update_status_label_noop_when_same(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    github_mock.get(f"/repos/{REPO}/issues/12").mock(
        return_value=httpx.Response(200, json={"labels": [{"name": "status:running"}]})
    )
    put = github_mock.put(f"/repos/{REPO}/issues/12/labels")
    assert await client.update_issue_status_label(REPO, 12, "running") is False
    assert put.call_count == 0


# ---------------------------------------------------------------- (d) comment
async def test_comment_with_key_creates_once(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    listing = github_mock.get(f"/repos/{REPO}/issues/12/comments").mock(
        side_effect=[
            httpx.Response(200, json=[]),
            httpx.Response(
                200,
                json=[
                    {
                        "id": 77,
                        "html_url": "https://gh/c/77",
                        "body": "hi\n<!-- ai-platform:comment key=summary:R1 -->",
                    }
                ],
            ),
        ]
    )
    post = github_mock.post(f"/repos/{REPO}/issues/12/comments").mock(
        return_value=httpx.Response(201, json={"id": 77, "html_url": "https://gh/c/77"})
    )
    first = await client.comment(REPO, 12, "hi", key="summary:R1")
    assert first == CommentRef(id=77, url="https://gh/c/77", created=True)
    assert _body(post.calls[0].request)["body"].endswith(markers.comment_marker("summary:R1"))
    second = await client.comment(REPO, 12, "hi again", key="summary:R1")
    assert second == CommentRef(id=77, url="https://gh/c/77", created=False)
    assert post.call_count == 1 and listing.call_count == 2


async def test_comment_without_key_always_posts(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    post = github_mock.post(f"/repos/{REPO}/issues/12/comments").mock(
        return_value=httpx.Response(201, json={"id": 1, "html_url": "u"})
    )
    await client.comment(REPO, 12, "a")
    await client.comment(REPO, 12, "b")
    assert post.call_count == 2


# ---------------------------------------------------------------- (e) create_branch
async def test_create_branch(github_mock: respx.MockRouter, client: GitHubRestClient) -> None:
    github_mock.get(f"/repos/{REPO}/git/ref/heads/ai/epic/12-x").mock(
        return_value=httpx.Response(404, json={})
    )
    github_mock.get(f"/repos/{REPO}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "abc123"}})
    )
    post = github_mock.post(f"/repos/{REPO}/git/refs").mock(
        return_value=httpx.Response(
            201, json={"ref": "refs/heads/ai/epic/12-x", "object": {"sha": "abc123"}}
        )
    )
    ref = await client.create_branch(REPO, "ai/epic/12-x", "main")
    assert ref == BranchRef(name="ai/epic/12-x", sha="abc123", created=True)
    assert _body(post.calls[0].request) == {"ref": "refs/heads/ai/epic/12-x", "sha": "abc123"}


async def test_create_branch_exists(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    github_mock.get(f"/repos/{REPO}/git/ref/heads/ai/epic/12-x").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "def456"}})
    )
    post = github_mock.post(f"/repos/{REPO}/git/refs")
    ref = await client.create_branch(REPO, "ai/epic/12-x", "main")
    assert ref == BranchRef(name="ai/epic/12-x", sha="def456", created=False)
    assert post.call_count == 0


# ---------------------------------------------------------------- (f) open_pr
async def test_open_pr(github_mock: respx.MockRouter, client: GitHubRestClient) -> None:
    github_mock.get(
        f"/repos/{REPO}/pulls", params={"head": "org:ai/epic/12-x", "base": "main", "state": "open"}
    ).mock(return_value=httpx.Response(200, json=[]))
    post = github_mock.post(f"/repos/{REPO}/pulls").mock(
        return_value=httpx.Response(201, json={"number": 42, "html_url": "https://gh/p/42"})
    )
    meta = PrMeta(task_id="01TASK", run_id="01RUN", agent_id="coding-1", tier="T1")
    ref = await client.open_pr(REPO, "ai/epic/12-x", "main", "[T-12] x", "desc", True, meta)
    assert ref == PullRef(number=42, url="https://gh/p/42", created=True)
    body = _body(post.calls[0].request)
    assert body["draft"] is True and body["head"] == "ai/epic/12-x" and body["base"] == "main"
    assert body["body"].startswith(
        "<!-- ai-platform:meta task=01TASK run=01RUN agent=coding-1 tier=T1 -->"
    )
    assert "desc" in body["body"]


async def test_open_pr_existing(github_mock: respx.MockRouter, client: GitHubRestClient) -> None:
    github_mock.get(
        f"/repos/{REPO}/pulls", params={"head": "org:ai/epic/12-x", "base": "main", "state": "open"}
    ).mock(return_value=httpx.Response(200, json=[{"number": 40, "html_url": "https://gh/p/40"}]))
    post = github_mock.post(f"/repos/{REPO}/pulls")
    meta = PrMeta(task_id="01TASK", run_id="01RUN", agent_id="coding-1", tier="T1")
    ref = await client.open_pr(REPO, "ai/epic/12-x", "main", "t", "b", True, meta)
    assert ref == PullRef(number=40, url="https://gh/p/40", created=False)
    assert post.call_count == 0


# ---------------------------------------------------------------- (g) create_milestone
async def test_create_milestone(github_mock: respx.MockRouter, client: GitHubRestClient) -> None:
    github_mock.get(f"/repos/{REPO}/milestones", params={"state": "all"}).mock(
        return_value=httpx.Response(200, json=[{"number": 1, "title": "other"}])
    )
    post = github_mock.post(f"/repos/{REPO}/milestones").mock(
        return_value=httpx.Response(201, json={"number": 2, "title": "Caching"})
    )
    ref = await client.create_milestone(
        REPO, EpicMilestone(epic_id="E1", title="Caching", description="d")
    )
    assert ref == MilestoneRef(number=2, title="Caching", created=True)
    assert _body(post.calls[0].request) == {"title": "Caching", "description": "d"}


async def test_create_milestone_existing(
    github_mock: respx.MockRouter, client: GitHubRestClient
) -> None:
    github_mock.get(f"/repos/{REPO}/milestones", params={"state": "all"}).mock(
        return_value=httpx.Response(200, json=[{"number": 5, "title": "Caching"}])
    )
    post = github_mock.post(f"/repos/{REPO}/milestones")
    ref = await client.create_milestone(
        REPO, EpicMilestone(epic_id="E1", title="Caching", description="d")
    )
    assert ref == MilestoneRef(number=5, title="Caching", created=False)
    assert post.call_count == 0


# ---------------------------------------------------------------- (h) 4xx/5xx → GitHubError
@pytest.mark.parametrize("status", [403, 422, 500])
async def test_http_errors_raise_github_error(
    github_mock: respx.MockRouter, client: GitHubRestClient, status: int
) -> None:
    github_mock.get(f"/repos/{REPO}/labels").mock(
        return_value=httpx.Response(status, json={"message": "nope"})
    )
    with pytest.raises(GitHubError) as exc:
        await client.ensure_labels(REPO)
    assert exc.value.status == status and "nope" in str(exc.value)
