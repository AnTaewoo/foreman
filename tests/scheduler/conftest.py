"""P1 이벤트 픽스처(aiosqlite + 진짜 Redis) 재사용."""

from tests.events.conftest import engine, factory, redis, session  # noqa: F401  # pytest 픽스처 재노출
