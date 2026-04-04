# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Database session management."""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fairyclaw.config.settings import settings


engine_kwargs: dict = {"future": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"timeout": settings.sqlite_busy_timeout_seconds}

engine = create_async_engine(settings.database_url, **engine_kwargs)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_busy_timeout(dbapi_connection, connection_record) -> None:
        """Apply SQLite PRAGMA settings on each new DB connection.

        Args:
            dbapi_connection: Raw DB-API connection object.
            connection_record: SQLAlchemy connection record metadata.

        Returns:
            None
        """
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout = {int(settings.sqlite_busy_timeout_seconds * 1000)}")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield one async DB session for request scope.

    Yields:
        AsyncSession: Active SQLAlchemy async session.
    """
    async with AsyncSessionLocal() as session:
        yield session
