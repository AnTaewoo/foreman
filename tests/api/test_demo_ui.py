"""P9.2 (D-52) — 데모 콘솔: `GET /`가 정적 1페이지, `/static/*` 서빙, 기존 라우트 그대로."""

from __future__ import annotations

import re

import httpx
import pytest


async def test_root_serves_demo_console(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="goals"' in r.text and "/static/demo.js" in r.text
    assert 'id="project"' in r.text  # 프로젝트 선택 (repo가 둘 이상일 때, ?project=<id> 딥링크)
    assert 'id="connect-form"' in r.text  # repo 연결 (P9.20: 점검 단계는 없다)
    assert 'id="delete-project"' in r.text  # 프로젝트 삭제 (D-54)
    assert 'id="llm"' in r.text  # LLM 프로파일 선택 (D-57)
    # 콘솔 UI 리뷰 2026-09-18: 인라인 에러, 진행 단계, 위험 구역, 이벤트·Goal 필터, 접근성
    for needle in (
        'id="goal-error"',
        'role="alert"',
        'id="steps"',
        'id="danger"',
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
    # 사용자 지시 2026-09-20 (P9.17): 이벤트 로그는 선택한 Goal 것만 — "프로젝트 전체 보기" 제거
    for gone in (
        'id="goal-meta"',
        'id="llm-hint"',
        "<th>시도</th>",
        "<th>Issue</th>",
        'id="events-all"',
        "프로젝트 전체 보기",
        "보관",  # 사용자 지시 2026-09-20 (P9.18): 삭제를 "보관"이라 부르지 않는다
        "점검",  # 사용자 지시 2026-09-20 (P9.20): 연결은 한 단계 — 점검 버튼 없음
        'id="connect-check"',
    ):
        assert gone not in r.text, gone


# 사용자 지시 2026-09-20 (P9.14): 처음 오는 사람이 읽고 그대로 따라 하도록 첫 화면 문구를 고친다.
async def test_first_screen_copy_for_beginners(client: httpx.AsyncClient) -> None:
    html = (await client.get("/")).text
    # (1) h1 부제 제거 — 데모 배지(id="demo-tag")는 demo.js가 쓰므로 남는다
    assert "Multi-Agent Dev Platform" not in html and "HITL" not in html
    assert 'id="demo-tag"' in html
    # (3) 맨 위 설명은 한 줄로 간결하게 (태그 뺀 길이 100자 이하)
    lead = re.search(r'<p class="lead">(.*?)</p>', html, re.S)
    assert lead, "p.lead"
    lead_text = " ".join(re.sub(r"<[^>]+>", "", lead.group(1)).split())
    assert 0 < len(lead_text) <= 100, lead_text
    # (2) 사용 방법: 3단계 → 6단계, repo 준비부터 머지까지. 제목의 숫자와 <li> 수가 같아야 한다
    howto = re.search(r'<details class="howto".*?</details>', html, re.S)
    assert howto, "details.howto"
    steps = re.findall(r"<li>", howto.group(0))
    assert len(steps) == 6, len(steps)
    # 사용자 지시 2026-09-20 (P9.16): 상자 제목은 "사용방법 6단계"만
    assert "<summary>사용방법 6단계</summary>" in howto.group(0)
    assert "처음이라면 이 순서대로" not in html
    for word in ("README", "설치", "연결", "/approve", "Merge"):
        assert word in howto.group(0), word
    # (5) "새 Goal" placeholder에서 "(영어 권장)" 삭제
    assert "영어 권장" not in html
    # P9.16: 데모 모드가 아니면 계정 안내줄을 띄우지 않는다 (demo-note는 데모 모드용으로 남는다)
    js = (await client.get("/static/demo.js")).text
    assert "로 기록됩니다" not in js and 'id="demo-note"' in html


async def test_llm_choices_and_examples(client: httpx.AsyncClient) -> None:
    js = (await client.get("/static/demo.js")).text
    # (4) 콘솔 LLM 선택지에서 anthropic 제거 (서버의 /llm·PROFILES는 그대로 — D-57)
    assert "HIDDEN_LLM" in js and '"anthropic"' in js
    assert [p["name"] for p in (await client.get("/llm")).json()["profiles"]] == [
        "ollama",
        "openai",
        "anthropic",
    ]
    # (6) 예시 Goal 2개, 둘 다 README.md 작성까지 시킨다
    block = re.search(r"const EXAMPLES = \[(.*?)\];", js, re.S)
    assert block, "EXAMPLES"
    examples = re.findall(r'"([^"]+)"', block.group(1))
    assert len(examples) == 2, examples
    assert all("README.md" in e for e in examples), examples


async def test_static_assets(client: httpx.AsyncClient) -> None:
    js = await client.get("/static/demo.js")
    assert js.status_code == 200 and "fetch(" in js.text
    # P9.20: 점검 UI 제거 — 정식 repo 이름 보정은 POST /projects가 한다
    assert "renderCheck" not in js.text
    # 사용자 보고: 새 버튼이 HTML엔 보이는데 눌리지 않음 = 옛 demo.js 캐시. 항상 재검증하게 한다
    assert "no-cache" in js.headers.get("cache-control", "")
    assert "no-cache" in (await client.get("/")).headers.get("cache-control", "")
    assert "STATUS_LABEL" in js.text and "scrollIntoView" in js.text and "replaceState" in js.text
    # 사용자 보고 2026-09-19: Goal 생성 직후 상세 GET이 projection 전이라 404 → 오류 알림이 떴다.
    # 404는 "아직 준비 중"으로 보고 알림 없이 재시도한다 (retryGoalSoon)
    assert "retryGoalSoon" in js.text and "e.status === 404" in js.text
    assert "events-all" not in js.text  # P9.17: 잔여 참조 0
    # P9.18: 삭제 문구 + projection 반영까지 기다렸다 다시 읽는다 (D-46)
    assert "보관" not in js.text and "untilDeleted" in js.text
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
    assert "install_url" in js  # ① 설치 링크는 남는다
    css = (await client.get("/static/demo.css")).text
    # P9.20: 점검 목록과 함께 죽은 CSS도 제거 (.check-inline은 다른 클래스라 남는다)
    assert "ul.check" not in css and ".check li" not in css
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
