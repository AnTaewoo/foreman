"""통합 테스트 공용 (Docker Postgres/Redis). 없으면 skip (D-17)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.store import session as sess

ROOT = Path(__file__).resolve().parent.parent.parent
PG_URL = os.environ.get("HITL_DATABASE_URL", "postgresql+asyncpg://hitl:hitl@localhost:5432/hitl")

pytestmark = pytest.mark.integration


def _pg_available() -> bool:
    async def probe() -> bool:
        engine = sess.create_engine(Settings(_env_file=None, database_url=PG_URL))
        try:
            async with engine.connect() as conn:
                await asyncio.wait_for(conn.execute(text("select 1")), timeout=3)
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    return asyncio.run(probe())


@pytest.fixture(scope="session")
def pg_url() -> str:
    if not PG_URL.startswith("postgresql"):
        pytest.skip("HITL_DATABASE_URL is not postgres")
    if not _pg_available():
        pytest.skip(f"Postgres not reachable at {PG_URL} (docker compose up -d --wait postgres)")
    return PG_URL


@pytest.fixture(scope="session")
def migrated(pg_url: str) -> str:
    """downgrade base → upgrade head. 세션당 한 번."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return pg_url


@pytest.fixture
async def pg_engine(migrated: str) -> AsyncIterator[AsyncEngine]:
    engine = sess.create_engine(Settings(_env_file=None, database_url=migrated))
    async with engine.begin() as conn:
        # DELETE는 트리거가 막으므로 TRUNCATE (row 트리거 안 탐)
        await conn.execute(
            text(
                "TRUNCATE events, tool_calls, runs, tasks, epics, goals, decisions, agents, "
                "projects CASCADE"
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as s:
        yield s
