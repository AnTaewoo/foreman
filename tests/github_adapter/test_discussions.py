"""P2.3 — GraphQL Discussions (red a~d). respx POST /graphql."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from github_adapter.discussions import (
    DiscussionCommentRef,
    DiscussionRef,
    DiscussionsClient,
    GitHubGraphQLError,
)

REPO = "org/demo"
REPO_META = {
    "data": {
        "repository": {
            "id": "R_1",
            "discussionCategories": {
                "nodes": [
                    {"id": "DC_plans", "name": "Plans", "slug": "plans"},
                    {"id": "DC_props", "name": "Proposals", "slug": "proposals"},
                ]
            },
        }
    }
}


def _page(nodes: list[dict[str, Any]], end_cursor: str | None, has_next: bool) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "discussions": {
                    "pageInfo": {"endCursor": end_cursor, "hasNextPage": has_next},
                    "nodes": nodes,
                }
            }
        }
    }


def _node(n: int, title: str) -> dict[str, Any]:
    return {
        "id": f"D_{n}", "number": n, "title": title, "body": "b", "url": f"https://gh/d/{n}",
        "category": {"id": "DC_plans", "name": "Plans"},
    }  # fmt: skip


def _req(call: Any) -> dict[str, Any]:
    return json.loads(call.request.content)  # type: ignore[no-any-return]


@pytest.fixture
def client(http: httpx.AsyncClient) -> DiscussionsClient:
    return DiscussionsClient(http)


# (a) create: 조회 1 + mutation 1 / 같은 제목이면 mutation 0 / 없는 카테고리 → LookupError
async def test_create_discussion(github_mock: respx.MockRouter, client: DiscussionsClient) -> None:
    route = github_mock.post("/graphql").mock(
        side_effect=[
            httpx.Response(200, json=REPO_META),
            httpx.Response(200, json=_page([], None, False)),
            httpx.Response(
                200,
                json={
                    "data": {
                        "createDiscussion": {
                            "discussion": {
                                "id": "D_9",
                                "number": 9,
                                "title": "Plan #1",
                                "url": "https://gh/d/9",
                            }
                        }
                    }
                },
            ),
        ]
    )
    ref = await client.create_discussion(REPO, "Plans", "Plan #1", "body")
    assert ref == DiscussionRef(
        id="D_9", number=9, title="Plan #1", url="https://gh/d/9", created=True
    )
    assert route.call_count == 3
    meta_q = _req(route.calls[0])
    assert "discussionCategories" in meta_q["query"] and meta_q["variables"] == {
        "owner": "org",
        "name": "demo",
    }
    list_q = _req(route.calls[1])
    assert list_q["variables"]["categoryId"] == "DC_plans"
    mut = _req(route.calls[2])
    assert "createDiscussion" in mut["query"]
    assert mut["variables"] == {
        "repositoryId": "R_1",
        "categoryId": "DC_plans",
        "title": "Plan #1",
        "body": "body",
    }


async def test_create_discussion_idempotent_by_title(
    github_mock: respx.MockRouter, client: DiscussionsClient
) -> None:
    route = github_mock.post("/graphql").mock(
        side_effect=[
            httpx.Response(200, json=REPO_META),
            httpx.Response(200, json=_page([_node(3, "Other"), _node(4, "Plan #1")], None, False)),
        ]
    )
    ref = await client.create_discussion(REPO, "Plans", "Plan #1", "body")
    assert ref == DiscussionRef(
        id="D_4", number=4, title="Plan #1", url="https://gh/d/4", created=False
    )
    assert route.call_count == 2  # mutation 0


async def test_create_discussion_unknown_category(
    github_mock: respx.MockRouter, client: DiscussionsClient
) -> None:
    github_mock.post("/graphql").mock(return_value=httpx.Response(200, json=REPO_META))
    with pytest.raises(LookupError, match="Reports"):
        await client.create_discussion(REPO, "Reports", "t", "b")


# (b) list: 카테고리 조회 1(캐시) + 페이지 N
async def test_list_discussions_paginates_and_caches_categories(
    github_mock: respx.MockRouter, client: DiscussionsClient
) -> None:
    route = github_mock.post("/graphql").mock(
        side_effect=[
            httpx.Response(200, json=REPO_META),
            httpx.Response(200, json=_page([_node(1, "a"), _node(2, "b")], "c1", True)),
            httpx.Response(200, json=_page([_node(3, "c")], None, False)),
            # 두 번째 list: 카테고리 재조회 없음
            httpx.Response(200, json=_page([_node(3, "c")], None, False)),
        ]
    )
    items = await client.list_discussions(REPO, "plans")  # slug로도 찾는다
    assert [d.number for d in items] == [1, 2, 3]
    assert all(d.created is False for d in items)
    assert _req(route.calls[1])["variables"].get("after") is None
    assert _req(route.calls[2])["variables"]["after"] == "c1"
    again = await client.list_discussions(REPO, "Plans")
    assert [d.number for d in again] == [3]
    assert route.call_count == 4


# (c) errors 배열 → GitHubGraphQLError
async def test_graphql_errors_raise(
    github_mock: respx.MockRouter, client: DiscussionsClient
) -> None:
    github_mock.post("/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": None, "errors": [{"message": "Could not resolve"}]}
        )
    )
    with pytest.raises(GitHubGraphQLError, match="Could not resolve"):
        await client.list_discussions(REPO, "Plans")


async def test_http_error_raises(github_mock: respx.MockRouter, client: DiscussionsClient) -> None:
    github_mock.post("/graphql").mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(GitHubGraphQLError, match="502"):
        await client.list_discussions(REPO, "Plans")


# (d) add_discussion_comment
async def test_add_discussion_comment(
    github_mock: respx.MockRouter, client: DiscussionsClient
) -> None:
    route = github_mock.post("/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "addDiscussionComment": {"comment": {"id": "DC_1", "url": "https://gh/c/1"}}
                }
            },
        )
    )
    ref = await client.add_discussion_comment("D_9", "/approve")
    assert ref == DiscussionCommentRef(id="DC_1", url="https://gh/c/1")
    body = _req(route.calls[0])
    assert "addDiscussionComment" in body["query"]
    assert body["variables"] == {"discussionId": "D_9", "body": "/approve"}
