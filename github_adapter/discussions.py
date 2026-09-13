"""GitHub Discussions — GraphQL 필수 (설계 §7.3, §8.4). httpx로 ``POST /graphql`` 직접 호출.

스키마 확인 출처 (D-09, 2026-09-13):
- https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions
- https://docs.github.com/public/fpt/schema.docs.graphql (공개 SDL)
  - ``Repository.discussionCategories(first, after, ...): DiscussionCategoryConnection!``
  - ``Repository.discussions(first, after, categoryId: ID, orderBy: DiscussionOrder, states, answered)``
  - ``DiscussionCategory { id name slug description }``, ``Discussion { id number title body url category }``
  - ``createDiscussion(input: {repositoryId!, categoryId!, title!, body!}) { discussion { … } }``
  - ``addDiscussionComment(input: {discussionId!, body!, replyToId}) { comment { id url } }``

멱등: ``create_discussion``은 같은 카테고리에 같은 제목이 있으면 mutation 없이 그것을 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

GRAPHQL_PATH = "/graphql"

_REPO_META_QUERY = """
query RepoMeta($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id
    discussionCategories(first: 50) { nodes { id name slug } }
  }
}
"""

_LIST_QUERY = """
query ListDiscussions($owner: String!, $name: String!, $categoryId: ID, $after: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: 50, after: $after, categoryId: $categoryId,
                orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { endCursor hasNextPage }
      nodes { id number title body url category { id name } }
    }
  }
}
"""

_CREATE_MUTATION = """
mutation CreateDiscussion($repositoryId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
  createDiscussion(input: {repositoryId: $repositoryId, categoryId: $categoryId,
                           title: $title, body: $body}) {
    discussion { id number title url }
  }
}
"""

_COMMENT_MUTATION = """
mutation AddDiscussionComment($discussionId: ID!, $body: String!) {
  addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
    comment { id url }
  }
}
"""


class GitHubGraphQLError(Exception):
    """HTTP 오류 또는 응답의 ``errors`` 배열."""


@dataclass(frozen=True)
class DiscussionRef:
    id: str  # GraphQL node id (mutation·코멘트에 쓴다)
    number: int
    title: str
    url: str
    created: bool


@dataclass(frozen=True)
class DiscussionCommentRef:
    id: str
    url: str


@dataclass(frozen=True)
class _RepoMeta:
    repository_id: str
    categories: dict[str, str]  # name(lower)/slug → category id


class DiscussionsClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._meta: dict[str, _RepoMeta] = {}

    async def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        res = await self._client.post(GRAPHQL_PATH, json={"query": query, "variables": variables})
        if res.status_code >= 400:
            raise GitHubGraphQLError(f"HTTP {res.status_code}: {res.text[:200]}")
        payload = res.json()
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message", e)) for e in payload["errors"])
            raise GitHubGraphQLError(messages)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GitHubGraphQLError("no data in response")
        return data

    async def _repo_meta(self, repo: str) -> _RepoMeta:
        if repo in self._meta:
            return self._meta[repo]
        owner, name = repo.split("/", 1)
        data = await self._gql(_REPO_META_QUERY, {"owner": owner, "name": name})
        repository = data["repository"]
        categories: dict[str, str] = {}
        for node in repository["discussionCategories"]["nodes"]:
            categories[str(node["name"]).lower()] = str(node["id"])
            categories[str(node["slug"]).lower()] = str(node["id"])
        meta = _RepoMeta(repository_id=str(repository["id"]), categories=categories)
        self._meta[repo] = meta
        return meta

    async def _category_id(self, repo: str, category: str) -> str:
        meta = await self._repo_meta(repo)
        try:
            return meta.categories[category.lower()]
        except KeyError as exc:
            raise LookupError(f"discussion category {category!r} not found in {repo}") from exc

    async def list_discussions(self, repo: str, category: str) -> list[DiscussionRef]:
        owner, name = repo.split("/", 1)
        category_id = await self._category_id(repo, category)
        out: list[DiscussionRef] = []
        after: str | None = None
        while True:
            data = await self._gql(
                _LIST_QUERY,
                {"owner": owner, "name": name, "categoryId": category_id, "after": after},
            )
            conn = data["repository"]["discussions"]
            for node in conn["nodes"]:
                out.append(
                    DiscussionRef(
                        id=str(node["id"]),
                        number=int(node["number"]),
                        title=str(node["title"]),
                        url=str(node["url"]),
                        created=False,
                    )  # fmt: skip
                )
            page = conn["pageInfo"]
            if not page.get("hasNextPage"):
                return out
            after = page.get("endCursor")

    async def create_discussion(
        self, repo: str, category: str, title: str, body: str
    ) -> DiscussionRef:
        meta = await self._repo_meta(repo)
        category_id = await self._category_id(repo, category)
        for existing in await self.list_discussions(repo, category):
            if existing.title == title:
                return existing
        data = await self._gql(
            _CREATE_MUTATION,
            {
                "repositoryId": meta.repository_id,
                "categoryId": category_id,
                "title": title,
                "body": body,
            },
        )
        node = data["createDiscussion"]["discussion"]
        log.info("github.discussion_created", repo=repo, category=category, number=node["number"])
        return DiscussionRef(
            id=str(node["id"]), number=int(node["number"]), title=str(node["title"]),
            url=str(node["url"]), created=True,
        )  # fmt: skip

    async def add_discussion_comment(self, discussion_id: str, body: str) -> DiscussionCommentRef:
        data = await self._gql(_COMMENT_MUTATION, {"discussionId": discussion_id, "body": body})
        node = data["addDiscussionComment"]["comment"]
        return DiscussionCommentRef(id=str(node["id"]), url=str(node["url"]))
