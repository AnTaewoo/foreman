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
# 통합 테스트는 대상 DB를 downgrade base → upgrade head 로 **비운다**.
# 개발/데모 DB `hitl`을 절대 쓰지 않도록 기본은 전용 `hitl_test`(없으면 만든다).
# 다른 DB를 쓰려면 FOREMAN_TEST_DATABASE_URL (P9.4)
PG_URL = os.environ.get(
    "FOREMAN_TEST_DATABASE_URL", "postgresql+asyncpg://hitl:hitl@localhost:5432/hitl_test"
)

pytestmark = pytest.mark.integration


def _pg_available() -> bool:
    """대상 DB에 접속되면 True. 없으면 만들어 본다 (compose의 hitl 사용자는 superuser)."""

    async def probe(url: str) -> bool:
        engine = sess.create_engine(Settings(_env_file=None, database_url=url))
        try:
            async with engine.connect() as conn:
                await asyncio.wait_for(conn.execute(text("select 1")), timeout=3)
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    async def create_db() -> bool:
        base, _, name = PG_URL.rpartition("/")
        engine = sess.create_engine(Settings(_env_file=None, database_url=f"{base}/postgres"))
        try:
            async with engine.connect() as conn:
                auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
                await auto.execute(text(f'CREATE DATABASE "{name}"'))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    async def run() -> bool:
        if await probe(PG_URL):
            return True
        return await create_db() and await probe(PG_URL)

    return asyncio.run(run())


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
