"""P9.11 — Plans 카테고리(또는 Discussions)가 없으면 Plan을 Issue로 게시하고 그 Issue의 /approve로 승인."""

from __future__ import annotations

import json

import httpx
import respx
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from control_plane.api.goals import plan_url
from control_plane.orchestrator.graph import build_graph
from control_plane.store import models as m
from github_adapter import markers
from github_adapter.client import GitHubRestClient
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient
from tests.orchestrator.test_graph import CFG, DECOMPOSE_JSON, PLAN_JSON, make, state0

API = "https://api.github.com"


async def test_plan_posted_as_issue_without_plans_category() -> None:
    deps, sink, _, _ = make([PLAN_JSON, DECOMPOSE_JSON])
    deps.discussions = DryRunDiscussionsClient(categories=("General",))
    gh = deps.github
    assert isinstance(gh, DryRunGitHubClient)
    graph = build_graph(deps, checkpointer=MemorySaver())
    await graph.ainvoke(state0(), CFG)
    ev = sink.events[0]
    assert ev.type.value == "goal.plan_proposed" and ev.payload["plan_issue"] is True
    number = ev.payload["plan_discussion_number"]
    issue = gh.snapshot()["repos"]["org/demo"]["issues"][number]
    assert issue["title"].startswith("Plan #1 (Goal #G1)")
    assert markers.parse_plan_marker(issue["body"]) == "G1-1"
    assert "/approve" in issue["body"] and "## Plan for Goal" in issue["body"]
    # 승인은 기존 경로 그대로 (interrupt 값의 번호 = Issue 번호)
    out = await graph.ainvoke(Command(resume={"approved": True, "by": "judge"}), CFG)
    assert out.get("error") is None and "goal.activated" in sink.types()


async def test_dry_plan_issue_idempotent() -> None:
    gh = DryRunGitHubClient()
    a = await gh.create_plan_issue("org/demo", "G1-1", "Plan #1", "body")
    b = await gh.create_plan_issue("org/demo", "G1-1", "Plan #1", "body")
    assert a.created and not b.created and a.number == b.number


async def test_rest_plan_issue_idempotent_by_marker() -> None:
    with respx.mock(base_url=API, assert_all_mocked=True) as router:
        listing = router.get("/repos/org/demo/issues", params={"state": "all"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        post = router.post("/repos/org/demo/issues").mock(
            return_value=httpx.Response(201, json={"number": 4, "html_url": "https://gh/i/4"})
        )
        async with httpx.AsyncClient(base_url=API) as http:
            client = GitHubRestClient(http)
            ref = await client.create_plan_issue("org/demo", "G1-1", "Plan #1", "md")
            assert ref.number == 4 and ref.created
            sent = json.loads(post.calls[0].request.content)
            assert sent["title"] == "Plan #1" and sent["body"].startswith(
                markers.plan_marker("G1-1")
            )
            listing.mock(
                return_value=httpx.Response(
                    200, json=[{"number": 4, "html_url": "u", "body": sent["body"]}]
                )
            )
            again = await client.create_plan_issue("org/demo", "G1-1", "Plan #1", "md")
            assert again.number == 4 and not again.created and post.call_count == 1


def test_plan_url_kind() -> None:
    g = m.Goal(id="G1", plan_discussion_id=7)
    assert plan_url("acme/demo", g) == "https://github.com/acme/demo/discussions/7"
    g.plan_kind = "issue"
    assert plan_url("acme/demo", g) == "https://github.com/acme/demo/issues/7"
    assert plan_url("/local/path", g) is None
