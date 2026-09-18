"""GitHub App 점검 (P7.1, D-42): 실 연결 전에 인증·설치·권한·웹훅 구독을 **읽기 전용**으로 확인한다.

REST 경로(2026-09-14 docs.github.com/en/rest/apps 확인):
- ``GET /app`` (JWT) → name, permissions, events
- ``GET /app/installations`` (JWT) → [{id, account.login, permissions, events}]
- ``POST /app/installations/{id}/access_tokens`` (JWT) → token, expires_at, permissions,
  repository_selection
- ``GET /installation/repositories`` (installation token) → total_count, repositories[].full_name
- ``GET /repos/{owner}/{repo}`` (installation token)
- ``GET /app/hook/config`` (JWT) → url, content_type, secret, insecure_ssl
토큰 값은 리포트에 절대 싣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from github_adapter.auth import API_HEADERS, app_jwt

REQUIRED_PERMISSIONS: dict[str, str] = {
    "contents": "write",
    "issues": "write",
    "pull_requests": "write",
    "discussions": "write",
    "metadata": "read",
}
REQUIRED_EVENTS: frozenset[str] = frozenset(
    {"issue_comment", "discussion_comment", "pull_request", "pull_request_review"}
)
# check_suite는 Checks 권한이 있어야 구독 목록에 보인다. MVP 1은 pr.checks_*가 noop → 선택(경고만)
OPTIONAL_EVENTS: frozenset[str] = frozenset({"check_suite"})
PLAN_CATEGORY = "Plans"  # same value as orchestrator.graph.PLAN_CATEGORY
_DISCUSSIONS_QUERY = """
query RepoDiscussions($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    hasDiscussionsEnabled
    discussionCategories(first: 50) { nodes { name } }
  }
}
"""
_LEVEL = {"read": 1, "write": 2, "admin": 3}


@dataclass(frozen=True)
class CheckItem:
    name: str
    ok: bool
    detail: str
    required: bool = True  # False면 경고 — 실패해도 연결을 막지 않는다 (P9.10)


@dataclass
class CheckReport:
    items: list[CheckItem] = field(default_factory=list)
    canonical: str | None = None  # GitHub가 돌려준 정식 owner/name (대소문자 정규화)
    # P9.10 공개 App: repo로 찾은 installation과 연결에 쓰는 값
    installation_id: int | None = None
    account_login: str | None = None
    default_branch: str | None = None
    install_url: str | None = None  # https://github.com/apps/{slug}/installations/new
    has_plan_category: bool = False  # Discussions + Plans — 없으면 Plan은 Issue로

    @property
    def ok(self) -> bool:
        return bool(self.items) and all(i.ok for i in self.items if i.required)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def add(self, name: str, ok: bool, detail: str, *, required: bool = True) -> None:
        self.items.append(CheckItem(name, ok, detail, required))

    def render(self) -> str:
        lines = [
            f"  [{'ok' if i.ok else ('FAIL' if i.required else 'warn')}] {i.name:<13} {i.detail}"
            for i in self.items
        ]
        return "\n".join(lines + [f"App check: {'PASS' if self.ok else 'FAIL'}"])


def _secret(value: Any) -> str:
    return str(value.get_secret_value()) if hasattr(value, "get_secret_value") else str(value or "")


def _missing_permissions(granted: dict[str, Any]) -> list[str]:
    out = []
    for name, need in REQUIRED_PERMISSIONS.items():
        have = str(granted.get(name, ""))
        if _LEVEL.get(have, 0) < _LEVEL[need]:
            out.append(f"{name}:{need} (has {have or 'none'})")
    return out


async def run_check(settings: Any, http: httpx.AsyncClient, *, repo: str) -> CheckReport:
    """``http``는 base_url이 GitHub API인 AsyncClient(테스트는 respx). 쓰기 호출 없음."""
    report = CheckReport()
    app_id = str(getattr(settings, "github_app_id", "") or "")
    pem = _secret(getattr(settings, "github_app_private_key", ""))
    secret = _secret(getattr(settings, "github_webhook_secret", ""))
    missing = [  # P9.10: installation id는 env가 아니라 repo로 찾는다 (공개 App)
        k
        for k, v in (
            ("HITL_GITHUB_APP_ID", app_id),
            ("HITL_GITHUB_APP_PRIVATE_KEY", pem),
            ("HITL_GITHUB_WEBHOOK_SECRET", secret),
        )
        if not v
    ]
    if missing:
        report.add("settings", False, "missing: " + ", ".join(missing))
        return report
    jwt_headers = {**API_HEADERS, "Authorization": f"Bearer {app_jwt(app_id, pem)}"}

    # 1) app
    r = await http.get("/app", headers=jwt_headers)
    if r.status_code != 200:
        report.add("app", False, f"GET /app → {r.status_code} (App ID·PEM 확인)")
        return report
    app = r.json()
    report.add("app", True, f"{app.get('name')} (id {app.get('id')})")
    if slug := app.get("slug"):
        report.install_url = f"https://github.com/apps/{slug}/installations/new"

    # 2) installation — 이 repo에 설치된 것 (GET /repos/{o}/{r}/installation, JWT, 없으면 404)
    r = await http.get(f"/repos/{repo}/installation", headers=jwt_headers)
    found: dict[str, Any] | None = r.json() if r.status_code == 200 else None
    if found is None:
        report.add(
            "installation",
            False,
            f"App not installed on {repo} (HTTP {r.status_code}) — install it: "
            f"{report.install_url or 'GitHub App settings → Install App'}",
        )
    else:
        report.installation_id = int(found["id"])
        report.account_login = str((found.get("account") or {}).get("login") or "")
        report.add("installation", True, f"{report.installation_id} on {report.account_login}")

    # 3) token (값은 리포트에 싣지 않는다)
    token = ""
    granted: dict[str, Any] = dict(app.get("permissions") or {})
    if report.installation_id is None:
        report.add("token", False, "skipped (not installed)")
    else:
        r = await http.post(
            f"/app/installations/{report.installation_id}/access_tokens", headers=jwt_headers
        )
        if r.status_code in (200, 201):
            data = r.json()
            token = str(data.get("token", ""))
            granted = dict(data.get("permissions") or granted)
            report.add("token", True, f"issued, expires {data.get('expires_at')}")
        else:
            report.add("token", False, f"POST access_tokens → {r.status_code}")

    # 4) permissions
    lacking = _missing_permissions(granted)
    report.add(
        "permissions",
        not lacking,
        "all required" if not lacking else "missing " + ", ".join(lacking),
    )

    # 5) events (웹훅 구독)
    events = set(app.get("events") or []) | set((found or {}).get("events") or [])
    lacking_events = sorted(REQUIRED_EVENTS - events)
    optional_missing = sorted(OPTIONAL_EVENTS - events)
    note = f" (optional not subscribed: {', '.join(optional_missing)})" if optional_missing else ""
    report.add(
        "events",
        not lacking_events,
        ("subscribed: " + ", ".join(sorted(REQUIRED_EVENTS)) + note)
        if not lacking_events
        else "not subscribed: " + ", ".join(lacking_events) + note,
    )

    # 6) repo
    if token:
        inst_headers = {**API_HEADERS, "Authorization": f"Bearer {token}"}
        r = await http.get("/installation/repositories", headers=inst_headers)
        names = (
            [x.get("full_name") for x in (r.json().get("repositories") or [])]
            if r.status_code == 200
            else []
        )
        r2 = await http.get(f"/repos/{repo}", headers=inst_headers)
        if (
            r2.status_code == 200
        ):  # GitHub는 대소문자를 구분하지 않는다 → 정식 이름으로 나머지를 점검
            repo = str(r2.json().get("full_name") or repo)
            report.canonical = repo
        ok = repo.lower() in {str(n).lower() for n in names} and r2.status_code == 200
        report.add(
            "repo",
            ok,
            f"{repo} accessible (default {r2.json().get('default_branch')})"
            if ok
            else f"{repo} not accessible (installation has {names}, GET /repos → {r2.status_code})",
        )
        # 6a) 기본 브랜치에 커밋이 있는가 (P9 버그 #1): 빈 repo는 clone·pytest가 무의미하다
        if ok:
            default_branch = str(r2.json().get("default_branch") or "main")
            report.default_branch = default_branch  # P9.10: 연결은 GitHub의 기본 브랜치로
            r3 = await http.get(f"/repos/{repo}/branches/{default_branch}", headers=inst_headers)
            if r3.status_code == 200:
                report.add("content", True, f"branch {default_branch} has commits")
            else:
                report.add(
                    "content",
                    False,
                    f"empty repository — branch {default_branch!r} not found "
                    f"(HTTP {r3.status_code}); "
                    "push at least one commit (README, pyproject) before connecting",
                )
        else:
            report.add("content", False, "skipped (repo not accessible)")
        # 6b) Discussions 켜짐 + 카테고리 Plans (P9). P9.10부터 경고 — 없으면 Plan은 Issue로
        if ok:
            owner, name = repo.split("/", 1)
            g = await http.post(
                "/graphql",
                headers=inst_headers,
                json={"query": _DISCUSSIONS_QUERY, "variables": {"owner": owner, "name": name}},
            )
            node = (
                ((g.json().get("data") or {}).get("repository") or {})
                if g.status_code == 200
                else {}
            )
            enabled = bool(node.get("hasDiscussionsEnabled"))
            cats = [
                str(c.get("name"))
                for c in ((node.get("discussionCategories") or {}).get("nodes") or [])
            ]
            fallback = " — the plan will be posted as an Issue instead"
            if not enabled:
                report.add(
                    "discussions",
                    False,
                    "Discussions disabled on the repo (Settings → Features)" + fallback,
                    required=False,
                )
            elif PLAN_CATEGORY not in cats:
                report.add(
                    "discussions",
                    False,
                    f"category {PLAN_CATEGORY!r} missing (has {cats})" + fallback,
                    required=False,
                )
            else:
                report.has_plan_category = True
                report.add("discussions", True, f"enabled, categories {cats}", required=False)
        else:
            report.add("discussions", False, "skipped (repo not accessible)", required=False)
    else:
        report.add("repo", False, f"{repo}: skipped (no token)")
        report.add("content", False, "skipped (no token)")
        report.add("discussions", False, "skipped (no token)", required=False)

    # 7) webhook config
    r = await http.get("/app/hook/config", headers=jwt_headers)
    cfg = r.json() if r.status_code == 200 else {}
    url = str(cfg.get("url") or "")
    report.add(
        "webhook",
        bool(url) and r.status_code == 200,
        f"url {url}, content_type {cfg.get('content_type')}"
        if url
        else "webhook URL not set on the App",
    )
    return report
