"""GitHub REST client — httpx 직접 (PyGithub 미사용). 모든 쓰기는 멱등 (설계 §7.2, §7.3, D-22).

``client``는 base_url이 GitHub API이고 ``InstallationAuth``가 붙은 ``httpx.AsyncClient``.
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
_ROLES = ("coding", "test", "review", "research", "architect")
_TIERS = (("T0", "c2e0c6"), ("T1", "fbca04"), ("T2", "f9a825"), ("T3", "d93f0b"))
_STATUSES = ("ready", "running", "blocked", "awaiting-decision")
_KINDS = ("feature", "bugfix", "test", "refactor", "research", "fix-from-review", "fix-from-test")
LABEL_COLORS: dict[str, str] = {
    "ai:task": "0e8a16",
    **{f"role:{r}": "1d76db" for r in _ROLES},
    **{f"tier:{t}": c for t, c in _TIERS},
    **{f"status:{s}": "5319e7" for s in _STATUSES},
    **{f"kind:{k}": "bfdadc" for k in _KINDS},
    "human:override": "b60205",
}


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

    async def create_plan_issue(self, repo: str, key: str, title: str, body: str) -> IssueRef:
        """P9.11: Plan을 Issue로 (Plans 카테고리 없음). ``ai-platform:plan`` 마커로 멱등."""
        for issue in await self._get_all(f"/repos/{repo}/issues", state="all"):
            if markers.parse_plan_marker(issue.get("body")) == key:
                return IssueRef(
                    number=int(issue["number"]), url=str(issue["html_url"]), created=False
                )
        data = await self._request(
            "POST",
            f"/repos/{repo}/issues",
            json={"title": title, "body": f"{markers.plan_marker(key)}\n\n{body}"},
        )
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

    # ------------------------------------------------------------------ (i) 정리·시드 (P7.4, D-42)
    # 경로 출처 2026-09-14 docs.github.com: issues(list/update), pulls(update), git/refs(delete)
    async def list_open_items(self, repo: str) -> list[dict[str, Any]]:
        """열린 Issue+PR (GitHub는 PR도 issue로 돌려준다 — ``pull_request`` 키로 구분)."""
        return await self._get_all(f"/repos/{repo}/issues", state="open")

    async def close_issue(self, repo: str, number: int) -> None:
        await self._request(
            "PATCH",
            f"/repos/{repo}/issues/{number}",
            json={"state": "closed", "state_reason": "not_planned"},
        )

    async def close_pull(self, repo: str, number: int) -> None:
        await self._request("PATCH", f"/repos/{repo}/pulls/{number}", json={"state": "closed"})

    async def list_refs(self, repo: str, prefix: str = "heads/ai/") -> list[str]:
        """``refs/heads/ai/*`` 브랜치 이름 목록."""
        refs = await self._request("GET", f"/repos/{repo}/git/matching-refs/{prefix}")
        return [str(r["ref"]).removeprefix("refs/heads/") for r in (refs or [])]

    async def delete_ref(self, repo: str, branch: str) -> bool:
        """브랜치 삭제. 이미 없으면(422) False."""
        try:
            await self._request("DELETE", f"/repos/{repo}/git/refs/heads/{branch}")
        except GitHubError as exc:
            if "422" in str(exc) or "does not exist" in str(exc):
                return False
            raise
        return True

    async def commit_count_hint(self, repo: str) -> int | None:
        """빈 repo면 ``GET /commits``가 409 → 0. 아니면 1 이상. 알 수 없으면 None."""
        try:
            items = await self._request("GET", f"/repos/{repo}/commits", params={"per_page": 1})
        except GitHubError as exc:
            if "409" in str(exc):
                return 0
            return None
        return len(items or [])
