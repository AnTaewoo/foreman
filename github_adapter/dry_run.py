"""DRY_RUN 계층 (D-10): 실제 GitHub API를 부르지 않고 ``would <method> …``를 로그로 남긴다.

``DryRunGitHubClient``는 ``GitHubClient`` Protocol과 같은 시그니처, ``DryRunDiscussionsClient``는
``DiscussionsClient``와 같은 시그니처. 상태는 메모리 dict — 번호는 결정적(Issue/PR은 같은 번호 공간,
GitHub과 동일)이고 모든 쓰기는 실 client와 같은 규칙으로 멱등이다. ``snapshot()``으로 검사한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from github_adapter import markers
from github_adapter.client import LABEL_COLORS, kind_label
from github_adapter.discussions import DiscussionCommentRef, DiscussionRef
from github_adapter.protocol import (
    BranchRef,
    CommentRef,
    EpicMilestone,
    IssueRef,
    MilestoneRef,
    PrMeta,
    PullRef,
    TaskIssue,
)

log = structlog.get_logger(__name__)

DEFAULT_DISCUSSION_CATEGORIES = ("Plans", "Proposals", "Reports")


@dataclass
class _Repo:
    next_number: int = 1  # Issue와 PR이 공유
    next_comment_id: int = 1
    next_milestone: int = 1
    labels: set[str] = field(default_factory=set)
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    pulls: dict[int, dict[str, Any]] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)  # name → sha
    milestones: dict[int, dict[str, Any]] = field(default_factory=dict)

    def take_number(self) -> int:
        n = self.next_number
        self.next_number += 1
        return n


class DryRunGitHubClient:
    def __init__(self) -> None:
        self._repos: dict[str, _Repo] = {}

    def _repo(self, repo: str) -> _Repo:
        return self._repos.setdefault(repo, _Repo())

    @staticmethod
    def _url(repo: str, kind: str, number: int) -> str:
        return f"https://github.com/{repo}/{kind}/{number}"

    # ------------------------------------------------------------------ GitHubClient
    async def ensure_labels(self, repo: str) -> list[str]:
        r = self._repo(repo)
        created = [name for name in LABEL_COLORS if name not in r.labels]
        r.labels.update(created)
        log.info("would ensure_labels", repo=repo, created=len(created))
        return created

    async def create_task_issue(self, repo: str, task: TaskIssue) -> IssueRef:
        r = self._repo(repo)
        for number, issue in r.issues.items():
            if issue["task_id"] == task.task_id:
                return IssueRef(number=number, url=self._url(repo, "issues", number), created=False)
        number = r.take_number()
        labels = ["ai:task", f"role:{task.role}", kind_label(task.kind), f"tier:{task.tier}"]
        if task.epic_number is not None:
            labels.append(f"epic:{task.epic_number}")
        r.issues[number] = {
            "task_id": task.task_id,
            "title": task.title,
            "body": f"{markers.issue_marker(task.task_id)}\n\n{task.spec}",
            "labels": labels,
            "milestone": task.milestone_number,
            "comments": [],
        }
        log.info(
            "would create_task_issue",
            repo=repo,
            number=number,
            task_id=task.task_id,
            title=task.title,
        )
        return IssueRef(number=number, url=self._url(repo, "issues", number), created=True)

    async def update_issue_status_label(self, repo: str, issue_number: int, status: str) -> bool:
        r = self._repo(repo)
        issue = r.issues.setdefault(issue_number, {"task_id": None, "labels": [], "comments": []})
        current: list[str] = list(issue["labels"])
        wanted = [x for x in current if not x.startswith("status:")] + [f"status:{status}"]
        if set(wanted) == set(current):
            return False
        issue["labels"] = wanted
        log.info("would update_issue_status_label", repo=repo, number=issue_number, status=status)
        return True

    async def comment(
        self, repo: str, number: int, body: str, key: str | None = None
    ) -> CommentRef:
        r = self._repo(repo)
        target = r.issues.get(number) or r.pulls.get(number)
        if target is None:
            target = r.issues.setdefault(number, {"task_id": None, "labels": [], "comments": []})
        if key is not None:
            for c in target["comments"]:
                if c["key"] == key:
                    return CommentRef(id=c["id"], url=c["url"], created=False)
        cid = r.next_comment_id
        r.next_comment_id += 1
        url = f"{self._url(repo, 'issues', number)}#issuecomment-{cid}"
        target["comments"].append({"id": cid, "key": key, "body": body, "url": url})
        log.info("would comment", repo=repo, number=number, key=key, chars=len(body))
        return CommentRef(id=cid, url=url, created=True)

    async def create_branch(self, repo: str, name: str, from_ref: str) -> BranchRef:
        r = self._repo(repo)
        if name in r.branches:
            return BranchRef(name=name, sha=r.branches[name], created=False)
        sha = r.branches.get(from_ref, f"dry{len(r.branches):06d}")
        r.branches[name] = sha
        log.info("would create_branch", repo=repo, name=name, from_ref=from_ref)
        return BranchRef(name=name, sha=sha, created=True)

    async def open_pr(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
        meta: PrMeta,
    ) -> PullRef:
        r = self._repo(repo)
        for number, pr in r.pulls.items():
            if pr["head"] == head and pr["base"] == base and pr["state"] == "open":
                return PullRef(number=number, url=self._url(repo, "pull", number), created=False)
        number = r.take_number()
        r.pulls[number] = {
            "head": head,
            "base": base,
            "title": title,
            "body": markers.pr_meta_block(meta, summary=body),
            "draft": draft,
            "state": "open",
            "task_id": meta.task_id,
            "comments": [],
        }
        log.info(
            "would open_pr",
            repo=repo,
            number=number,
            head=head,
            base=base,
            draft=draft,
            task_id=meta.task_id,
        )
        return PullRef(number=number, url=self._url(repo, "pull", number), created=True)

    async def create_milestone(self, repo: str, epic: EpicMilestone) -> MilestoneRef:
        r = self._repo(repo)
        for number, ms in r.milestones.items():
            if ms["title"] == epic.title:
                return MilestoneRef(number=number, title=epic.title, created=False)
        number = r.next_milestone
        r.next_milestone += 1
        r.milestones[number] = {
            "title": epic.title,
            "description": epic.description,
            "epic_id": epic.epic_id,
        }
        log.info("would create_milestone", repo=repo, number=number, title=epic.title)
        return MilestoneRef(number=number, title=epic.title, created=True)

    # ------------------------------------------------------------------ 검사용
    def snapshot(self) -> dict[str, Any]:
        return {
            "repos": {
                name: {
                    "labels": sorted(r.labels),
                    "issues": {n: dict(i) for n, i in r.issues.items()},
                    "pulls": {n: dict(p) for n, p in r.pulls.items()},
                    "branches": dict(r.branches),
                    "milestones": {n: dict(m) for n, m in r.milestones.items()},
                }
                for name, r in self._repos.items()
            }
        }


@dataclass
class _DiscussionRepo:
    next_number: int = 1
    discussions: dict[int, dict[str, Any]] = field(default_factory=dict)


class DryRunDiscussionsClient:
    def __init__(self, categories: tuple[str, ...] = DEFAULT_DISCUSSION_CATEGORIES) -> None:
        self._categories = {c.lower(): c for c in categories}
        self._repos: dict[str, _DiscussionRepo] = {}

    def _category(self, repo: str, category: str) -> str:
        try:
            return self._categories[category.lower()]
        except KeyError as exc:
            raise LookupError(f"discussion category {category!r} not found in {repo}") from exc

    async def list_discussions(self, repo: str, category: str) -> list[DiscussionRef]:
        cat = self._category(repo, category)
        r = self._repos.setdefault(repo, _DiscussionRepo())
        return [
            DiscussionRef(id=d["id"], number=n, title=d["title"], url=d["url"], created=False)
            for n, d in r.discussions.items()
            if d["category"] == cat
        ]

    async def create_discussion(
        self, repo: str, category: str, title: str, body: str
    ) -> DiscussionRef:
        cat = self._category(repo, category)
        r = self._repos.setdefault(repo, _DiscussionRepo())
        for n, d in r.discussions.items():
            if d["category"] == cat and d["title"] == title:
                return DiscussionRef(id=d["id"], number=n, title=title, url=d["url"], created=False)
        n = r.next_number
        r.next_number += 1
        d_id = f"DRY_D_{n}"
        url = f"https://github.com/{repo}/discussions/{n}"
        r.discussions[n] = {
            "id": d_id,
            "category": cat,
            "title": title,
            "body": body,
            "url": url,
            "comments": [],
        }
        log.info("would create_discussion", repo=repo, category=cat, number=n, title=title)
        return DiscussionRef(id=d_id, number=n, title=title, url=url, created=True)

    async def add_discussion_comment(self, discussion_id: str, body: str) -> DiscussionCommentRef:
        for repo, r in self._repos.items():
            for n, d in r.discussions.items():
                if d["id"] == discussion_id:
                    d["comments"].append(body)
                    cid = f"{discussion_id}_C{len(d['comments'])}"
                    log.info("would add_discussion_comment", repo=repo, number=n, chars=len(body))
                    return DiscussionCommentRef(id=cid, url=f"{d['url']}#discussioncomment-{cid}")
        raise LookupError(f"discussion {discussion_id} not found")

    def snapshot(self) -> dict[str, Any]:
        return {
            "repos": {
                name: {"discussions": {n: dict(d) for n, d in r.discussions.items()}}
                for name, r in self._repos.items()
            }
        }
