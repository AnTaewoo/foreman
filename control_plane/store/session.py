"""비동기 엔진·세션 팩토리. 설정은 ``Settings.database_url`` 하나로 결정된다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from control_plane.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """``database_url``로 AsyncEngine. sqlite ``:memory:``는 연결을 공유해야 테이블이 보인다."""
    url = settings.database_url
    if url.startswith("sqlite") and url.endswith(":memory:"):
        return create_async_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """정상 종료면 commit, 예외면 rollback 후 재전파."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
