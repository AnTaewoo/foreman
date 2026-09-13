"""P1.3 — Alembic 마이그레이션(aiosqlite) + session.py (ROADMAP §7 P1.3 red a~c)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.store import session as sess
from control_plane.store.models import Base

ROOT = Path(__file__).resolve().parent.parent.parent
EXPECTED_TABLES = {
    "projects", "goals", "epics", "tasks", "runs", "decisions", "agents", "events", "tool_calls"
}  # fmt: skip


def _cfg(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


def _tables(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


# (a) upgrade head → 9 테이블 → downgrade base → 0
def test_upgrade_and_downgrade(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "head")
    assert _tables(db) == EXPECTED_TABLES
    command.downgrade(cfg, "base")
    assert _tables(db) == set()


def test_two_revisions_in_chain(tmp_path: Path) -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_cfg(tmp_path / "x.db"))
    revs = list(script.walk_revisions())
    assert len(revs) == 2
    assert [r.revision for r in reversed(revs)] == ["0001", "0002"]


# (b) 모델과 마이그레이션이 일치
def test_metadata_matches_migrations(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    command.upgrade(_cfg(db), "head")
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


# (c) session.py
def test_create_engine_from_settings(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    engine = sess.create_engine(settings)
    assert isinstance(engine, AsyncEngine)
    assert engine.url.get_backend_name() == "sqlite"


async def test_session_factory_and_get_session(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    engine = sess.create_engine(settings)
    factory = sess.create_session_factory(engine)
    assert isinstance(factory, async_sessionmaker)
    async with sess.get_session(factory) as s:
        assert isinstance(s, AsyncSession)
        assert (await s.execute(text("select 1"))).scalar_one() == 1
    await engine.dispose()


async def test_get_session_rolls_back_on_error(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    engine = sess.create_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sess.create_session_factory(engine)
    from control_plane.store.models import Project

    with pytest.raises(RuntimeError):
        async with sess.get_session(factory) as s:
            s.add(Project(id="P1", name="n", repo_full_name="r"))
            await s.flush()
            raise RuntimeError("boom")
    async with sess.get_session(factory) as s:
        assert await s.get(Project, "P1") is None
    await engine.dispose()


async def test_memory_sqlite_shares_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:")
    engine = sess.create_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sess.create_session_factory(engine)
    async with sess.get_session(factory) as s:
        names = (await s.execute(text("select name from sqlite_master where type='table'"))).all()
    assert len(names) >= 9  # 두 번째 연결에서도 테이블이 보인다 (StaticPool)
    await engine.dispose()
