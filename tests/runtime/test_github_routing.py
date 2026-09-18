"""P9.9 — 프로젝트별 installation: repo → installation_id → 그 client·토큰."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.github_routing import RepoRouter, RoutingGitHubClient
from control_plane.store import models as m
from github_adapter import ClientRegistry
from github_adapter.auth import TokenUnavailable
from github_adapter.dry_run import DryRunGitHubClient


@pytest.fixture(scope="module")
def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def gh() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as router:
        for iid in (42, 7):
            router.post(f"/app/installations/{iid}/access_tokens").mock(
                return_value=httpx.Response(
                    201, json={"token": f"ghs_{iid}", "expires_at": "2099-01-01T00:00:00Z"}
                )
            )
        yield router


async def _projects(factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(UTC)
    async with factory() as s:
        s.add_all(
            [
                # 같은 repo의 옛(보관된) 프로젝트는 무시된다
                m.Project(
                    id="P0", name="old", repo_full_name="Judge/Demo", installation_id=99,
                    archived_at=now,
                ),
                m.Project(id="P1", name="a", repo_full_name="Judge/Demo", installation_id=42),
                m.Project(id="P2", name="b", repo_full_name="AnTaewoo/foreman_test"),
            ]
        )  # fmt: skip
        await s.commit()


def _real(pem: str) -> Settings:
    return Settings(
        _env_file=None,
        dry_run=False,
        github_app_id="1",
        github_app_private_key=pem,
        github_installation_id=7,
    )


# repo → installation (대소문자 무시, 보관 제외, 없으면 env 기본), 토큰은 prepare 뒤 동기로
async def test_router_tokens_per_repo(
    factory: async_sessionmaker[AsyncSession], gh: respx.MockRouter, pem: str
) -> None:
    await _projects(factory)
    router = RepoRouter(factory, ClientRegistry(_real(pem)))
    assert await router.installation_for("judge/demo") == 42
    assert await router.installation_for("AnTaewoo/foreman_test") is None  # env 기본
    with pytest.raises(TokenUnavailable):
        router.token_nowait("judge/demo")  # 아직 발급 전
    await router.prepare("judge/demo")
    await router.prepare("AnTaewoo/foreman_test")
    assert router.token_nowait("Judge/Demo") == "ghs_42"
    assert router.token_nowait("AnTaewoo/foreman_test") == "ghs_7"
    assert router.client_for_installation(42) is router.registry.github(42)


# refresh_all: 보관되지 않은 모든 프로젝트의 토큰을 미리 발급 (control plane 루프)
async def test_refresh_all(
    factory: async_sessionmaker[AsyncSession], gh: respx.MockRouter, pem: str
) -> None:
    await _projects(factory)
    router = RepoRouter(factory, ClientRegistry(_real(pem)))
    await router.refresh_all()
    assert router.token_nowait("judge/demo") == "ghs_42"
    assert router.token_nowait("antaewoo/foreman_test") == "ghs_7"


# 라우팅 client: 첫 인자 repo로 installation client를 골라 그대로 위임
async def test_routing_client_delegates_by_repo() -> None:
    calls: list[tuple[int | None, str, tuple[Any, ...]]] = []

    class Fake:
        def __init__(self, iid: int | None) -> None:
            self.iid = iid

        async def open_pr(self, repo: str, *args: Any) -> str:
            calls.append((self.iid, repo, args))
            return f"pr@{self.iid}"

    fakes = {42: Fake(42), None: Fake(None)}

    async def resolve(repo: str) -> Any:
        return fakes[42 if repo.lower() == "judge/demo" else None]

    client = RoutingGitHubClient(resolve)
    assert await client.open_pr("Judge/Demo", "head", "main") == "pr@42"
    assert await client.open_pr("org/other", "head") == "pr@None"
    assert calls == [(42, "Judge/Demo", ("head", "main")), (None, "org/other", ("head",))]


# DRY_RUN: 라우팅 없이 Dry client 하나, 토큰 경로 없음
async def test_router_dry(factory: async_sessionmaker[AsyncSession]) -> None:
    router = RepoRouter(factory, ClientRegistry(Settings(_env_file=None)))
    assert isinstance(router.github, DryRunGitHubClient)
    assert router.token_getter is None
    await router.refresh_all()  # no-op
