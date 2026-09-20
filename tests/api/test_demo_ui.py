"""P9.2 (D-52) — 데모 콘솔: `GET /`가 정적 1페이지, `/static/*` 서빙, 기존 라우트 그대로."""

from __future__ import annotations

import httpx
import pytest


async def test_root_serves_demo_console(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="goals"' in r.text and "/static/demo.js" in r.text
    assert 'id="project"' in r.text  # 프로젝트 선택 (repo가 둘 이상일 때, ?project=<id> 딥링크)
    assert 'id="connect-form"' in r.text and 'id="connect-check"' in r.text  # repo 연결 + 점검
    assert 'id="delete-project"' in r.text  # 프로젝트 삭제(보관) (D-54)
    assert 'id="llm"' in r.text  # LLM 프로파일 선택 (D-57)
    # 콘솔 UI 리뷰 2026-09-18: 인라인 에러, 진행 단계, 위험 구역(보관), 이벤트·Goal 필터, 접근성
    for needle in (
        'id="goal-error"',
        'role="alert"',
        'id="steps"',
        'id="danger"',
        'id="events-all"',
        'id="goal-filter"',
        'id="ws-banner"',
        'id="reject-box"',
        'rel="icon"',
        'aria-live="polite"',
    ):
        assert needle in r.text, needle
    assert "로컬 LLM" not in r.text  # 안내 문구는 고른 LLM에 맞춘다 (하드코딩 제거)
    # 사용자 방향 2026-09-18: 심플하게 — 세부 정보는 접거나 뺀다
    # (보이는 게 많을수록 프런트 오류도 는다)
    assert 'id="events-box"' in r.text and 'id="plan-box"' in r.text and 'id="settings"' in r.text
    for gone in ('id="goal-meta"', 'id="llm-hint"', "<th>시도</th>", "<th>Issue</th>"):
        assert gone not in r.text, gone


async def test_static_assets(client: httpx.AsyncClient) -> None:
    js = await client.get("/static/demo.js")
    assert js.status_code == 200 and "fetch(" in js.text
    assert "canonical" in js.text  # 인계서 #4: 점검이 돌려준 정식 repo 이름으로 연결
    # 사용자 보고: 새 버튼이 HTML엔 보이는데 눌리지 않음 = 옛 demo.js 캐시. 항상 재검증하게 한다
    assert "no-cache" in js.headers.get("cache-control", "")
    assert "no-cache" in (await client.get("/")).headers.get("cache-control", "")
    assert "STATUS_LABEL" in js.text and "scrollIntoView" in js.text and "replaceState" in js.text
    # 사용자 보고 2026-09-19: Goal 생성 직후 상세 GET이 projection 전이라 404 → 오류 알림이 떴다.
    # 404는 "아직 준비 중"으로 보고 알림 없이 재시도한다 (retryGoalSoon)
    assert "retryGoalSoon" in js.text and "e.status === 404" in js.text
    css = await client.get("/static/demo.css")
    assert css.status_code == 200
    assert "prefers-color-scheme: dark" in css.text and ":focus-visible" in css.text
    assert (await client.get("/static/nope.js")).status_code == 404


async def test_existing_routes_unchanged(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/docs")).status_code == 200
    schema = (await client.get("/openapi.json")).json()
    assert "/" not in schema["paths"]  # 콘솔은 API 문서에 안 나온다


# 2차 계정 리허설(2026-09-19): 외부 repo에서 콘솔 Approve가 403(judge는 멤버 아님).
# 콘솔 사용자가 owner/approver일 때만 버튼, 아니면 GitHub /approve 안내 + Plan 링크
async def test_approve_button_only_for_approvers(client: httpx.AsyncClient) -> None:
    html = (await client.get("/")).text
    assert 'id="github-approve"' in html and 'id="approve-box"' in html
    js = (await client.get("/static/demo.js")).text
    assert "approvers" in js and "canApprove" in js
    r = await client.post(
        "/projects",
        json={
            "name": "d",
            "repo": "acme/demo",
            "members": [
                {"user_id": "alice", "role": "owner"},
                {"user_id": "judge", "role": "approver"},
                {"user_id": "bob", "role": "viewer"},
            ],
        },
    )
    pid = r.json()["id"]
    assert r.json()["approvers"] == ["alice", "judge"]
    assert (await client.get(f"/projects/{pid}")).json()["approvers"] == ["alice", "judge"]


# P9.11 공개 App: 콘솔 연결은 두 단계 — ① App 설치 링크 → ② repo 입력·점검·연결.
# 점검의 경고(required=False, Discussions/Plans)는 막지 않는 노란 항목으로 보인다
async def test_console_two_step_connect(client: httpx.AsyncClient) -> None:
    html = (await client.get("/")).text
    assert 'id="install-app"' in html and "App 설치" in html
    assert "Issue로" in html  # Plans 카테고리가 없으면 Plan은 Issue로 (조건 안내)
    js = (await client.get("/static/demo.js")).text
    assert "install_url" in js and "required === false" in js
    css = (await client.get("/static/demo.css")).text
    assert ".check li.warn" in css
    info = (await client.get("/demo")).json()
    assert "install_url" in info and info["install_url"] is None  # Dry: GitHub 호출 없음


async def test_demo_info_install_url_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """실 모드: `GET /app`의 slug로 설치 링크를 만들고 프로세스 안에서 한 번만 부른다."""
    from control_plane.api import app as app_mod

    calls: list[str] = []

    async def fake(settings: object) -> str | None:
        calls.append("app")
        return "https://github.com/apps/foreman-antaewoo/installations/new"

    monkeypatch.setattr(app_mod, "fetch_install_url", fake)
    cache = app_mod.InstallUrl(dry_run=False)
    assert await cache.get(object()) == await cache.get(object())
    assert calls == ["app"]
    assert await app_mod.InstallUrl(dry_run=True).get(object()) is None
