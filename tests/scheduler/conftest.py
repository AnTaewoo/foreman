"""P1 이벤트 픽스처(aiosqlite + 진짜 Redis) 재사용."""

from tests.events.conftest import (  # noqa: F401  # pytest 픽스처 재노출
    engine,
    factory,
    redis,
    session,
)
