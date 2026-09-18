"""P9.10 — 공개 App 연결: 설치가 곧 권한 증명 (§6 2026-09-19).

실 모드 `POST /projects {repo}`는 `run_check`(repo로 installation 탐지)를 거친다.
- 외부 installation(서버 env installation이 아님) → 데모 모드여도 관리 토큰 없이 201,
  이름·기본 브랜치는 GitHub 값, members = installation 계정 owner, installation_id 기록.
- 미설치 → 400 app_not_installed + 설치 링크. 점검 실패(빈 repo 등) → 400.
- 서버 자체 installation → 기존대로 데모 모드면 관리 토큰.
"""

from __future__ import annotations

from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.api import projects as projects_mod
from control_plane.store import models as m
from github_adapter.app_check import CheckReport
from tests.api.conftest import Pump
from tests.api.test_demo_guard import ADMIN, client_for, make_app

INSTALL_URL = "https://github.com/apps/foreman-dev/installations/new"
SERVER_IID = 42


def report(
    *,
    installation_id: int | None = 99,
    login: str = "judge",
    canonical: str = "Judge/Demo",
    branch: str = "master",
    content_ok: bool = True,
) -> CheckReport:
    rep = CheckReport()
    rep.install_url = INSTALL_URL
    rep.add("app", True, "foreman-dev")
    if installation_id is None:
        rep.add("installation", False, f"not installed — {INSTALL_URL}")
        return rep
    rep.installation_id, rep.account_login = installation_id, login
    rep.canonical, rep.default_branch = canonical, branch
    rep.add("installation", True, f"{installation_id} on {login}")
    rep.add("content", content_ok, "ok" if content_ok else "empty repository — push a commit")
    rep.add("discussions", False, "category 'Plans' missing", required=False)  # 경고일 뿐
    return rep


@pytest.fixture
def check(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {"report": report(), "seen": []}

    async def fake_run_check(settings: Any, http: Any, *, repo: str) -> CheckReport:
        box["seen"].append(repo)
        rep: CheckReport = box["report"]
        return rep

    class FakeHttp:
        async def __aenter__(self) -> FakeHttp:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

    monkeypatch.setattr(projects_mod, "run_check", fake_run_check)
    monkeypatch.setattr(projects_mod, "github_http", lambda: FakeHttp())
    return box


def real_app(factory: async_sessionmaker[AsyncSession], redis: Redis) -> Any:
    return make_app(factory, redis, dry_run=False, github_installation_id=SERVER_IID)


async def test_external_installation_connects_without_admin_token(
    factory: async_sessionmaker[AsyncSession], redis: Redis, pump: Pump, check: dict[str, Any]
) -> None:
    async with client_for(real_app(factory, redis)) as c:
        r = await c.post("/projects", json={"name": "demo", "repo": "judge/demo"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["repo"] == "Judge/Demo" and body["default_branch"] == "master"
    assert check["seen"] == ["judge/demo"]
    await pump()
    async with factory() as s:
        row = await s.scalar(select(m.Project).where(m.Project.id == body["id"]))
        assert row is not None and row.installation_id == 99
        assert row.members == [{"user_id": "judge", "role": "owner"}]  # 콘솔 judge 없음
        ev = await s.scalar(select(m.Event).where(m.Event.type == "project.created"))
        assert ev is not None and ev.payload["installation_id"] == 99


async def test_not_installed_returns_install_link(
    factory: async_sessionmaker[AsyncSession], redis: Redis, check: dict[str, Any]
) -> None:
    check["report"] = report(installation_id=None)
    async with client_for(real_app(factory, redis)) as c:
        r = await c.post("/projects", json={"name": "demo", "repo": "judge/demo"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.startswith("app_not_installed") and INSTALL_URL in detail


async def test_failed_check_blocks_connect(
    factory: async_sessionmaker[AsyncSession], redis: Redis, check: dict[str, Any]
) -> None:
    check["report"] = report(content_ok=False)
    async with client_for(real_app(factory, redis)) as c:
        r = await c.post("/projects", json={"name": "demo", "repo": "judge/demo"})
    assert r.status_code == 400 and "empty repository" in r.json()["detail"]


async def test_server_installation_still_needs_admin_token(
    factory: async_sessionmaker[AsyncSession], redis: Redis, check: dict[str, Any]
) -> None:
    check["report"] = report(installation_id=SERVER_IID, login="AnTaewoo", canonical="AnTaewoo/x")
    async with client_for(real_app(factory, redis)) as c:
        assert (await c.post("/projects", json={"name": "x", "repo": "AnTaewoo/x"})).status_code == 401
        r = await c.post("/projects", json={"name": "x", "repo": "AnTaewoo/x"}, headers=ADMIN)
    assert r.status_code == 201, r.text


async def test_check_route_reports_warning_and_install_url(
    factory: async_sessionmaker[AsyncSession], redis: Redis, check: dict[str, Any]
) -> None:
    async with client_for(real_app(factory, redis)) as c:
        body = (await c.get("/projects/check", params={"repo": "judge/demo"})).json()
    assert body["ok"] is True  # Discussions는 경고라 막지 않는다
    disc = next(i for i in body["items"] if i["name"] == "discussions")
    assert disc["ok"] is False and disc["required"] is False
    assert body["install_url"] == INSTALL_URL and body["installation_id"] == 99
