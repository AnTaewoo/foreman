"""P9.2 (D-52) — 데모 콘솔: `GET /`가 정적 1페이지, `/static/*` 서빙, 기존 라우트 그대로."""

from __future__ import annotations

import httpx


async def test_root_serves_demo_console(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="goals"' in r.text and "/static/demo.js" in r.text
    assert 'id="project"' in r.text  # 프로젝트 선택 (repo가 둘 이상일 때, ?project=<id> 딥링크)
    assert 'id="connect-form"' in r.text and 'id="connect-check"' in r.text  # repo 연결 + 점검
    assert 'id="delete-project"' in r.text  # 프로젝트 삭제(보관) (D-54)
    assert 'id="llm"' in r.text  # LLM 프로파일 선택 (D-57)


async def test_static_assets(client: httpx.AsyncClient) -> None:
    js = await client.get("/static/demo.js")
    assert js.status_code == 200 and "fetch(" in js.text
    # 사용자 보고: 새 버튼이 HTML엔 보이는데 눌리지 않음 = 옛 demo.js 캐시. 항상 재검증하게 한다
    assert "no-cache" in js.headers.get("cache-control", "")
    assert "no-cache" in (await client.get("/")).headers.get("cache-control", "")
    css = await client.get("/static/demo.css")
    assert css.status_code == 200
    assert (await client.get("/static/nope.js")).status_code == 404


async def test_existing_routes_unchanged(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/docs")).status_code == 200
    schema = (await client.get("/openapi.json")).json()
    assert "/" not in schema["paths"]  # 콘솔은 API 문서에 안 나온다
