"""GitHub REST client — httpx 직접 (PyGithub 미사용). 모든 쓰기는 멱등 (설계 §7.2, §7.3, D-22).

``client``는 ``base_url=https://api.github.com``이고 ``InstallationAuth``가 붙은 ``httpx.AsyncClient``.
멱등 판정은 GitHub search API(인덱스 지연) 대신 목록 조회로 한다 (D-22).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from github_adapter import markers
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

GITHUB_API_BASE_URL = "https://api.github.com"

# §7.2 라벨 스키마 (이름 → 색). kind는 TaskKind 값의 언더스코어를 하이픈으로.
LABEL_COLORS: dict[str, str] = {
    "ai:task": "0e8a16",
    **{f"role:{r}": "1d76db" for r in ("coding", "test", "review", "research", "architect")},
    **{f"tier:{t}": c for t, c in (("T0", "c2e0c6"), ("T1", "fbca04"), ("T2", "f9a825"), ("T3", "d93f0b"))},
    **{f"status:{s}": "5319e7" for s in ("ready", "running", "blocked", "awaiting-decision")},
    **{
        f"kind:{k}": "bfdadc"
        for k in ("feature", "bugfix", "test", "refactor", "research", "fix-from-review", "fix-from-test")
    },
    "human:override": "b60205",
}  # fmt: skip


class GitHubError(Exception):
    def __init__(self, status: int, message: str, url: str) -> None:
        self.status = status
        self.url = url
        super().__init__(f"GitHub {status} on {url}: {message}")


def kind_label(kind: str) -> str:
    return f"kind:{kind.replace('_', '-')}"


class GitHubRestClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    # ------------------------------------------------------------------ 공통
    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:  # Any: JSON 본문
        res = await self._client.request(method, url, **kwargs)
        if res.status_code >= 400:
            try:
                message = str(res.json().get("message", res.text))
            except ValueError:
                message = res.text
            raise GitHubError(res.status_code, message, url)
        if res.status_code == 204 or not res.content:
            return None
        return res.json()

    async def _get_all(self, url: str, **params: Any) -> list[dict[str, Any]]:
        """per_page=100 페이지네이션 (Link 헤더 대신 결과 수로 판단)."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = await self._request(
                "GET", url, params={**params, "per_page": 100, "page": page}
            )
            items = list(batch or [])
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1

    # ------------------------------------------------------------------ (a) labels
    async def ensure_labels(self, repo: str) -> list[str]:
        existing = {str(x["name"]) for x in await self._get_all(f"/repos/{repo}/labels")}
        created: list[str] = []
        for name, color in LABEL_COLORS.items():
            if name in existing:
                continue
            await self._request(
                "POST", f"/repos/{repo}/labels", json={"name": name, "color": color}
            )
            created.append(name)
        if created:
            log.info("github.labels_created", repo=repo, count=len(created))
        return created

    # ------------------------------------------------------------------ (b) issue
    async def _find_task_issue(self, repo: str, task_id: str) -> IssueRef | None:
        issues = await self._get_all(f"/repos/{repo}/issues", labels="ai:task", state="all")
        for issue in issues:
            if markers.parse_issue_marker(issue.get("body")) == task_id:
                return IssueRef(
                    number=int(issue["number"]), url=str(issue["html_url"]), created=False
                )
        return None

    async def create_task_issue(self, repo: str, task: TaskIssue) -> IssueRef:
        if (existing := await self._find_task_issue(repo, task.task_id)) is not None:
            return existing
        labels = ["ai:task", f"role:{task.role}", kind_label(task.kind), f"tier:{task.tier}"]
        if task.epic_number is not None:
            labels.append(f"epic:{task.epic_number}")
        payload: dict[str, Any] = {
            "title": task.title,
            "body": f"{markers.issue_marker(task.task_id)}\n\n{task.spec}",
            "labels": labels,
        }
        if task.milestone_number is not None:
            payload["milestone"] = task.milestone_number
        data = await self._request("POST", f"/repos/{repo}/issues", json=payload)
        return IssueRef(number=int(data["number"]), url=str(data["html_url"]), created=True)

    # ------------------------------------------------------------------ (c) status label
    async def update_issue_status_label(self, repo: str, issue_number: int, status: str) -> bool:
        issue = await self._request("GET", f"/repos/{repo}/issues/{issue_number}")
        current = [str(x["name"]) for x in issue.get("labels", [])]
        wanted = [x for x in current if not x.startswith("status:")] + [f"status:{status}"]
        if set(wanted) == set(current):
            return False
        await self._request(
            "PUT", f"/repos/{repo}/issues/{issue_number}/labels", json={"labels": wanted}
        )
        return True

    # ------------------------------------------------------------------ (d) comment
    async def comment(
        self, repo: str, number: int, body: str, key: str | None = None
    ) -> CommentRef:
        if key is not None:
            for c in await self._get_all(f"/repos/{repo}/issues/{number}/comments"):
                if markers.parse_comment_marker(c.get("body")) == key:
                    return CommentRef(id=int(c["id"]), url=str(c["html_url"]), created=False)
            body = f"{body}\n\n{markers.comment_marker(key)}"
        data = await self._request(
            "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
        )
        return CommentRef(id=int(data["id"]), url=str(data["html_url"]), created=True)

    # ------------------------------------------------------------------ (e) branch
    async def create_branch(self, repo: str, name: str, from_ref: str) -> BranchRef:
        try:
            ref = await self._request("GET", f"/repos/{repo}/git/ref/heads/{name}")
        except GitHubError as exc:
            if exc.status != 404:
                raise
        else:
            return BranchRef(name=name, sha=str(ref["object"]["sha"]), created=False)
        base = await self._request("GET", f"/repos/{repo}/git/ref/heads/{from_ref}")
        sha = str(base["object"]["sha"])
        await self._request(
            "POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{name}", "sha": sha}
        )
        return BranchRef(name=name, sha=sha, created=True)

    # ------------------------------------------------------------------ (f) pull request
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
        owner = repo.split("/", 1)[0]
        existing = await self._request(
            "GET",
            f"/repos/{repo}/pulls",
            params={"head": f"{owner}:{head}", "base": base, "state": "open"},
        )
        for pr in existing or []:
            return PullRef(number=int(pr["number"]), url=str(pr["html_url"]), created=False)
        full_body = f"{markers.pr_meta_block(meta, summary=body)}"
        data = await self._request(
            "POST",
            f"/repos/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": full_body, "draft": draft},
        )
        return PullRef(number=int(data["number"]), url=str(data["html_url"]), created=True)

    # ------------------------------------------------------------------ (g) milestone
    async def create_milestone(self, repo: str, epic: EpicMilestone) -> MilestoneRef:
        for ms in await self._get_all(f"/repos/{repo}/milestones", state="all"):
            if str(ms.get("title")) == epic.title:
                return MilestoneRef(number=int(ms["number"]), title=epic.title, created=False)
        data = await self._request(
            "POST",
            f"/repos/{repo}/milestones",
            json={"title": epic.title, "description": epic.description},
        )
        return MilestoneRef(number=int(data["number"]), title=str(data["title"]), created=True)
