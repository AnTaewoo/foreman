"""P7.1 — App 점검 (D-42, red a~d): 읽기 전용 REST로 인증·설치·권한·웹훅 구독 확인. 실 호출 0.

경로 출처(2026-09-14 docs.github.com REST 확인): GET /app, GET /app/installations,
POST /app/installations/{id}/access_tokens, GET /installation/repositories, GET /repos/{o}/{n},
GET /app/hook/config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import respx

APP_ID = "123"
PERMS_OK = {
    "contents": "write",
    "issues": "write",
    "pull_requests": "write",
    "discussions": "write",
    "metadata": "read",
}
EVENTS_OK = ["issue_comment", "discussion_comment", "pull_request", "pull_request_review"]


@dataclass
class FakeSettings:
    github_app_id: str = APP_ID
    github_app_private_key: Any = None
    github_installation_id: int | None = 42
    github_webhook_secret: Any = None


class Secret:
    def __init__(self, v: str) -> None:
        self._v = v

    def get_secret_value(self) -> str:
        return self._v


def mock_all(
    router: respx.MockRouter,
    *,
    perms: dict[str, str] = PERMS_OK,
    events: list[str] = EVENTS_OK,
    installation_id: int = 42,
    repo: str = "org/demo",
    hook_url: str = "https://smee.io/abc",
    categories: list[str] | None = None,
    discussions_enabled: bool = True,
    empty_repo: bool = False,
) -> None:
    # P9 버그 #1: 커밋 0개인 repo도 8/8 통과했다 → 기본 브랜치 존재 확인
    router.get(f"/repos/{repo}/branches/main").mock(
        return_value=httpx.Response(404, json={"message": "Branch not found"})
        if empty_repo
        else httpx.Response(200, json={"name": "main", "commit": {"sha": "abc"}})
    )
    cats = ["General", "Plans"] if categories is None else categories
    router.post("/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "hasDiscussionsEnabled": discussions_enabled,
                        "discussionCategories": {"nodes": [{"name": c} for c in cats]},
                    }
                }
            },
        )
    )
    router.get("/app").mock(
        return_value=httpx.Response(
            200, json={"id": 1, "name": "foreman-dev", "permissions": perms, "events": events}
        )
    )
    router.get("/app/installations").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": installation_id,
                    "account": {"login": "org"},
                    "permissions": perms,
                    "events": events,
                }
            ],
        )
    )
    router.post(f"/app/installations/{installation_id}/access_tokens").mock(
        return_value=httpx.Response(
            201,
            json={
                "token": "ghs_SECRET_TOKEN",
                "expires_at": "2099-01-01T00:00:00Z",
                "permissions": perms,
                "repository_selection": "selected",
            },
        )
    )
    router.get("/installation/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "repository_selection": "selected",
                "repositories": [{"full_name": repo}],
            },
        )
    )
    router.get(f"/repos/{repo}").mock(
        return_value=httpx.Response(200, json={"full_name": repo, "default_branch": "main"})
    )
    router.get("/app/hook/config").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": hook_url,
                "content_type": "json",
                "secret": "********",
                "insecure_ssl": "0",
            },
        )
    )


def settings(pem: str, secret: str = "s3cret") -> FakeSettings:
    return FakeSettings(github_app_private_key=Secret(pem), github_webhook_secret=Secret(secret))


# (a) 전부 통과: 6개 항목 [ok], 토큰 값은 리포트에 없다
async def test_all_ok(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str
) -> None:
    from github_adapter.app_check import run_check

    mock_all(github_mock)
    report = await run_check(settings(private_key_pem), http, repo="org/demo")
    assert report.ok, report.render()
    names = [i.name for i in report.items]
    assert names == [
        "app",
        "installation",
        "token",
        "permissions",
        "events",
        "repo",
        "content",  # P9 버그 #1: 기본 브랜치에 커밋이 있어야 Plan·워커가 돈다
        "discussions",  # P9: Discussions 켜짐 + 카테고리 Plans (없으면 Plan 게시가 실패한다)
        "webhook",
    ]
    assert all(i.ok for i in report.items)
    text = report.render()
    assert "ghs_SECRET_TOKEN" not in text and "[ok]" in text
    assert github_mock.calls.call_count == 8


async def test_empty_repo_fails_content_check(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str
) -> None:
    from github_adapter.app_check import run_check

    mock_all(github_mock, empty_repo=True)
    report = await run_check(settings(private_key_pem), http, repo="org/demo")
    bad = [i for i in report.items if not i.ok]
    assert [i.name for i in bad] == ["content"] and "empty" in bad[0].detail.lower()
    assert "commit" in bad[0].detail.lower()


# (a') Discussions가 꺼졌거나 Plans 카테고리가 없으면 discussions 항목만 FAIL
async def test_discussions_category_required(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str
) -> None:
    from github_adapter.app_check import run_check

    mock_all(github_mock, categories=["General", "Ideas"])
    report = await run_check(settings(private_key_pem), http, repo="org/demo")
    bad = [i for i in report.items if not i.ok]
    assert [i.name for i in bad] == ["discussions"] and "Plans" in bad[0].detail
    assert "Ideas" in bad[0].detail
    github_mock.reset()
    mock_all(github_mock, discussions_enabled=False)
    report = await run_check(settings(private_key_pem), http, repo="org/demo")
    bad = [i for i in report.items if not i.ok]
    assert [i.name for i in bad] == ["discussions"] and "disabled" in bad[0].detail.lower()


# (b) 권한·구독·설치·repo·웹훅 각각 빠지면 그 항목만 [FAIL], ok False
async def test_missing_permission_and_event(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str
) -> None:
    from github_adapter.app_check import REQUIRED_EVENTS, REQUIRED_PERMISSIONS, run_check

    assert REQUIRED_PERMISSIONS == PERMS_OK and REQUIRED_EVENTS == set(EVENTS_OK)
    from github_adapter.app_check import OPTIONAL_EVENTS

    assert OPTIONAL_EVENTS == {"check_suite"}  # Checks 권한 없으면 안 보임 — MVP 1 선택
    perms = {**PERMS_OK, "discussions": "read"}
    events = [e for e in EVENTS_OK if e != "discussion_comment"]
    mock_all(github_mock, perms=perms, events=events)
    report = await run_check(settings(private_key_pem), http, repo="org/demo")
    by = {i.name: i for i in report.items}
    assert not report.ok
    assert not by["permissions"].ok and "discussions" in by["permissions"].detail
    assert not by["events"].ok and "discussion_comment" in by["events"].detail
    assert by["app"].ok and by["repo"].ok and by["webhook"].ok


async def test_installation_repo_and_hook_failures(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str
) -> None:
    from github_adapter.app_check import run_check

    mock_all(
        github_mock, installation_id=7, hook_url=""
    )  # 설정의 42가 목록에 없다, 웹훅 URL 비어 있음
    github_mock.post("/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(404, json={})
    )
    report = await run_check(settings(private_key_pem), http, repo="org/other")
    by = {i.name: i for i in report.items}
    assert not by["installation"].ok and "42" in by["installation"].detail
    assert not by["token"].ok
    assert not by["webhook"].ok or by["webhook"].name == "webhook"
    assert not report.ok


# (c) 설정이 비어 있으면 호출 없이 [FAIL]
async def test_missing_settings(github_mock: respx.MockRouter, http: httpx.AsyncClient) -> None:
    from github_adapter.app_check import run_check

    report = await run_check(
        FakeSettings(github_app_id="", github_app_private_key=Secret("")), http, repo="org/demo"
    )
    assert (
        not report.ok and report.items[0].name == "settings" and github_mock.calls.call_count == 0
    )


# (d) 스크립트 종료 코드
def test_script_exit_codes() -> None:
    from github_adapter.app_check import CheckItem, CheckReport

    ok = CheckReport(items=[CheckItem("app", True, "foreman-dev")])
    bad = CheckReport(items=[CheckItem("app", False, "401")])
    assert ok.ok and ok.exit_code == 0 and not bad.ok and bad.exit_code == 1


# 인계서 2026-09-18 #4: `antaewoo/foreman_calc`는 실패, `AnTaewoo/Foreman_calc`는 통과했다.
# GitHub는 대소문자를 구분하지 않는다 → GET /repos의 정식 full_name으로 정규화해 점검
async def test_repo_name_is_case_insensitive_and_canonicalized(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str
) -> None:
    from github_adapter.app_check import run_check

    mock_all(github_mock, repo="Org/Demo")
    github_mock.get("/repos/org/demo").mock(
        return_value=httpx.Response(200, json={"full_name": "Org/Demo", "default_branch": "main"})
    )
    report = await run_check(settings(private_key_pem), http, repo="org/demo")
    assert report.ok, report.render()
    assert report.canonical == "Org/Demo"
    repo_item = next(i for i in report.items if i.name == "repo")
    assert "Org/Demo" in repo_item.detail
