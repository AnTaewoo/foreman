"""Alembic async env. URL 우선순위: `-x url=` > alembic.ini sqlalchemy.url > Settings().database_url.

이미 실행 중인 이벤트 루프 안에서 호출되면(pytest-asyncio 등) 별도 스레드에서 새 루프로 돌린다.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from control_plane.config import Settings
from control_plane.store.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if url := x_args.get("url"):
        return str(url)
    if url := config.get_main_option("sqlalchemy.url"):
        return url
    return Settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def _run_in_fresh_loop(coro_factory: Callable[[], object]) -> None:
    """실행 중인 루프가 있으면 스레드에서 새 루프로 격리해 돌린다."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro_factory())  # type: ignore[arg-type]  # coroutine 반환
        return
    error: list[BaseException] = []

    def target() -> None:
        try:
            asyncio.run(coro_factory())  # type: ignore[arg-type]
        except BaseException as exc:  # noqa: BLE001 — 스레드 경계에서 재전파
            error.append(exc)

    t = threading.Thread(target=target, name="alembic-migrations")
    t.start()
    t.join()
    if error:
        raise error[0]


def run_migrations_online() -> None:
    _run_in_fresh_loop(_run_async_migrations)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
