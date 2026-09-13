"""P2 공용: respx가 api.github.com을 전부 가로챈다. 미매칭 요청 = 테스트 실패 (D-10). RSA 키 픽스처."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

GITHUB_API = "https://api.github.com"


@pytest.fixture(autouse=True)
def github_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=GITHUB_API, assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def private_key_pem(rsa_key: rsa.RSAPrivateKey) -> str:
    return rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="session")
def public_key_pem(rsa_key: rsa.RSAPrivateKey) -> str:
    return (
        rsa_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=GITHUB_API) as client:
        yield client


class Clock:
    """주입 가능한 now()."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()
